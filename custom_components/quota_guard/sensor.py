"""Expose configured connections and measured usage in Home Assistant."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .engine import evaluate

DOMAIN = "quota_guard"
HELP_URL = (
    "https://github.com/NirBY/ha-cloud-quota-guard/blob/main/README.md#setup-help"
)
FRESH_STATUSES = {"ok", "warning", "cutoff", "usage_only"}


def source_device(guard, metric_id):
    config = guard.data["metrics"][metric_id]["config"]
    provider_id = config.get("provider_id")
    if provider_id and provider_id in guard.providers:
        return provider_device(guard, provider_id)
    return {
        "identifiers": {(DOMAIN, "source:" + metric_id)},
        "name": config["name"],
        "manufacturer": "Cloud Quota Guard",
        "model": "Sensor source" if config.get("usage_entity") else "Reported source",
        "entry_type": dr.DeviceEntryType.SERVICE,
        "configuration_url": HELP_URL + "-sensor",
    }


def provider_device(guard, provider_id):
    provider = guard.providers[provider_id]
    title = "GitHub" if provider["type"] == "github" else "Google"
    return {
        "identifiers": {(DOMAIN, "provider:" + provider_id)},
        "name": title + " — " + provider["name"],
        "manufacturer": title,
        "model": "REST core quota" if title == "GitHub" else "Project API quotas",
        "entry_type": dr.DeviceEntryType.SERVICE,
        "configuration_url": HELP_URL + "-" + provider["type"],
    }


async def async_setup_entry(hass, entry, async_add_entities):
    guard = hass.data[DOMAIN]
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    known = set()

    @callback
    def sync_sources():
        desired = {
            "quota_guard_connection_" + id: (Connection, id) for id in guard.providers
        }
        desired.update(
            {"quota_guard_source_" + id: (Usage, id) for id in guard.data["metrics"]}
        )
        # Removing a connection removes only this integration's source entities.
        # Historical measurements stay in the archived dashboard sources.
        for registered in er.async_entries_for_config_entry(registry, entry.entry_id):
            if (
                registered.unique_id.startswith(
                    ("quota_guard_source_", "quota_guard_connection_")
                )
                and registered.unique_id not in desired
            ):
                registry.async_remove(registered.entity_id)
        for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
            if (
                device.config_entries == {entry.entry_id}
                and any(domain == DOMAIN for domain, _ in device.identifiers)
                and not er.async_entries_for_device(registry, device.id)
            ):
                devices.async_remove_device(device.id)
        added = [
            cls(guard, id) for key, (cls, id) in desired.items() if key not in known
        ]
        known.intersection_update(desired)
        known.update(desired)
        if added:
            async_add_entities(added)

    guard.listeners.append(sync_sources)
    entry.async_on_unload(lambda: guard.listeners.remove(sync_sources))
    async_add_entities([Coverage(guard)])
    sync_sources()


class SourceEntity(SensorEntity):
    _attr_should_poll = False

    def __init__(self, guard, id):
        self.guard = guard
        self.id = id

    async def async_added_to_hass(self):
        self.guard.listeners.append(self.async_write_ha_state)
        self.async_on_remove(
            lambda: self.guard.listeners.remove(self.async_write_ha_state)
        )


class Connection(SourceEntity):
    """Show a saved connection immediately, even before its first API poll."""

    _attr_has_entity_name = True
    _attr_name = "Connection"
    _attr_translation_key = "connection"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["pending", "connected", "needs_attention", "removed"]
    _attr_icon = "mdi:cloud-check-outline"

    def __init__(self, guard, id):
        super().__init__(guard, id)
        self._attr_unique_id = "quota_guard_connection_" + id
        self._attr_device_info = provider_device(guard, id)

    @property
    def native_value(self):
        provider = self.guard.providers.get(self.id)
        if provider is None:
            return "removed"
        metrics = [
            m
            for m in self.guard.data["metrics"].values()
            if m["config"].get("provider_id") == self.id
        ]
        expected = 1 if provider["type"] == "github" else len(provider["services"])
        if not metrics or len(metrics) < expected:
            return "pending"
        if any(
            m.get("status") not in FRESH_STATUSES | {"no_activity_reported"}
            for m in metrics
        ):
            return "needs_attention"
        return "connected"

    @property
    def extra_state_attributes(self):
        return {
            "configured_sources": sum(
                m["config"].get("provider_id") == self.id
                for m in self.guard.data["metrics"].values()
            ),
            "setup_help": self._attr_device_info["configuration_url"],
        }


class Usage(SourceEntity):
    """One real usage measurement per source; unknown never becomes zero."""

    _attr_icon = "mdi:chart-line"
    _attr_has_entity_name = False

    def __init__(self, guard, id):
        super().__init__(guard, id)
        self._attr_unique_id = "quota_guard_source_" + id
        self._attr_device_info = source_device(guard, id)

    @property
    def metric(self):
        return self.guard.data["metrics"].get(self.id, {})

    @property
    def name(self):
        # Keep source labels stable and English by default. User-provided names
        # remain exactly as entered, independently of HA's system language.
        return self.metric.get("config", {}).get("name", self.id)

    @property
    def native_unit_of_measurement(self):
        return self.metric.get("config", {}).get("unit")

    @property
    def available(self):
        return (
            self.metric.get("status") in FRESH_STATUSES
            and self.native_value is not None
        )

    @property
    def native_value(self):
        if self.metric.get("status") not in FRESH_STATUSES:
            return None
        decision = evaluate(
            self.metric.get("sample") or {},
            dt_util.utcnow(),
            self.metric.get("config", {}).get("max_age", 900),
        )
        return decision.get("used")

    @property
    def extra_state_attributes(self):
        m = self.metric
        sample = m.get("sample") or {}
        return {
            "metric_id": self.id,
            "status": m.get("status", "removed"),
            "limit": sample.get("limit") if self.available else None,
            "remaining": m.get("remaining") if self.available else None,
            "percentage": m.get("percent") if self.available else None,
            "period": sample.get("period"),
            "source_timestamp": sample.get("timestamp"),
            "source_entity": m.get("config", {}).get("usage_entity"),
            "automatic_cutoff": bool(
                m.get("cutoff_authorized") and m.get("config", {}).get("enforce")
            ),
        }


class Coverage(SensorEntity):
    _attr_name = "Quota Guard monitored quotas"
    _attr_unique_id = "quota_guard_coverage"
    _attr_icon = "mdi:shield-check-outline"
    _attr_should_poll = False

    def __init__(self, guard):
        self.guard = guard

    async def async_added_to_hass(self):
        self.guard.listeners.append(self.async_write_ha_state)
        self.async_on_remove(
            lambda: self.guard.listeners.remove(self.async_write_ha_state)
        )

    @property
    def native_value(self):
        return sum(
            m.get("percent") is not None for m in self.guard.data["metrics"].values()
        )

    @property
    def extra_state_attributes(self):
        return {
            "needs_setup": sum(
                r["coverage"] == "needs_setup" for r in self.guard.inventory
            ),
            "configured_metrics": len(self.guard.data["metrics"]),
            "warning_threshold": 90,
            "cutoff_threshold": 95,
        }
