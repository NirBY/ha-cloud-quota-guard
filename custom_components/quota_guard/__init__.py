"""Generic, provider-agnostic quota monitoring. Unknown is never safe/zero."""

import asyncio
import json
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

from .engine import crossings, evaluate
from .google_provider import GoogleProvider
from .provider_policies import policy_for

DOMAIN = "quota_guard"
SCHEMA = vol.Schema(
    {
        vol.Required("id"): vol.Match(r"^[a-z0-9_]{1,60}$"),
        vol.Required("name"): cv.string,
        vol.Required("entry_ids"): [cv.string],
        vol.Required("kind"): vol.In(["free_allowance", "rate_limit", "budget"]),
        vol.Required("source"): cv.string,
        vol.Required("unit"): cv.string,
        vol.Optional("usage_entity"): cv.entity_id,
        vol.Optional("limit_entity"): cv.entity_id,
        vol.Optional("limit"): vol.All(vol.Coerce(float), vol.Range(min=0.000001)),
        vol.Optional("period_attribute", default="period"): cv.string,
        vol.Optional("timestamp_attribute", default="sampled_at"): cv.string,
        vol.Optional("max_age", default=900): vol.All(
            vol.Coerce(int), vol.Range(min=30, max=86400)
        ),
        vol.Optional("enforce", default=True): cv.boolean,
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
        "report",
        dispatch,
        schema=vol.Schema(
            {
                vol.Required("id"): cv.string,
                vol.Required("used"): vol.Coerce(float),
                vol.Required("limit"): vol.Coerce(float),
                vol.Required("period"): cv.string,
                vol.Required("timestamp"): cv.string,
            }
        ),
    )
    websocket_api.async_register_command(hass, ws_report)
    return True


async def async_setup_entry(hass, entry):
    manager = Guard(hass)
    hass.data[DOMAIN] = manager
    await manager.load()
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
    def __init__(self, hass):
        self.hass = hass
        self.store = Store(hass, 1, DOMAIN)
        self.data = {"metrics": {}, "events": []}
        self.inventory = []
        self.listeners = []
        self.lock = asyncio.Lock()
        self.google = GoogleProvider()

    async def load(self):
        self.data = await self.store.async_load() or self.data

    def snapshot(self):
        return {
            **self.data,
            "inventory": self.inventory,
            "updated_at": dt_util.utcnow().isoformat(),
        }

    async def configure(self, call):
        cfg = dict(call.data)
        if not cfg["entry_ids"]:
            raise vol.Invalid("At least one target integration is required")
        for id in cfg["entry_ids"]:
            e = self.hass.config_entries.async_get_entry(id)
            if not e or e.domain == DOMAIN:
                raise vol.Invalid("Invalid target integration")
        async with self.lock:
            old = self.data["metrics"].get(cfg["id"], {})
            self.data["metrics"][cfg["id"]] = {
                **old,
                "config": cfg,
                "status": "unknown",
            }
            await self.store.async_save(self.data)
        await self.tick()

    async def report(self, call):
        async with self.lock:
            metric = self.data["metrics"].get(call.data["id"])
            if metric is None:
                raise vol.Invalid("Configure the metric first")
            sample = {k: v for k, v in call.data.items() if k != "id"}
            if (
                evaluate(sample, dt_util.utcnow(), metric["config"]["max_age"])[
                    "percent"
                ]
                is None
            ):
                raise vol.Invalid("Invalid/stale sample, period or timestamp")
            previous = metric.get("sample", {})
            if previous.get("timestamp") and dt_util.parse_datetime(
                sample["timestamp"]
            ) < dt_util.parse_datetime(previous["timestamp"]):
                raise vol.Invalid("Out-of-order sample rejected")
            metric["sample"] = sample
            await self.store.async_save(self.data)
        await self.tick()

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

    async def tick(self, *_):
        async with self.lock:
            await self.inventory_refresh()
            now = dt_util.utcnow()
            await self.github_samples(now)
            await self.google_samples()
            for id, m in self.data["metrics"].items():
                cfg = m["config"]
                if cfg.get("usage_entity"):
                    s = self.hass.states.get(cfg["usage_entity"])
                    lim = (
                        self.hass.states.get(cfg.get("limit_entity", ""))
                        if cfg.get("limit_entity")
                        else None
                    )
                    m["sample"] = {
                        "used": s.state if s else None,
                        "limit": lim.state if lim else cfg.get("limit"),
                        "period": s.attributes.get(cfg["period_attribute"])
                        if s
                        else None,
                        "timestamp": s.attributes.get(cfg["timestamp_attribute"])
                        if s
                        else None,
                    }
                sample = m.get("sample", {})
                decision = evaluate(sample, now, cfg["max_age"])
                old_status = m.get("status")
                m.update(decision)
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
                    message = f"{cfg['name']}: המדידה חסרה או ישנה; לא ניתן לאמת הגנה"
                    if (
                        m.get("provider_error")
                        == "Google has not reported any usage samples for this day; not assumed zero"
                    ):
                        message = f"{cfg['name']}: Google עדיין לא פרסם דגימות שימוש ליום המכסה הנוכחי. ממתין לנתוני הספק; אין להסיק שהשימוש אפס."
                    # Persistent notifications may be forwarded to Telegram. Do not
                    # recreate unchanged messages on each poll or after a restart.
                    if m.get("data_alert_message") != message:
                        m["data_alert_message"] = message
                        if old_status not in (None, "unknown", "stale"):
                            await self.event(id, "data_unavailable", None, message)
                        else:
                            persistent_notification.async_create(
                                self.hass,
                                message,
                                title="מכסות שירותים",
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
                            "period": period,
                        }
                    )
                    m["points"] = points[-3000:]
                for threshold in crossings(m.get("last_percent"), pct):
                    if threshold not in m.setdefault("notified", []):
                        m["notified"].append(threshold)
                        await self.event(
                            id,
                            "threshold",
                            threshold,
                            f"{cfg['name']}: זוהה שימוש של {pct:.2f}% — סף {threshold}%",
                        )
                m["last_percent"] = pct
                if pct >= 95 and cfg["enforce"]:
                    result = {}
                    for eid in cfg["entry_ids"]:
                        e = self.hass.config_entries.async_get_entry(eid)
                        if not e:
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
                            id, "cutoff", 95, "כיבוי: " + ", ".join(result.values())
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

    async def github_samples(self, now):
        """Existing GitHub credentials stay in HA; read the provider's real counter."""
        for entry in self.hass.config_entries.async_entries("github"):
            token = entry.data.get("access_token")
            if not token:
                continue
            id = "github_" + entry.entry_id.lower() + "_core"
            metric = self.data["metrics"].setdefault(
                id,
                {
                    "config": {
                        "id": id,
                        "name": "GitHub REST — " + entry.title,
                        "entry_ids": [entry.entry_id],
                        "kind": "rate_limit",
                        "source": "https://api.github.com/rate_limit",
                        "unit": "requests",
                        "max_age": 180,
                        "enforce": True,
                    },
                    "status": "unknown",
                },
            )
            try:
                async with async_get_clientsession(self.hass).get(
                    "https://api.github.com/rate_limit",
                    headers={
                        "Authorization": "Bearer " + token,
                        "Accept": "application/vnd.github+json",
                    },
                    timeout=15,
                ) as response:
                    response.raise_for_status()
                    data = (await response.json())["resources"]["core"]
                    # Core response headers are authoritative when provided.
                    limit = response.headers.get("X-RateLimit-Limit", data["limit"])
                    used = response.headers.get("X-RateLimit-Used", data["used"])
                    reset = response.headers.get("X-RateLimit-Reset", data["reset"])
                metric["sample"] = {
                    "used": float(used),
                    "limit": float(limit),
                    "period": str(reset),
                    "timestamp": now.isoformat(),
                }
                metric["provider_error"] = None
            except Exception:
                metric["provider_error"] = (
                    "Provider unavailable; previous sample will expire"
                )

    async def google_samples(self):
        accounts = [
            {"id": e.entry_id, "info": e.data["service_account_info"]}
            for e in self.hass.config_entries.async_entries("google_cloud")
            if e.data.get("service_account_info")
        ]
        if not accounts:
            return
        entries = []
        for e in self.hass.config_entries.async_entries():
            if e.domain not in ("google", "google_assistant_sdk", "google_cloud"):
                continue
            auth = e.data.get("auth_implementation", "")
            # Legacy api-project-N service accounts can be matched to OAuth client IDs.
            project = next(
                (
                    json.loads(a["info"])["project_id"]
                    if isinstance(a["info"], str)
                    else a["info"]["project_id"]
                    for a in accounts
                    if (
                        json.loads(a["info"])
                        if isinstance(a["info"], str)
                        else a["info"]
                    )["project_id"].removeprefix("api-project-")
                    in auth
                ),
                None,
            )
            entries.append(
                {"entry_id": e.entry_id, "domain": e.domain, "project": project}
            )
        try:
            results = await self.hass.async_add_executor_job(
                self.google.fetch, accounts, entries
            )
        except Exception:
            return
        for row in results:
            m = self.data["metrics"].setdefault(row["config"]["id"], {})
            # Never carry yesterday's data into an empty new provider period.
            if "sample" not in row:
                m.pop("sample", None)
            m.update(row)

    async def event(self, id, kind, threshold, message):
        sample = self.data["metrics"][id].get("sample", {})
        item = {
            "id": id,
            "kind": kind,
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
        self.hass.bus.async_fire("quota_guard_event", item)
        persistent_notification.async_create(
            self.hass,
            message,
            title="מכסות שירותים",
            notification_id=f"quota_guard_{id}_{kind}",
        )
