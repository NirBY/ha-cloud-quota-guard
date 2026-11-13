from types import SimpleNamespace

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector


class ConfigFlow(config_entries.ConfigFlow, domain="quota_guard"):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlow()

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id("quota_guard")
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="Cloud Quota Guard", data={})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))


class OptionsFlow(config_entries.OptionsFlow):
    """Configure sensor sources from the integration's Configure button."""

    _source_id = None

    async def async_step_init(self, user_input=None):
        if "quota_guard" not in self.hass.data:
            return self.async_abort(reason="not_loaded")
        return self.async_show_menu(step_id="init", menu_options=["add", "edit"])

    async def async_step_add(self, user_input=None):
        self._source_id = None
        return await self.async_step_source(user_input)

    async def async_step_edit(self, user_input=None):
        if user_input is not None:
            self._source_id = user_input["source_id"]
            return await self.async_step_source()
        sources = [
            {"value": key, "label": value["config"]["name"]}
            for key, value in self.hass.data["quota_guard"].data["metrics"].items()
            if value["config"].get("usage_entity")
            and not value["config"].get("builtin_name")
        ]
        if not sources:
            return self.async_abort(reason="no_sources")
        return self.async_show_form(
            step_id="edit",
            data_schema=vol.Schema(
                {
                    vol.Required("source_id"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=sources)
                    )
                }
            ),
        )

    async def async_step_source(self, user_input=None):
        from . import SCHEMA

        manager = self.hass.data.get("quota_guard")
        if manager is None:
            return self.async_abort(reason="not_loaded")
        errors = {}
        current = manager.data["metrics"].get(self._source_id, {}).get("config", {})
        if user_input is not None:
            data = {key: value for key, value in user_input.items() if value != ""}
            data.setdefault("entry_ids", [])
            data.setdefault("enforce", False)
            data.setdefault(
                "source",
                current.get("source")
                or data.get("usage_url")
                or data.get("usage_entity", ""),
            )
            if self._source_id:
                data["id"] = self._source_id
            try:
                config = SCHEMA(data)
                if not self._source_id and config["id"] in manager.data["metrics"]:
                    errors["id"] = "duplicate_id"
                elif not self.hass.states.get(config["usage_entity"]):
                    errors["usage_entity"] = "sensor_missing"
                elif config["enforce"] and not config["entry_ids"]:
                    errors["entry_ids"] = "targets_required"
                else:
                    await manager.configure(SimpleNamespace(data=config))
                    return self.async_create_entry(title="", data={})
            except (vol.Invalid, KeyError, ValueError):
                errors["base"] = "invalid_source"
        defaults = {
            "unit": "requests",
            "kind": "free_allowance",
            "max_age": 900,
            "period_attribute": "period",
            "timestamp_attribute": "sampled_at",
            "period_mode": "attribute",
            "period_timezone": "UTC",
            "enforce": False,
            "entry_ids": [],
            **current,
            **(user_input or {}),
        }
        entities = selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=["sensor", "input_number", "input_text", "input_datetime"]
            )
        )
        targets = [
            {"value": entry.entry_id, "label": entry.title}
            for entry in self.hass.config_entries.async_entries()
            if entry.domain != "quota_guard" and entry.source != "ignore"
        ]
        fields = {}

        def add(key, field, required=False):
            marker = vol.Required if required else vol.Optional
            options = (
                {"description": {"suggested_value": defaults[key]}}
                if key in defaults
                else {}
            )
            fields[marker(key, **options)] = field

        for key in ("id", "name", "unit"):
            add(key, str, True)
        add("usage_url", str)
        add("usage_entity", entities, True)
        add("usage_attribute", str)
        add(
            "kind",
            selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["free_allowance", "rate_limit", "budget"],
                    translation_key="quota_kind",
                )
            ),
            True,
        )
        add(
            "limit",
            selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.000001, mode="box")
            ),
        )
        add("limit_entity", entities)
        add(
            "period_mode",
            selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["attribute", "monthly"], translation_key="period_mode"
                )
            ),
            True,
        )
        add("period_timezone", str)
        add("period_attribute", str)
        add("period_entity", entities)
        add("timestamp_attribute", str)
        add("timestamp_entity", entities)
        add(
            "max_age",
            selector.NumberSelector(
                selector.NumberSelectorConfig(min=30, max=86400, mode="box")
            ),
            True,
        )
        add("enforce", bool, True)
        add(
            "entry_ids",
            selector.SelectSelector(
                selector.SelectSelectorConfig(options=targets, multiple=True)
            ),
        )
        return self.async_show_form(
            step_id="source", data_schema=vol.Schema(fields), errors=errors
        )
