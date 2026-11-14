from types import SimpleNamespace
from uuid import uuid4

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .alert_flow import AlertFlow
from .control_flow import ControlFlow
from .family_flow import FamilyFlow
from .oracle_flow import OracleFlow, threshold_fields
from .usage_flow import UsageFlow

SETUP_HELP = (
    "https://github.com/NirBY/ha-cloud-quota-guard/blob/main/README.md#setup-help"
)


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


class OptionsFlow(
    ControlFlow,
    FamilyFlow,
    AlertFlow,
    UsageFlow,
    OracleFlow,
    config_entries.OptionsFlow,
):
    """Configure sensor sources from the integration's Configure button."""

    _source_id = None

    async def async_step_init(self, user_input=None):
        if "quota_guard" not in self.hass.data:
            return self.async_abort(reason="not_loaded")
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "add",
                "edit",
                "ai",
                "family",
                "tuya",
                "imou",
                "github",
                "google",
                "oracle",
                "oracle_manage",
                "remove_provider",
                "mapping",
                "provider_cutoff",
                "alerts",
                "notifications",
                "dashboard",
                "request_counters",
            ],
        )

    async def async_step_request_counters(self, user_input=None):
        manager = self.hass.data["quota_guard"]
        if user_input is not None:
            manager.data["automatic_request_counters"] = user_input["enabled"]
            await manager.tick(refresh_providers=False)
            return self.async_create_entry(title="", data={})
        statuses = manager.request_counters.states if manager.request_counters else {}
        labels = {
            "observing": "Counting HTTP requests",
            "waiting_for_client": "Waiting for a supported HTTP client",
            "integration_not_loaded": "Integration is not loaded",
            "shared_client_unsupported": "Shared HTTP client cannot be attributed safely",
            "off": "Off",
        }
        return self.async_show_form(
            step_id="request_counters",
            description_placeholders={
                "coverage": "\n".join(
                    f"- {s['name']}: {labels[s['state']]}" for s in statuses.values()
                )
                or "No supported HTTP clients are loaded yet."
            },
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "enabled",
                        default=manager.data.get("automatic_request_counters", True),
                    ): bool
                }
            ),
        )

    async def async_step_dashboard(self, user_input=None):
        from .dashboard_setup import add_view, dashboards

        errors = {}
        if user_input is not None:
            try:
                await add_view(self.hass, user_input["dashboard_path"])
            except Exception:
                errors["base"] = "dashboard_failed"
            else:
                return self.async_create_entry(title="", data={})
        choices = [
            {
                "value": path or "lovelace",
                "label": (dashboard.config or {}).get("title", path or "Overview"),
            }
            for path, dashboard in dashboards(self.hass).items()
            if dashboard.mode == "storage"
        ]
        return self.async_show_form(
            step_id="dashboard",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required("dashboard_path"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=choices)
                    )
                }
            ),
        )

    async def async_step_github(self, user_input=None):
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        errors = {}
        if user_input is not None:
            token = user_input["access_token"].strip()
            try:
                if not token:
                    raise ValueError("Missing token")
                async with async_get_clientsession(self.hass).get(
                    "https://api.github.com/rate_limit",
                    headers={"Authorization": "Bearer " + token},
                    timeout=15,
                ) as response:
                    response.raise_for_status()
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                return self.save_provider(
                    {
                        "type": "github",
                        "name": user_input["name"],
                        "access_token": token,
                    }
                )
        return self.async_show_form(
            step_id="github",
            errors=errors,
            description_placeholders={"setup_help_url": SETUP_HELP + "-github"},
            data_schema=vol.Schema(
                {
                    vol.Required("name"): str,
                    vol.Required("access_token"): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                }
            ),
        )

    async def async_step_google(self, user_input=None):
        from .credentials import google_credentials
        from .google_provider import SERVICES

        errors = {}
        if user_input is not None:
            try:
                services = user_input["services"]
                if not services or any(service not in SERVICES for service in services):
                    raise ValueError("Select supported APIs")
                info = await self.hass.async_add_executor_job(
                    google_credentials, user_input["service_account_info"]
                )
            except (ValueError, KeyError, TypeError):
                errors["base"] = "invalid_credentials"
            else:
                return self.save_provider(
                    {
                        "type": "google",
                        "name": user_input["name"],
                        "service_account_info": info,
                        "services": services,
                    }
                )
        return self.async_show_form(
            step_id="google",
            errors=errors,
            description_placeholders={"setup_help_url": SETUP_HELP + "-google"},
            data_schema=vol.Schema(
                {
                    vol.Required("name"): str,
                    vol.Required("service_account_info"): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                    vol.Required("services"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=list(SERVICES),
                            multiple=True,
                            translation_key="google_services",
                        )
                    ),
                }
            ),
        )

    def save_provider(self, provider, provider_id=None):
        entry = self.config_entry
        providers = {
            **entry.data.get("providers", {}),
            provider_id or uuid4().hex[:12]: provider,
        }
        self.hass.config_entries.async_update_entry(
            entry, data={**entry.data, "providers": providers}
        )
        # Publish the saved connection before the next provider poll. Its status
        # stays pending until a real response arrives.
        manager = self.hass.data["quota_guard"]
        for listener in tuple(manager.listeners):
            listener()
        return self.async_create_entry(title="", data={})

    async def async_step_remove_provider(self, user_input=None):
        manager = self.hass.data["quota_guard"]
        if user_input is not None:
            await manager.remove_provider(user_input["provider_id"])
            return self.async_create_entry(title="", data={})
        providers = [
            {"value": id, "label": provider["name"]}
            for id, provider in manager.providers.items()
        ]
        if not providers:
            return self.async_abort(reason="no_providers")
        return self.async_show_form(
            step_id="remove_provider",
            data_schema=vol.Schema(
                {
                    vol.Required("provider_id"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=providers)
                    )
                }
            ),
        )

    async def async_step_mapping(self, user_input=None):
        if user_input is not None:
            self._source_id = user_input["source_id"]
            return await self.async_step_cutoff()
        metrics = self.hass.data["quota_guard"].data["metrics"]
        if not metrics:
            return self.async_abort(reason="no_metrics")
        return self.async_show_form(
            step_id="mapping",
            data_schema=vol.Schema(
                {
                    vol.Required("source_id"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": id, "label": metric["config"]["name"]}
                                for id, metric in metrics.items()
                            ]
                        )
                    )
                }
            ),
        )

    async def async_step_cutoff(self, user_input=None):
        from . import SCHEMA
        from .ai_provider import AUTOMATIC

        manager = self.hass.data["quota_guard"]
        current = manager.data["metrics"].get(self._source_id, {}).get("config")
        if current is None:
            return self.async_abort(reason="no_metrics")
        provider_type = manager.providers.get(current.get("provider_id"), {}).get(
            "type"
        )
        if (
            manager.providers.get(current.get("provider_id"), {}).get("type")
            == "oracle"
        ):
            return await self.async_step_oracle_cutoff(user_input)
        errors = {}
        if user_input is not None:
            data = {str(k): current[str(k)] for k in SCHEMA.schema if str(k) in current}
            if provider_type in AUTOMATIC and provider_type != "deepseek":
                data.pop("limit", None)
            data.update(user_input)
            data["entry_ids"] = user_input.get("entry_ids", [])
            try:
                await manager.configure(SimpleNamespace(data=SCHEMA(data)))
            except vol.Invalid:
                errors["base"] = "targets_required"
            else:
                return self.async_create_entry(title="", data={})
        targets = [
            {"value": entry.entry_id, "label": entry.title}
            for entry in self.hass.config_entries.async_entries()
            if entry.domain != "quota_guard" and entry.source != "ignore"
        ]
        return self.async_show_form(
            step_id="cutoff",
            errors=errors,
            data_schema=vol.Schema(
                {
                    **(
                        {
                            vol.Optional(
                                "limit",
                                description={"suggested_value": current.get("limit")},
                            ): selector.NumberSelector(
                                selector.NumberSelectorConfig(min=0.000001, mode="box")
                            )
                        }
                        if provider_type in AUTOMATIC and provider_type != "deepseek"
                        else {}
                    ),
                    **(
                        threshold_fields(current)
                        if current.get("threshold_mode", "percent") == "percent"
                        else {}
                    ),
                    vol.Required("enforce", default=False): bool,
                    vol.Optional(
                        "entry_ids",
                        description={"suggested_value": current["entry_ids"]},
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=targets, multiple=True)
                    ),
                }
            ),
        )

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
            for key in (
                "threshold_mode",
                "warning_value",
                "critical_value",
                "timestamp_mode",
            ):
                if key in current:
                    data.setdefault(key, current[key])
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
            **getattr(self, "_source_defaults", {}),
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
                    options=["attribute", "monthly", "daily"],
                    translation_key="period_mode",
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
        fields.update(threshold_fields(defaults))
        add(
            "entry_ids",
            selector.SelectSelector(
                selector.SelectSelectorConfig(options=targets, multiple=True)
            ),
        )
        return self.async_show_form(
            step_id="source",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={
                "setup_help_url": getattr(self, "_source_help", SETUP_HELP + "-sensor")
            },
        )
