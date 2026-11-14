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

from .diagnostics import connection_issues, metric_issues, retired_issues
from .engine import crossings, evaluate
from .google_provider import GoogleProvider
from .localization import translate
from .oracle_provider import OracleProvider, stop_instances, validate_targets
from .provider_policies import policy_for
from .provider_signals import collect_signals, subscribe
from .relationships import collect_relationships
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
            ["attribute", "monthly"]
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
    }
)


async def async_setup(hass, config):
    """Register the bundled card and administrator-only actions."""
    from homeassistant.exceptions import HomeAssistantError

    from .frontend import async_register_frontend

    await async_register_frontend(hass)

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
            }
        ),
    )
    websocket_api.async_register_command(hass, ws_report)
    return True


async def async_setup_entry(hass, entry):
    manager = Guard(hass, entry)
    hass.data[DOMAIN] = manager
    await manager.load()
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

    async def configure(self, call):
        cfg = dict(call.data)
        if (
            not 0
            < cfg.get("warning_percent", 90)
            < cfg.get("cutoff_percent", 95)
            <= 100
        ):
            raise vol.Invalid("Warning must be below cutoff, from 0 to 100 percent")
        current = self.data["metrics"].get(cfg["id"], {})
        provider_id = current.get("config", {}).get("provider_id")
        provider = self.providers.get(provider_id, {})
        oracle = provider.get("type") == "oracle"
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
                "timestamp_entity",
                "period_mode",
                "period_timezone",
            )
            if old_config.get("provider_id"):
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
                    if not (oracle and k == "limit")
                ):
                    raise vol.Invalid("Provider measurements cannot be overwritten")
                cfg = {**old_config, **cfg}
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
                for k in ("warning_percent", "cutoff_percent", "limit")
            ):
                self.data["metrics"][cfg["id"]].update(last_percent=None, notified=[])
            await self.store.async_save(self.data)
        await self.tick(refresh_providers=False)

    async def report(self, call):
        async with self.lock:
            metric = self.data["metrics"].get(call.data["id"])
            if metric is None:
                raise vol.Invalid("Configure the metric first")
            if metric["config"].get("provider_id"):
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
                    "coverage": "configured"
                    if ids
                    else "local"
                    if io.startswith("local") or io == "calculated"
                    else "needs_setup",
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
                        "coverage": "needs_setup",
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
                await self.evaluate_metrics()
        else:
            await self.evaluate_metrics()

    async def evaluate_metrics(self):
        """Evaluate locally available samples without querying cloud providers."""
        async with self.lock:
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
                decision = evaluate(
                    sample,
                    now,
                    cfg["max_age"],
                    cfg.get("warning_percent", 90),
                    cfg.get("cutoff_percent", 95),
                )
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
                if decision["percent"] is None:
                    for field in ("used", "limit", "remaining"):
                        m.pop(field, None)
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
                for threshold in crossings(
                    m.get("last_percent"),
                    pct,
                    cfg.get("warning_percent", 90),
                    cfg.get("cutoff_percent", 95),
                ):
                    if threshold not in m.setdefault("notified", []):
                        m["notified"].append(threshold)
                        await self.event(
                            id,
                            "threshold",
                            threshold,
                            self.text(
                                "{name}: detected usage {percent:.2f}% — threshold {threshold}%",
                                name=self.metric_name(cfg),
                                percent=pct,
                                threshold=threshold,
                            ),
                        )
                m["last_percent"] = pct
                if (
                    pct >= cfg.get("cutoff_percent", 95)
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
            if row.get("provider_kind") == "oracle" and "sample" in row:
                metric["sample"]["limit"] = config.get("limit")

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
        if kind != "provider_quota_exhausted" and self.provider_alerts_paused(config):
            return
        self.hass.bus.async_fire("quota_guard_event", item)
        persistent_notification.async_create(
            self.hass,
            message,
            title=self.text("Cloud service quotas"),
            notification_id=f"quota_guard_{id}_{kind}",
        )
