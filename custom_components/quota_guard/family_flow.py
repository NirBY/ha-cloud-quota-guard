"""Screen-time monitoring using only explicitly selected family sensors."""

from types import SimpleNamespace
from uuid import uuid4

import voluptuous as vol
from homeassistant.helpers import selector

FAMILIES = {
    "microsoft_family_safety": "Microsoft Family Safety",
    "familylink": "Google Family Link",
}


def sensor_choices(hass, domain):
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    entries = {
        e.entry_id for e in hass.config_entries.async_entries() if e.domain == domain
    }
    choices = []
    for entity in registry.entities.values():
        if entity.config_entry_id not in entries or not entity.entity_id.startswith(
            "sensor."
        ):
            continue
        state = hass.states.get(entity.entity_id)
        if not state or state.attributes.get("unit_of_measurement") != "min":
            continue
        # Count/duration-formatted/remaining/location sensors are different metrics.
        if "screen_time" not in entity.unique_id or "remaining" in entity.unique_id:
            continue
        if not state.attributes.get("date"):
            continue
        choices.append({"value": entity.entity_id, "label": state.name})
    return choices


class FamilyFlow:
    async def async_step_family(self, user_input=None):
        if user_input is not None:
            self._family_domain = user_input["integration"]
            return await self.async_step_family_sensor()
        return self.async_show_form(
            step_id="family",
            data_schema=vol.Schema(
                {
                    vol.Required("integration"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": k, "label": v} for k, v in FAMILIES.items()
                            ]
                        )
                    )
                }
            ),
        )

    async def async_step_family_sensor(self, user_input=None):
        from . import SCHEMA

        domain = self._family_domain
        choices = sensor_choices(self.hass, domain)
        if not choices:
            return self.async_abort(reason="no_family_sensors")
        errors = {}
        if user_input is not None:
            try:
                if user_input["usage_entity"] not in {c["value"] for c in choices}:
                    raise ValueError("Select a sensor from this integration")
                state = self.hass.states.get(user_input["usage_entity"])
                data = {
                    **user_input,
                    "id": "family_" + uuid4().hex[:12],
                    "unit": "min",
                    "source": FAMILIES[domain] + " screen-time sensor",
                    "kind": "budget",
                    "entry_ids": [],
                    "enforce": False,
                    "period_mode": "daily",
                    "period_attribute": "date",
                    "period_timezone": self.hass.config.time_zone,
                    "timestamp_mode": "attribute"
                    if state.attributes.get("last_api_poll")
                    else "state_reported",
                    "timestamp_attribute": "last_api_poll",
                    "max_age": 3600,
                    "threshold_mode": "above",
                }
                await self.hass.data["quota_guard"].configure(
                    SimpleNamespace(data=SCHEMA(data))
                )
            except (ValueError, TypeError, KeyError, vol.Invalid):
                errors["base"] = "invalid_source"
            else:
                return self.async_create_entry(title="", data={})
        return self.async_show_form(
            step_id="family_sensor",
            errors=errors,
            description_placeholders={"provider": FAMILIES[domain]},
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "name", default=FAMILIES[domain] + " — screen time today"
                    ): str,
                    vol.Required("usage_entity"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=choices)
                    ),
                    vol.Required("warning_value"): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, unit_of_measurement="min", mode="box"
                        )
                    ),
                    vol.Required("critical_value"): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, unit_of_measurement="min", mode="box"
                        )
                    ),
                    vol.Optional("limit"): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1, unit_of_measurement="min", mode="box"
                        )
                    ),
                }
            ),
        )
