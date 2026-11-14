"""Compact AI and Tuya setup, with explicit automatic versus manual data."""

from types import SimpleNamespace
from uuid import uuid4

import voluptuous as vol
from homeassistant.helpers import selector

from .ai_provider import AI_SERVICES, AUTOMATIC, AIProvider
from .credentials import google_credentials
from .oracle_flow import threshold_fields

SERVICES = {
    **AI_SERVICES,
    "tuya": ("Tuya", "https://iot.tuya.com"),
    "imou": ("IMOU", "https://open.imoulife.com/consoleNew/resourceManage/myResource"),
}
HELP = "https://github.com/NirBY/ha-cloud-quota-guard/blob/main/README.md#setup-help-"


def manual_source(service):
    return SERVICES[service][0] + " — manually entered reading"


def select(options):
    return selector.SelectSelector(selector.SelectSelectorConfig(options=options))


class UsageFlow:
    _usage_service = None
    _manual_config = None
    _manual_id = None

    async def async_step_ai(self, user_input=None):
        if user_input is not None:
            self._usage_service = user_input["provider"]
            if self._usage_service not in AI_SERVICES:
                return self.async_abort(reason="no_sources")
            mode = user_input["account_type"]
            if mode == "api":
                if self._usage_service not in AUTOMATIC:
                    return await self.async_step_ai_unavailable()
                return await self.async_step_ai_connect()
            if mode == "sensor":
                return await self.async_step_usage_sensor()
            if mode == "update":
                return await self.async_step_usage_update()
            return await self.async_step_usage_manual()
        return self.async_show_form(
            step_id="ai",
            data_schema=vol.Schema(
                {
                    vol.Required("provider"): select(
                        [{"value": k, "label": v[0]} for k, v in AI_SERVICES.items()]
                    ),
                    vol.Required("account_type", default="api"): select(
                        [
                            {"value": "api", "label": "API / team account — automatic"},
                            {
                                "value": "personal",
                                "label": "Personal subscription — manual reading",
                            },
                            {"value": "sensor", "label": "Existing usage sensor"},
                            {"value": "update", "label": "Update a manual reading"},
                        ]
                    ),
                }
            ),
            description_placeholders={"setup_help_url": HELP + "ai"},
        )

    async def async_step_tuya(self, user_input=None):
        self._usage_service = "tuya"
        return self.async_show_menu(
            step_id="tuya",
            menu_options=["usage_sensor", "usage_manual", "usage_update"],
            description_placeholders={"setup_help_url": HELP + "tuya"},
        )

    async def async_step_imou(self, user_input=None):
        self._usage_service = "imou"
        return self.async_show_menu(
            step_id="imou",
            menu_options=["usage_sensor", "usage_manual", "usage_update"],
            description_placeholders={"setup_help_url": HELP + "imou"},
        )

    async def async_step_ai_unavailable(self, user_input=None):
        if user_input is not None:
            return await self.async_step_usage_manual()
        return self.async_show_form(
            step_id="ai_unavailable",
            data_schema=vol.Schema({}),
            description_placeholders={
                "provider": SERVICES[self._usage_service][0],
                "setup_help_url": HELP + "ai",
            },
        )

    async def async_step_ai_connect(self, user_input=None):
        kind = self._usage_service
        name = SERVICES[kind][0]
        errors = {}
        if user_input is not None:
            provider = {"type": kind, "name": user_input["name"].strip()}
            try:
                if not provider["name"]:
                    raise ValueError("Missing name")
                if kind == "google_ai":
                    provider[
                        "service_account_info"
                    ] = await self.hass.async_add_executor_job(
                        google_credentials, user_input["service_account_info"]
                    )
                else:
                    key = user_input["api_key"].strip()
                    if not key or any(char.isspace() for char in key):
                        raise ValueError("Invalid key")
                    provider["api_key"] = key
                if kind == "grok":
                    provider["team_id"] = user_input["team_id"].strip()
                if kind == "deepseek":
                    provider["currency"] = user_input["currency"]
                    if provider["currency"] not in ("USD", "CNY"):
                        raise ValueError("Invalid currency")
                else:
                    if user_input.get("limit") is not None:
                        from .ai_provider import number

                        provider["limit"] = float(number(user_input["limit"]))
                        if provider["limit"] <= 0:
                            raise ValueError("Invalid allowance")
                    provider.update(
                        {
                            k: float(user_input[k])
                            for k in ("warning_percent", "cutoff_percent")
                        }
                    )
                    if (
                        not 0
                        < provider["warning_percent"]
                        < provider["cutoff_percent"]
                        <= 100
                    ):
                        raise ValueError("Invalid thresholds")
            except (ValueError, TypeError, KeyError):
                errors["base"] = "ai_credentials_invalid"
            else:
                id = uuid4().hex[:12]
                rows = await self.hass.async_add_executor_job(
                    AIProvider().fetch, {"id": id, **provider}
                )
                if rows[0].get("provider_error"):
                    errors["base"] = "ai_access_failed"
                else:
                    result = self.save_provider(provider, provider_id=id)
                    manager = self.hass.data["quota_guard"]
                    async with manager.poll_lock:
                        for row in rows:
                            await manager.apply_provider_row(row)
                    await manager.tick(refresh_providers=False)
                    return result
        password = selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        )
        fields = {vol.Required("name", default=name): str}
        fields[
            vol.Required("service_account_info" if kind == "google_ai" else "api_key")
        ] = password
        if kind == "grok":
            fields[vol.Required("team_id")] = str
        if kind == "deepseek":
            fields[vol.Required("currency", default="USD")] = select(["USD", "CNY"])
        else:
            fields[vol.Optional("limit")] = selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.000001, mode="box")
            )
            fields.update(threshold_fields({}))
        instructions = {
            "openai": "Use an OpenAI organization Admin API key. Reads reported API cost for the UTC calendar month, not ChatGPT or Codex subscription limits. Optional budget is in USD.",
            "google_ai": "Paste a service-account JSON key for the Gemini project with Cloud Monitoring Viewer access. Reads reported output tokens across the project for the UTC calendar month. It does not measure input tokens, cost, or per-model rate limits. Optional allowance is in output tokens. A Gemini API key is not sufficient.",
            "claude": "Use a Claude Console Admin API key or a Claude Enterprise analytics key. Reads organization cost for the UTC calendar month, after discounts and before credits. Reporting can lag up to 24 hours. Optional budget is in USD. Personal Pro/Max limits are not exposed by this connection.",
            "cursor": "Use a Cursor team Admin API key. Reads total team on-demand spend for the current billing cycle, excluding included usage. Optional budget is in USD. Individual subscription limits are not exposed by this connection.",
            "deepseek": "Use a DeepSeek API key and select the currency of your balance. Reads available API credits only; this is not monthly consumption or a personal chat limit. Percentages and cutoff do not apply to a credit balance.",
            "grok": "Use an xAI Management API key with billing-usage read access and your team ID. Reads reported API cost for the UTC calendar month. Optional budget is in USD. Grok personal subscriptions and X Premium limits are separate.",
            "openrouter": "Use the OpenRouter API key whose usage you want to monitor. Reads this key's reported monthly spend in USD, excluding separate BYOK usage. It does not show whole-account spend. Optional budget is in USD.",
            "mistral": "Use a Mistral Enterprise Admin API key. Reads distinct API calls across the organization for the UTC calendar month. Optional allowance is in requests, not money. Standard API keys and personal subscriptions cannot use the Admin API; choose a manual reading or existing sensor instead.",
        }
        return self.async_show_form(
            step_id="ai_connect",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={
                "provider": name,
                "instructions": instructions[kind],
                "setup_help_url": HELP + kind,
            },
        )

    async def async_step_usage_sensor(self, user_input=None):
        name, portal = SERVICES[self._usage_service]
        self._source_defaults = {
            "id": self._usage_service + "_sensor_" + uuid4().hex[:8],
            "name": name + " — sensor usage",
            "usage_url": portal,
        }
        self._source_help = HELP + (
            self._usage_service if self._usage_service in ("tuya", "imou") else "ai"
        )
        return await self.async_step_add(user_input)

    async def async_step_usage_manual(self, user_input=None):
        from . import SCHEMA

        self._manual_id = None
        errors = {}
        if user_input is not None:
            try:
                data = {k: v for k, v in user_input.items() if v != ""}
                if data["unit"] == "%":
                    data["limit"] = 100
                config = SCHEMA(
                    {
                        **data,
                        "id": self._usage_service + "_manual_" + uuid4().hex[:12],
                        "source": manual_source(self._usage_service),
                        "usage_url": SERVICES[self._usage_service][1],
                        "kind": "budget" if data["unit"] == "USD" else "free_allowance",
                        "entry_ids": [],
                        "enforce": False,
                        "max_age": 86400,
                    }
                )
                if (
                    not config["name"].strip()
                    or not 0
                    < config["warning_percent"]
                    < config["cutoff_percent"]
                    <= 100
                ):
                    raise ValueError("Invalid settings")
                self._manual_config = config
                return await self.async_step_usage_reading()
            except (vol.Invalid, ValueError, TypeError, KeyError):
                errors["base"] = "invalid_source"
        return self.async_show_form(
            step_id="usage_manual",
            errors=errors,
            description_placeholders={
                "provider": SERVICES[self._usage_service][0],
                "usage_url": SERVICES[self._usage_service][1],
            },
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "name",
                        default=SERVICES[self._usage_service][0] + " — manual usage",
                    ): str,
                    vol.Required("unit", default="%"): select(
                        ["%", "requests", "messages", "tokens", "USD"]
                    ),
                    vol.Optional("limit"): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=0.000001, mode="box")
                    ),
                    **threshold_fields({}),
                }
            ),
        )

    async def async_step_usage_update(self, user_input=None):
        metrics = self.hass.data["quota_guard"].data["metrics"]
        choices = {
            id: m["config"]
            for id, m in metrics.items()
            if m["config"].get("source") == manual_source(self._usage_service)
            and not m["config"].get("provider_id")
            and not m["config"].get("usage_entity")
        }
        if not choices:
            return self.async_abort(reason="no_manual_readings")
        if user_input is not None and user_input["source_id"] in choices:
            self._manual_id = user_input["source_id"]
            self._manual_config = choices[self._manual_id]
            return await self.async_step_usage_reading()
        return self.async_show_form(
            step_id="usage_update",
            data_schema=vol.Schema(
                {
                    vol.Required("source_id"): select(
                        [{"value": k, "label": v["name"]} for k, v in choices.items()]
                    )
                }
            ),
        )

    async def async_step_usage_reading(self, user_input=None):
        from .ai_provider import number

        manager = self.hass.data["quota_guard"]
        cfg = self._manual_config
        errors = {}
        if user_input is not None:
            try:
                if user_input["reading_type"] not in ("used", "remaining"):
                    raise ValueError("Invalid reading type")
                used = float(number(user_input["value"]))
                if user_input["reading_type"] == "remaining":
                    if cfg.get("limit") is None or used > cfg["limit"]:
                        raise ValueError("Remaining needs an allowance")
                    used = cfg["limit"] - used
                if cfg["unit"] == "%" and used > 100:
                    raise ValueError("Invalid percentage")
                sample = {
                    "used": used,
                    "period": user_input["period"].strip(),
                    "timestamp": user_input["timestamp"].strip(),
                }
                if user_input.get("expires_at", "").strip():
                    sample["expires_at"] = user_input["expires_at"].strip()
                if self._manual_id:
                    await manager.report(
                        SimpleNamespace(data={"id": self._manual_id, **sample})
                    )
                else:
                    await manager.configure(
                        SimpleNamespace(data=cfg), initial_sample=sample
                    )
                return self.async_create_entry(title="", data={})
            except (vol.Invalid, ValueError, KeyError, TypeError):
                errors["base"] = "invalid_reading"
        return self.async_show_form(
            step_id="usage_reading",
            errors=errors,
            description_placeholders={
                "source_name": cfg["name"],
                "unit": cfg["unit"],
                "usage_url": SERVICES[self._usage_service][1],
            },
            data_schema=vol.Schema(
                {
                    vol.Required("reading_type", default="used"): select(
                        [
                            {"value": "used", "label": "Used"},
                            {"value": "remaining", "label": "Remaining"},
                        ]
                    ),
                    vol.Required("value"): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=0, mode="box")
                    ),
                    vol.Required("period"): str,
                    vol.Required("timestamp"): str,
                    vol.Optional("expires_at"): str,
                }
            ),
        )
