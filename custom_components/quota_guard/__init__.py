"""Generic, provider-agnostic quota monitoring. Unknown is never safe/zero."""

import asyncio
from datetime import timedelta

import voluptuous as vol
from homeassistant.components import persistent_notification, websocket_api
from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.storage import Store
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from .ai_provider import AUTOMATIC, AIProvider
from .diagnostics import connection_issues, metric_issues, retired_issues
from .engine import crossings, evaluate, evaluate_config, validate_thresholds
from .google_provider import GoogleProvider
from .localization import translate
from .oracle_provider import OracleProvider, stop_instances, validate_targets
from .provider_policies import coverage_for, policy_for
from .provider_signals import collect_signals, subscribe
from .relationships import collect_relationships
from .request_counters import RequestCounters
from .source_config import period_timezone, sensor_sample, usage_url

DOMAIN = "quota_guard"
SCHEMA = vol.Schema(
    {
        vol.Required("id"): vol.Match(r"^[a-z0-9_]{1,60}$"),
        vol.Required("name"): cv.string,
        vol.Required("entry_ids"): [cv.string],
        vol.Required("kind"): vol.In(["free_allowance", "rate_limit", "budget"]),
        vol.Required("source"): cv.string,
        vol.Required("unit"): cv.string,
        vol.Optional("usage_url"): usage_url,
        vol.Optional("usage_entity"): cv.entity_id,
        vol.Optional("usage_attribute"): cv.string,
        vol.Optional("limit_entity"): cv.entity_id,
        vol.Optional("period_entity"): cv.entity_id,
        vol.Optional("timestamp_entity"): cv.entity_id,
        vol.Optional("period_mode", default="attribute"): vol.In(
            ["attribute", "monthly", "daily"]
        ),
        vol.Optional("timestamp_mode", default="attribute"): vol.In(
            ["attribute", "state_reported"]
        ),
        vol.Optional("period_timezone", default="UTC"): period_timezone,
        vol.Optional("limit"): vol.All(vol.Coerce(float), vol.Range(min=0.000001)),
        vol.Optional("period_attribute", default="period"): cv.string,
        vol.Optional("timestamp_attribute", default="sampled_at"): cv.string,
        vol.Optional("max_age", default=900): vol.All(
            vol.Coerce(int), vol.Range(min=30, max=86400)
        ),
        vol.Optional("enforce", default=False): cv.boolean,
        vol.Optional("warning_percent", default=90): vol.All(
            vol.Coerce(float), vol.Range(min=0.01, max=100)
        ),
        vol.Optional("cutoff_percent", default=95): vol.All(
            vol.Coerce(float), vol.Range(min=0.01, max=100)
        ),
        vol.Optional("oracle_instance_ids", default=[]): [cv.string],
        vol.Optional("threshold_mode", default="percent"): vol.In(
            ["percent", "above", "below"]
        ),
        vol.Optional("warning_value"): vol.Coerce(float),
        vol.Optional("critical_value"): vol.Coerce(float),
    }
)


async def async_setup(hass, config):
    """Register the bundled card and administrator-only actions."""
    from homeassistant.exceptions import HomeAssistantError

    from .frontend import async_register_frontend

    await async_register_frontend(hass)

    async def add_dashboard_view(call):
        from .dashboard_setup import add_view

        try:
            await add_view(hass, call.data["dashboard_path"])
        except ValueError as error:
            raise HomeAssistantError(str(error)) from error

    async_register_admin_service(
        hass,
        DOMAIN,
        "add_dashboard_view",
        add_dashboard_view,
        schema=vol.Schema({vol.Required("dashboard_path"): cv.string}),
    )

    async def dispatch(call):
        manager = hass.data.get(DOMAIN)
        if manager is None:
            raise HomeAssistantError("Cloud Quota Guard is not loaded")
        await getattr(manager, call.service)(call)

    async_register_admin_service(hass, DOMAIN, "configure", dispatch, schema=SCHEMA)
    async_register_admin_service(
        hass,
        DOMAIN,
        "acknowledge_retired",
        dispatch,
        schema=vol.Schema({vol.Required("id"): cv.string}),
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        "report",
        dispatch,
        schema=vol.Schema(
            {
                vol.Required("id"): cv.string,
                vol.Required("used"): vol.Coerce(float),
                vol.Optional("limit"): vol.Coerce(float),
                vol.Required("period"): cv.string,
                vol.Required("timestamp"): cv.string,
                vol.Optional("expires_at"): cv.string,
            }
        ),
    )
    websocket_api.async_register_command(hass, ws_report)
    return True


async def async_setup_entry(hass, entry):
    manager = Guard(hass, entry)
    hass.data[DOMAIN] = manager
    await manager.load()
    manager.request_counters = RequestCounters(hass, manager.data)
    await manager.refresh_request_counters()
    entry.async_on_unload(manager.request_counters.close)

    async def flush_counters(_event):
        manager.request_counters.close()
        async with manager.lock:
            await manager.store.async_save(manager.data)

    entry.async_on_unload(
        hass.bus.async_listen_once("homeassistant_stop", flush_counters)
    )
    entry.async_on_unload(
        async_track_time_interval(
            hass, manager.refresh_request_counters, timedelta(seconds=10)
        )
    )
    entry.async_on_unload(subscribe(hass, manager))
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    entry.async_on_unload(
        async_track_time_interval(hass, manager.tick, timedelta(seconds=60))
    )
    await manager.tick()
    return True


async def async_unload_entry(hass, entry):
    ok = await hass.config_entries.async_unload_platforms(entry, ["sensor"])
    if ok:
        await hass.data[DOMAIN].store.async_save(hass.data[DOMAIN].data)
        hass.data.pop(DOMAIN, None)
    return ok


@websocket_api.websocket_command({vol.Required("type"): "quota_guard/report"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_report(hass, connection, msg):
    guard = hass.data.get(DOMAIN)
    if not guard:
        connection.send_error(msg["id"], "not_loaded", "Quota Guard is not loaded")
        return
    connection.send_result(msg["id"], guard.snapshot())


class Guard:
    def __init__(self, hass, entry=None):
        self.hass = hass
        self.entry = entry
        self.store = Store(hass, 1, DOMAIN)
        self.data = {"metrics": {}, "events": []}
        self.inventory = []
        self.listeners = []
        self.lock = asyncio.Lock()
        self.poll_lock = asyncio.Lock()
        self.google = GoogleProvider()
        self.ai = AIProvider()
        self.request_counters = None
        self.relationships = {"available": False, "entries": {}}

    async def load(self):
        self.data = await self.store.async_load() or self.data
        if self.data.get("permissions_version") != 1:
            needs_setup = False
            # Old releases inferred targets and defaulted configure to enforcement.
            # No stored legacy flag establishes explicit administrator consent.
            for id, metric in list(self.data["metrics"].items()):
                cfg = metric["config"]
                needs_setup |= bool(cfg.get("enforce"))
                cfg["enforce"] = False
                metric["cutoff_authorized"] = False
                if cfg.get("builtin_name") or (
                    id.startswith("github_")
                    and cfg.get("source") == "https://api.github.com/rate_limit"
                ):
                    needs_setup = True
                    self.retire_metric(id)
            self.data["permissions_version"] = 1
            await self.store.async_save(self.data)
            if needs_setup:
                persistent_notification.async_create(
                    self.hass,
                    self.text(
                        "Quota Guard updated: add your own GitHub/Google credentials in Configure. The Tuya observer was removed. History is retained; all legacy cutoffs are off until you explicitly map targets and enable cutoff again. Already disabled integrations remain disabled."
                    ),
                    title=self.text("Cloud service quotas"),
                    notification_id="quota_guard_permissions_upgrade",
                )

    @property
    def providers(self):
        """Credentials belong only to this integration's config entry."""
        return self.entry.data.get("providers", {}) if self.entry else {}

    def retire_metric(self, id):
        """Keep history without evaluating a disconnected or removed source."""
        metric = self.data["metrics"].pop(id)
        metric["config"]["enforce"] = False
        metric["config"]["remote_enforce"] = False
        self.data.get("remote_controls", {}).pop(id, None)
        metric["cutoff_authorized"] = False
        self.clear_data_alert(id, metric)
        for kind in ("threshold", "cutoff"):
            persistent_notification.async_dismiss(self.hass, f"quota_guard_{id}_{kind}")
        self.data.setdefault("retired_metrics", {})[id] = metric

    async def remove_provider(self, provider_id):
        # Wait for any in-flight request before removing its metrics/credentials.
        async with self.poll_lock, self.lock:
            providers = dict(self.providers)
            providers.pop(provider_id, None)
            self.hass.config_entries.async_update_entry(
                self.entry, data={**self.entry.data, "providers": providers}
            )
            for id, metric in list(self.data["metrics"].items()):
                if metric["config"].get("provider_id") == provider_id:
                    self.retire_metric(id)
            await self.store.async_save(self.data)
            for listener in tuple(self.listeners):
                listener()

    def snapshot(self):
        return {
            **self.data,
            "metrics": {
                id: {**m, "diagnostics": self.metric_diagnostics(m)}
                for id, m in self.data["metrics"].items()
            },
            "retired_metrics": {
                id: {**m, "diagnostics": retired_issues(m)}
                for id, m in self.data.get("retired_metrics", {}).items()
            },
            "inventory": [
                {**row, "diagnostics": connection_issues(row)} for row in self.inventory
            ],
            "relationships": self.relationships,
            "frontend": {
                key: value
                for key, value in self.hass.data.get(
                    "quota_guard_frontend_registered", {}
                ).items()
                if key in ("version", "url")
            }
            if isinstance(
                getattr(self.hass, "data", {}).get("quota_guard_frontend_registered"),
                dict,
            )
            else {},
            "updated_at": dt_util.utcnow().isoformat(),
        }

    def metric_diagnostics(self, metric):
        entries = {}
        for id in metric["config"].get("entry_ids", []):
            entry = self.hass.config_entries.async_get_entry(id)
            if entry:
                entries[id] = entry.domain
        return metric_issues(
            metric, getattr(self.hass, "states", {}), entries, dt_util.utcnow()
        )

    async def acknowledge_retired(self, call):
        """Acknowledge a stopped source; retain its history and never hide active errors."""
        async with self.lock:
            metric = self.data.get("retired_metrics", {}).get(call.data["id"])
            if metric is None:
                raise vol.Invalid("Only retired sources can be acknowledged")
            metric["acknowledged"] = True
            await self.store.async_save(self.data)

    async def configure(self, call, *, initial_sample=None):
        cfg = dict(call.data)
        try:
            validate_thresholds(cfg)
        except (ValueError, TypeError, KeyError) as error:
            raise vol.Invalid(
                "Set valid warning and critical values in the selected unit"
            ) from error
        current = self.data["metrics"].get(cfg["id"], {})
        provider_id = current.get("config", {}).get("provider_id")
        provider = self.providers.get(provider_id, {})
        oracle = provider.get("type") == "oracle"
        ai = provider.get("type") in AUTOMATIC
        if provider.get("type") == "deepseek" and (
            cfg.get("limit") is not None
            or (cfg.get("enforce") and cfg.get("threshold_mode") != "below")
        ):
            raise vol.Invalid(
                "Credit balances need remaining-balance thresholds, not a usage percentage"
            )
        targets = cfg.get("oracle_instance_ids", [])
        if targets and not oracle:
            raise vol.Invalid("Oracle targets require an Oracle API source")
        if oracle and cfg["entry_ids"]:
            raise vol.Invalid("Oracle quotas use compute targets, not HA integrations")
        if cfg.get("enforce") and oracle and (not targets or not cfg.get("limit")):
            raise vol.Invalid(
                "Power-off requires a verified allowance and explicit instances"
            )
        if cfg.get("enforce", False) and not (cfg["entry_ids"] or (oracle and targets)):
            raise vol.Invalid("At least one target integration is required")
        verified_targets = []
        if targets:
            try:
                verified_targets = await self.hass.async_add_executor_job(
                    validate_targets, provider["credentials"], targets
                )
            except Exception as error:
                raise vol.Invalid("Oracle instance validation failed") from error
        for id in cfg["entry_ids"]:
            e = self.hass.config_entries.async_get_entry(id)
            if not e or e.domain == DOMAIN:
                raise vol.Invalid("Invalid target integration")
        async with self.lock:
            old = self.data["metrics"].get(cfg["id"], {})
            if initial_sample is not None:
                if old or provider_id or cfg.get("usage_entity"):
                    raise vol.Invalid("Initial reports require a new manual source")
                initial_sample = {**initial_sample, "limit": cfg.get("limit")}
                if evaluate(initial_sample, dt_util.utcnow(), cfg["max_age"])[
                    "status"
                ] not in ("ok", "warning", "cutoff", "usage_only"):
                    raise vol.Invalid("Invalid initial reading")
            old_config = old.get("config", {})
            # A changed source must supply a new sample before it can enforce a cutoff.
            measurement_fields = (
                "source",
                "unit",
                "kind",
                "usage_entity",
                "usage_attribute",
                "limit",
                "limit_entity",
                "period_attribute",
                "period_entity",
                "timestamp_attribute",
                "timestamp_mode",
                "timestamp_entity",
                "period_mode",
                "period_timezone",
            )
            if old_config.get("provider_id") or old_config.get("automatic_counter"):
                # Provider measurements are immutable; only explicit target mapping
                # and cutoff consent can be changed through the admin action.
                normalized = SCHEMA(
                    {
                        str(k): old_config[str(k)]
                        for k in SCHEMA.schema
                        if str(k) in old_config
                    }
                )
                if any(
                    cfg.get(k) != normalized.get(k)
                    for k in measurement_fields
                    if not (
                        (oracle or ai or old_config.get("automatic_counter"))
                        and k == "limit"
                    )
                ):
                    raise vol.Invalid("Provider measurements cannot be overwritten")
                cfg = {**old_config, **cfg}
                if (
                    old_config.get("automatic_counter")
                    and cfg["entry_ids"] != old_config["entry_ids"]
                ):
                    raise vol.Invalid(
                        "A request counter belongs to its observed integration"
                    )
            if not old_config.get("provider_id") and any(
                old.get("config", {}).get(k) != cfg.get(k) for k in measurement_fields
            ):
                old = {
                    k: v
                    for k, v in old.items()
                    if k
                    in ("points", "notified", "period", "last_percent", "cutoff_result")
                }
            self.data["metrics"][cfg["id"]] = {
                **old,
                "config": cfg,
                "cutoff_authorized": bool(
                    cfg.get("enforce") and (cfg["entry_ids"] or (oracle and targets))
                ),
                "status": "unknown",
            }
            if initial_sample is not None:
                self.data["metrics"][cfg["id"]]["sample"] = initial_sample
            if ai and old.get("sample"):
                self.data["metrics"][cfg["id"]]["sample"] = {
                    **old["sample"],
                    "limit": cfg.get("limit"),
                }
            if oracle:
                self.data["metrics"][cfg["id"]]["oracle_targets"] = verified_targets
                if (
                    not cfg.get("enforce")
                    or old_config.get("oracle_instance_ids") != targets
                ):
                    self.data["metrics"][cfg["id"]].pop("cutoff_result", None)
                if old.get("sample"):
                    self.data["metrics"][cfg["id"]]["sample"] = {
                        **old["sample"],
                        "limit": cfg.get("limit"),
                    }
            if any(
                old_config.get(k) != cfg.get(k)
                for k in (
                    "warning_percent",
                    "cutoff_percent",
                    "limit",
                    "threshold_mode",
                    "warning_value",
                    "critical_value",
                )
            ):
                self.data["metrics"][cfg["id"]].update(last_percent=None, notified=[])
            await self.store.async_save(self.data)
        await self.tick(refresh_providers=False)

    async def report(self, call):
        async with self.lock:
            metric = self.data["metrics"].get(call.data["id"])
            if metric is None:
                raise vol.Invalid("Configure the metric first")
            if metric["config"].get("provider_id") or metric["config"].get(
                "automatic_counter"
            ):
                raise vol.Invalid(
                    "Provider-managed metrics cannot accept manual reports"
                )
            sample = {k: v for k, v in call.data.items() if k != "id"}
            if "limit" not in sample and metric["config"].get("limit") is not None:
                sample["limit"] = metric["config"]["limit"]
            if evaluate(sample, dt_util.utcnow(), metric["config"]["max_age"])[
                "status"
            ] not in ("ok", "warning", "cutoff", "usage_only"):
                raise vol.Invalid("Invalid/stale sample, period or timestamp")
            previous = metric.get("sample", {})
            if previous.get("timestamp") and dt_util.parse_datetime(
                sample["timestamp"]
            ) < dt_util.parse_datetime(previous["timestamp"]):
                raise vol.Invalid("Out-of-order sample rejected")
            metric["sample"] = sample
            await self.store.async_save(self.data)
        await self.tick(refresh_providers=False)

    async def inventory_refresh(self):
        rows = []
        for e in self.hass.config_entries.async_entries():
            if e.domain == DOMAIN or e.source == "ignore":
                continue
            try:
                integration = await async_get_integration(self.hass, e.domain)
                io = integration.manifest.get("iot_class", "unknown")
            except Exception:
                io = "unknown"
            ids = [
                id
                for id, m in self.data["metrics"].items()
                if e.entry_id in m["config"]["entry_ids"]
            ]
            rows.append(
                {
                    "entry_id": e.entry_id,
                    "domain": e.domain,
                    "title": e.title,
                    "io_class": io,
                    "state": e.state.value,
                    "disabled": bool(e.disabled_by),
                    "metrics": ids,
                    "policy": policy_for(e.domain, e.data, e.options),
                    "coverage": coverage_for(e.domain, io, ids),
                }
            )
        self.inventory = rows
        represented = {r["domain"] for r in rows}
        for domain in sorted(
            {c.split(".")[0] for c in self.hass.config.components}
            - represented
            - {DOMAIN}
        ):
            try:
                integration = await async_get_integration(self.hass, domain)
                io = integration.manifest.get("iot_class", "unknown")
            except Exception:
                continue
            if io.startswith("cloud"):
                rows.append(
                    {
                        "entry_id": None,
                        "domain": domain,
                        "title": integration.name + " (YAML)",
                        "io_class": io,
                        "state": "loaded",
                        "disabled": False,
                        "metrics": [],
                        "coverage": coverage_for(domain, io, []),
                    }
                )

    async def tick(self, *_, refresh_providers=True):
        """Keep slow provider requests out of the configuration/storage lock."""
        if refresh_providers:
            # Skip overlapping scheduled polls instead of building a request queue.
            if self.poll_lock.locked():
                return
            async with self.poll_lock:
                await self.github_samples(dt_util.utcnow())
                await self.google_samples()
                await self.oracle_samples()
                await self.ai_samples()
                await self.evaluate_metrics()
        else:
            await self.evaluate_metrics()

    async def refresh_request_counters(self, *_):
        if self.request_counters:
            self.request_counters.refresh(self.hass.config_entries.async_entries())

    async def evaluate_metrics(self):
        """Evaluate locally available samples without querying cloud providers."""
        async with self.lock:
            await self.refresh_request_counters()
            if self.request_counters:
                current_entries = {
                    e.entry_id for e in self.hass.config_entries.async_entries()
                }
                for id, metric in list(self.data["metrics"].items()):
                    if (
                        metric["config"].get("automatic_counter")
                        and not set(metric["config"]["entry_ids"]) & current_entries
                    ):
                        self.retire_metric(id)
                for row in self.request_counters.rows():
                    metric = self.data["metrics"].setdefault(row["config"]["id"], row)
                    config = metric["config"]
                    if "sample" not in row:
                        metric.pop("sample", None)
                    metric.update(row)
                    metric["config"] = config
                    if "sample" in metric and config.get("limit"):
                        metric["sample"]["limit"] = config["limit"]
            await self.inventory_refresh()
            await self.provider_signals_refresh()
            try:
                self.relationships = collect_relationships(self.hass)
            except ImportError:
                self.relationships = {"available": False, "entries": {}}
            now = dt_util.utcnow()
            for id, m in self.data["metrics"].items():
                cfg = m["config"]
                if cfg.get("usage_entity"):
                    m["sample"] = sensor_sample(cfg, self.hass.states, now)
                sample = m.get("sample", {})
                decision = evaluate_config(sample, now, cfg)
                old_status = m.get("status")
                m.update(decision)
                if decision["status"] == "usage_only":
                    m.pop("limit", None)
                    m.pop("remaining", None)
                    self.clear_data_alert(id, m)
                    points = m.setdefault("points", [])
                    if not points or points[-1]["source_at"] != sample["timestamp"]:
                        points.append(
                            {
                                "at": now.isoformat(),
                                "source_at": sample["timestamp"],
                                "used": decision["used"],
                                "period": sample["period"],
                                "segment": m.get("observation", {}).get("started_at"),
                            }
                        )
                        m["points"] = points[-3000:]
                    continue
                if decision["status"] in ("unknown", "stale"):
                    for field in ("used", "limit", "remaining"):
                        m.pop(field, None)
                    if (
                        cfg.get("automatic_counter")
                        and m.get("counter", {}).get("state") != "observing"
                    ):
                        m["status"] = "counter_unavailable"
                        self.clear_data_alert(id, m)
                        continue
                    reporting = m.get("reporting") or {}
                    checked_at = (
                        dt_util.parse_datetime(reporting["checked_at"])
                        if reporting.get("checked_at")
                        else None
                    )
                    if (
                        reporting.get("state") == "no_activity_reported"
                        and checked_at is not None
                        and 0
                        <= (dt_util.utcnow() - checked_at).total_seconds()
                        <= cfg["max_age"]
                    ):
                        # A successful empty query is a reporting observation, not a
                        # verified zero-use sample. Never enforce or graph it as zero.
                        m["status"] = "no_activity_reported"
                        self.clear_data_alert(id, m)
                        continue
                    if self.provider_alerts_paused(cfg):
                        self.clear_data_alert(id, m)
                        continue
                    message_key = "{name}: measurement is missing or stale; protection cannot be verified"
                    if (
                        m.get("provider_error")
                        == "Google has not reported any usage samples for this day; not assumed zero"
                    ):
                        message_key = "{name}: Google has not published usage samples for the current quota day. Waiting for provider data; do not assume zero usage."
                    # Persistent notifications may be forwarded to Telegram. Do not
                    # recreate unchanged messages on each poll or after a restart.
                    message = self.text(message_key, name=self.metric_name(cfg))
                    # Stable semantic identity prevents language changes and restarts
                    # from re-sending an already active alert. Migrate the old latch.
                    previous_key = m.get("data_alert_key")
                    already_alerted = previous_key == message_key or (
                        previous_key is None and bool(m.get("data_alert_message"))
                    )
                    m["data_alert_key"] = message_key
                    if not already_alerted:
                        m["data_alert_message"] = message
                        if old_status not in (None, "unknown", "stale"):
                            await self.event(id, "data_unavailable", None, message)
                        else:
                            persistent_notification.async_create(
                                self.hass,
                                message,
                                title=self.text("Cloud service quotas"),
                                notification_id=f"quota_guard_{id}_data_unavailable",
                            )
                    continue
                self.clear_data_alert(id, m)
                if "limit" not in decision:
                    m.pop("limit", None)
                    m.pop("remaining", None)
                period = sample["period"]
                if m.get("period") != period:
                    m["period"] = period
                    m["last_percent"] = None
                    m["notified"] = []
                pct = decision["percent"]
                points = m.setdefault("points", [])
                if not points or points[-1]["source_at"] != sample["timestamp"]:
                    points.append(
                        {
                            "at": now.isoformat(),
                            "source_at": sample["timestamp"],
                            "percent": pct,
                            "used": decision["used"],
                            "period": period,
                        }
                    )
                    m["points"] = points[-3000:]
                absolute = cfg.get("threshold_mode", "percent") != "percent"
                levels = (
                    (
                        ["warning", "critical"]
                        if decision["status"] == "cutoff"
                        else ["warning"]
                        if decision["status"] == "warning"
                        else []
                    )
                    if absolute
                    else crossings(
                        m.get("last_percent"),
                        pct,
                        cfg.get("warning_percent", 90),
                        cfg.get("cutoff_percent", 95),
                    )
                )
                for threshold in levels:
                    if threshold not in m.setdefault("notified", []):
                        m["notified"].append(threshold)
                        await self.event(
                            id,
                            "threshold",
                            threshold,
                            (
                                f"{self.metric_name(cfg)}: {threshold} — measured {decision['used']:g} {cfg.get('unit', '')}; threshold {cfg['critical_value'] if threshold == 'critical' else cfg['warning_value']:g} {cfg.get('unit', '')}."
                                if absolute
                                else self.text(
                                    "{name}: detected usage {percent:.2f}% — threshold {threshold}%",
                                    name=self.metric_name(cfg),
                                    percent=pct,
                                    threshold=threshold,
                                )
                            ),
                        )
                m["last_percent"] = pct
                if (
                    decision["status"] == "cutoff"
                    and cfg.get("enforce")
                    and m.get("cutoff_authorized")
                    and not any(
                        issue["severity"] == "error"
                        for issue in self.metric_diagnostics(m)
                    )
                ):
                    result = {}
                    provider = self.providers.get(cfg.get("provider_id"), {})
                    if provider.get("type") == "oracle" and cfg.get(
                        "oracle_instance_ids"
                    ):
                        try:
                            result = await self.hass.async_add_executor_job(
                                stop_instances,
                                provider["credentials"],
                                cfg["oracle_instance_ids"],
                                m.get("cutoff_result", {}),
                            )
                        except Exception:
                            result = {
                                id: "power_off_failed"
                                for id in cfg["oracle_instance_ids"]
                            }
                    for eid in cfg["entry_ids"]:
                        e = self.hass.config_entries.async_get_entry(eid)
                        if not e or e.domain == DOMAIN:
                            result[eid] = "missing"
                            continue
                        if e.disabled_by and e.state.value == "not_loaded":
                            result[eid] = "disabled"
                            continue
                        try:
                            ok = await self.hass.config_entries.async_set_disabled_by(
                                eid, ConfigEntryDisabler.USER
                            )
                            result[eid] = (
                                "disabled"
                                if ok and e.state.value == "not_loaded"
                                else "restart_required"
                            )
                        except Exception:
                            result[eid] = "failed"
                    if result != m.get("cutoff_result"):
                        m["cutoff_result"] = result
                        await self.event(
                            id,
                            "cutoff",
                            cfg.get("cutoff_percent", 95),
                            self.text("Cutoff: ") + ", ".join(result.values()),
                        )
                control = self.data.get("remote_controls", {}).get(id, {})
                if (
                    decision["status"] == "cutoff"
                    and cfg.get("remote_enforce")
                    and control.get("enabled")
                    and control.get("targets")
                    and control.get("attempts", 0) < 3
                    and not any(
                        issue["severity"] == "error"
                        for issue in self.metric_diagnostics(m)
                    )
                ):
                    checked_at = (
                        dt_util.parse_datetime(control["checked_at"])
                        if control.get("checked_at")
                        else None
                    )
                    if checked_at is None or (now - checked_at).total_seconds() >= 900:
                        from .provider_controls import DONE, stop

                        previous = control.get("result", {})
                        if not all(
                            previous.get(target) in DONE
                            for target in control["targets"]
                        ):
                            control["attempts"] = control.get("attempts", 0) + 1
                            control["checked_at"] = now.isoformat()
                            # Persist intent before mutation; retries are bounded across restarts.
                            await self.store.async_save(self.data)
                            try:
                                result = await self.hass.async_add_executor_job(
                                    stop,
                                    self.providers[cfg["provider_id"]],
                                    control["targets"],
                                    previous,
                                    control.get("service"),
                                )
                            except Exception:
                                result = {
                                    target: previous.get(
                                        target, "failed_or_unconfirmed"
                                    )
                                    for target in control["targets"]
                                }
                            control["result"] = result
                            m["remote_cutoff_result"] = result
                            await self.event(
                                id,
                                "cutoff",
                                None,
                                "Provider cutoff: " + ", ".join(result.values()),
                            )
            await self.store.async_save(self.data)
            for listener in self.listeners:
                listener()

    def clear_data_alert(self, id, metric):
        """Clear the persisted latch only on recovery; also dismiss pre-upgrade alerts."""
        persistent_notification.async_dismiss(
            self.hass, f"quota_guard_{id}_data_unavailable"
        )
        metric.pop("data_alert_message", None)
        metric.pop("data_alert_key", None)

    async def apply_provider_row(self, row):
        """A poll updates observations, never administrator cutoff choices."""
        async with self.lock:
            id = row["config"]["id"]
            metric = self.data["metrics"].setdefault(id, {})
            config = metric.get("config", row["config"])
            if "sample" not in row:
                metric.pop("sample", None)
            metric.update(row)
            metric["config"] = config
            if row.get("provider_kind") in {"oracle", *AUTOMATIC} and "sample" in row:
                metric["sample"]["limit"] = config.get("limit")

    async def ai_samples(self):
        for id, provider in self.providers.items():
            if provider.get("type") not in AUTOMATIC:
                continue
            rows = await self.hass.async_add_executor_job(
                self.ai.fetch, {"id": id, **provider}
            )
            if id in self.providers:
                for row in rows:
                    await self.apply_provider_row(row)

    async def github_samples(self, now):
        """Read core usage only with a token supplied to Quota Guard itself."""
        for provider_id, provider in self.providers.items():
            if provider["type"] != "github":
                continue
            id = "qg_github_" + provider_id
            row = {
                "config": {
                    "id": id,
                    "name": "GitHub REST — " + provider["name"],
                    "provider_id": provider_id,
                    "entry_ids": [],
                    "kind": "rate_limit",
                    "source": "https://api.github.com/rate_limit",
                    "unit": "requests",
                    "max_age": 180,
                    "enforce": False,
                },
                "status": "unknown",
            }
            try:
                async with async_get_clientsession(self.hass).get(
                    "https://api.github.com/rate_limit",
                    headers={
                        "Authorization": "Bearer " + provider["access_token"],
                        "Accept": "application/vnd.github+json",
                    },
                    timeout=15,
                ) as response:
                    response.raise_for_status()
                    data = (await response.json())["resources"]["core"]
                    limit = response.headers.get("X-RateLimit-Limit", data["limit"])
                    used = response.headers.get("X-RateLimit-Used", data["used"])
                    reset = response.headers.get("X-RateLimit-Reset", data["reset"])
                row["sample"] = {
                    "used": float(used),
                    "limit": float(limit),
                    "period": str(reset),
                    "timestamp": now.isoformat(),
                }
                row["provider_error"] = None
                row["provider_error_code"] = None
                row["provider_http_status"] = None
            except Exception as error:
                row["provider_error"] = (
                    "Provider unavailable; check Quota Guard credentials"
                )
                status = getattr(error, "status", None)
                row["provider_http_status"] = status
                row["provider_error_code"] = {
                    401: "authentication_failed",
                    403: "permission_denied",
                    429: "rate_limited",
                }.get(status, "provider_unavailable")
            await self.apply_provider_row(row)

    async def google_samples(self):
        accounts = [
            {"id": id, **provider}
            for id, provider in self.providers.items()
            if provider["type"] == "google"
        ]
        if not accounts:
            return
        try:
            results = await self.hass.async_add_executor_job(
                self.google.fetch, accounts
            )
        except Exception:
            # A malformed saved credential must not prevent local source evaluation.
            return
        for row in results:
            await self.apply_provider_row(row)

    async def oracle_samples(self):
        if not hasattr(self, "oracle"):
            self.oracle = OracleProvider()
        for id, provider in tuple(self.providers.items()):
            if provider.get("type") != "oracle":
                continue
            rows = await self.hass.async_add_executor_job(
                self.oracle.fetch, {"id": id, **provider}
            )
            if id not in self.providers:
                continue
            for row in rows or []:
                await self.apply_provider_row(row)

    async def replace_oracle_meters(self, provider_id, meters):
        async with self.poll_lock, self.lock:
            provider = self.providers.get(provider_id)
            if not provider or provider.get("type") != "oracle":
                raise vol.Invalid("Oracle connection no longer exists")
            providers = {**self.providers, provider_id: {**provider, "meters": meters}}
            self.hass.config_entries.async_update_entry(
                self.entry, data={**self.entry.data, "providers": providers}
            )
            desired = {"qg_oracle_" + provider_id + "_" + m["id"] for m in meters}
            for id, metric in list(self.data["metrics"].items()):
                if (
                    metric["config"].get("provider_id") == provider_id
                    and id not in desired
                ):
                    self.retire_metric(id)
            if hasattr(self, "oracle"):
                self.oracle.checked.pop(provider_id, None)
            await self.store.async_save(self.data)
            for listener in tuple(self.listeners):
                listener()

    async def provider_signals_refresh(self):
        """Notify once per active provider failure, separately from numeric quotas."""
        previous = self.data.get("provider_signals", {})
        try:
            current = collect_signals(self.hass, previous)
        except ImportError:
            return
        self.data["provider_signals"] = current
        for row in self.inventory:
            row.pop("provider_signal", None)
            signal = next(
                (
                    item
                    for item in current.values()
                    if item["entry_id"] == row.get("entry_id")
                ),
                None,
            )
            if signal:
                row["provider_signal"] = signal
        if current == previous:
            return
        # An integration reload can briefly clear and recreate the same repair.
        # Reserve the closing notice per account and quota month before forwarding it.
        windows = self.data.setdefault("provider_notice_windows", {})
        notices = []
        for signal in current.values():
            account = signal["entry_id"]
            reset = signal["expected_reset_date"]
            if signal["confirmed"] and windows.get(account) != reset:
                windows[account] = reset
                notices.append(signal)
        await self.store.async_save(self.data)
        for signal in notices:
            await self.event(
                "provider_" + signal["entry_id"],
                "provider_quota_exhausted",
                None,
                self.text(
                    signal["message_key"],
                    name=signal["name"],
                    reset_date=signal["expected_reset_date"],
                ),
            )
        for key in previous.keys() - current.keys():
            persistent_notification.async_dismiss(
                self.hass,
                "quota_guard_provider_"
                + previous[key]["entry_id"]
                + "_provider_quota_exhausted",
            )

    def provider_alerts_paused(self, config):
        targets = set(config.get("entry_ids", []))
        paused = {
            s["entry_id"]
            for s in self.data.get("provider_signals", {}).values()
            if s.get("alerts_paused")
        }
        return bool(targets) and targets <= paused

    def text(self, message, **values):
        """Use the Home Assistant system language; default to English."""
        language = getattr(getattr(self.hass, "config", None), "language", "en")
        return translate(message, language, **values)

    def metric_name(self, config):
        name = config["name"]
        return self.text(name) if config.get("builtin_name") else name

    async def event(self, id, kind, threshold, message):
        config = self.data["metrics"].get(id, {}).get("config", {})
        if kind == "threshold" and not (
            config.get("enforce") or config.get("remote_enforce")
        ):
            message += " Monitoring only — automatic cutoff is off."
        sample = self.data["metrics"].get(id, {}).get("sample", {})
        item = {
            "id": id,
            "kind": kind,
            "name": self.data["metrics"].get(id, {}).get("config", {}).get("name")
            or next(
                (
                    s["name"]
                    for s in self.data.get("provider_signals", {}).values()
                    if "provider_" + s["entry_id"] == id
                ),
                id,
            ),
            "threshold": threshold,
            "detected_at": dt_util.utcnow().isoformat(),
            "source_at": sample.get("timestamp"),
            "period": sample.get("period"),
            "message": message,
        }
        self.data["events"].append(item)
        self.data["events"] = self.data["events"][-1000:]
        # Persist the threshold latch before sending notifications or changing targets.
        await self.store.async_save(self.data)
        config = self.data["metrics"].get(id, {}).get("config", {})
        if kind not in (
            "provider_quota_exhausted",
            "threshold",
            "cutoff",
        ) and self.provider_alerts_paused(config):
            return
        self.hass.bus.async_fire("quota_guard_event", item)
        persistent_notification.async_create(
            self.hass,
            message,
            title=self.text("Cloud service quotas"),
            notification_id=f"quota_guard_{id}_{kind}",
        )
        channels = (
            self.entry.data.get("notification_channels", []) if self.entry else []
        )
        if channels and kind in ("threshold", "cutoff", "provider_quota_exhausted"):
            from .notifications import deliver

            level = (
                "cutoff"
                if kind == "cutoff"
                else "critical"
                if kind == "provider_quota_exhausted"
                or threshold == "critical"
                or isinstance(threshold, (int, float))
                and threshold >= config.get("cutoff_percent", 95)
                else "warning"
            )
            failures = await deliver(
                self.hass, channels, level, "Cloud Quota Guard — " + level, message
            )
            self.data["notification_failures"] = failures
            await self.store.async_save(self.data)
            if failures:
                persistent_notification.async_create(
                    self.hass,
                    "A configured Quota Guard notification channel failed. The alert remains here. Check Notification channels in Quota Guard settings.",
                    title="Quota Guard notification delivery",
                    notification_id="quota_guard_notification_delivery",
                )
