from homeassistant.components.sensor import SensorEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([Coverage(hass.data["quota_guard"])])


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
