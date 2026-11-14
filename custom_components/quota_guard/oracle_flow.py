"""Native OCI setup and per-quota power-off settings."""

from types import SimpleNamespace

import voluptuous as vol
from homeassistant.helpers import selector

from .oracle_provider import credentials, discover

HELP = "https://github.com/NirBY/ha-cloud-quota-guard/blob/main/README.md#setup-help-oracle"


def percent(default):
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0.01, max=100, step=0.01, mode="box", unit_of_measurement="%"
        )
    )


def threshold_fields(current):
    return {
        vol.Required(
            "warning_percent", default=current.get("warning_percent", 90)
        ): percent(90),
        vol.Required(
            "cutoff_percent", default=current.get("cutoff_percent", 95)
        ): percent(95),
    }


class OracleFlow:
    _oracle_pending = None
    _oracle_provider_id = None
    _oracle_catalog = None

    async def async_step_oracle(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                clean = await self.hass.async_add_executor_job(credentials, user_input)
            except (ValueError, TypeError, KeyError):
                errors["base"] = "invalid_oracle_credentials"
            else:
                try:
                    catalog = await self.hass.async_add_executor_job(discover, clean)
                except Exception:
                    errors["base"] = "oracle_access_failed"
                else:
                    self._oracle_pending = {
                        "type": "oracle",
                        "name": user_input["name"],
                        "credentials": clean,
                    }
                    self._oracle_provider_id = None
                    self._oracle_catalog = catalog
                    return await self.async_step_oracle_meters()
        password = selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        )
        return self.async_show_form(
            step_id="oracle",
            errors=errors,
            description_placeholders={"setup_help_url": HELP},
            data_schema=vol.Schema(
                {
                    vol.Required("name", default="Oracle Free Tier"): str,
                    vol.Required("tenancy"): str,
                    vol.Required("user"): str,
                    vol.Required("fingerprint"): str,
                    vol.Required("region"): str,
                    vol.Required("private_key"): password,
                    vol.Optional("pass_phrase"): password,
                }
            ),
        )

    async def async_step_oracle_manage(self, user_input=None):
        providers = self.hass.data["quota_guard"].providers
        choices = [
            {"value": id, "label": p["name"]}
            for id, p in providers.items()
            if p["type"] == "oracle"
        ]
        if not choices:
            return await self.async_step_oracle()
        errors = {}
        if user_input is not None:
            id = user_input["provider_id"]
            provider = providers.get(id, {})
            if provider.get("type") != "oracle":
                errors["base"] = "oracle_access_failed"
            else:
                try:
                    catalog = await self.hass.async_add_executor_job(
                        discover, provider["credentials"]
                    )
                except Exception:
                    errors["base"] = "oracle_access_failed"
                else:
                    # Previously selected meters remain selectable in a quiet month.
                    for meter in provider["meters"]:
                        catalog.setdefault(meter["id"], meter)
                    self._oracle_provider_id = id
                    self._oracle_pending = provider
                    self._oracle_catalog = catalog
                    return await self.async_step_oracle_meters()
        return self.async_show_form(
            step_id="oracle_manage",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required("provider_id"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=choices)
                    )
                }
            ),
        )

    async def async_step_oracle_meters(self, user_input=None):
        errors = {}
        catalog = self._oracle_catalog or {}
        if not catalog:
            self._oracle_pending = None
            return self.async_abort(reason="oracle_no_usage")
        if user_input is not None:
            ids = user_input.get("meter_ids", [])
            if not ids or any(id not in catalog for id in ids):
                errors["base"] = "oracle_meters_required"
            else:
                selected = [
                    {k: v for k, v in catalog[id].items() if k != "used"}
                    for id in dict.fromkeys(ids)
                ]
                provider = {**self._oracle_pending, "meters": selected}
                self._oracle_pending = None
                if self._oracle_provider_id:
                    await self.hass.data["quota_guard"].replace_oracle_meters(
                        self._oracle_provider_id, selected
                    )
                    return self.async_create_entry(title="", data={})
                return self.save_provider(provider)
        return self.async_show_form(
            step_id="oracle_meters",
            errors=errors,
            description_placeholders={"setup_help_url": HELP},
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "meter_ids",
                        default=[
                            m["id"] for m in self._oracle_pending.get("meters", [])
                        ],
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {
                                    "value": id,
                                    "label": f"{m['service']} · {m['sku_name']} · {m['sku_part_number']} · {m['unit']}",
                                }
                                for id, m in catalog.items()
                            ],
                            multiple=True,
                        )
                    )
                }
            ),
        )

    async def async_step_oracle_cutoff(self, user_input=None):
        from . import SCHEMA

        manager = self.hass.data["quota_guard"]
        metric = manager.data["metrics"].get(self._source_id, {})
        current = metric.get("config")
        if not current:
            return self.async_abort(reason="no_metrics")
        errors = {}
        if user_input is not None:
            data = {str(k): current[str(k)] for k in SCHEMA.schema if str(k) in current}
            data.pop("limit", None)
            data.update({k: v for k, v in user_input.items() if k != "confirm_targets"})
            data["entry_ids"] = []
            if data.get("enforce") and not user_input.get("confirm_targets"):
                errors["base"] = "oracle_confirmation_required"
            else:
                try:
                    await manager.configure(SimpleNamespace(data=SCHEMA(data)))
                except vol.Invalid:
                    errors["base"] = "oracle_cutoff_invalid"
                else:
                    return self.async_create_entry(title="", data={})
        known = [
            {"value": t["id"], "label": t["name"] + " · " + t["id"]}
            for t in metric.get("oracle_targets", [])
        ]
        return self.async_show_form(
            step_id="oracle_cutoff",
            errors=errors,
            description_placeholders={
                "setup_help_url": HELP,
                "unit": current["unit"],
                "source_name": current["name"],
            },
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "limit", description={"suggested_value": current.get("limit")}
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=0.000001, mode="box")
                    ),
                    **threshold_fields(current),
                    vol.Optional(
                        "oracle_instance_ids",
                        default=current.get("oracle_instance_ids", []),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=known, multiple=True, custom_value=True
                        )
                    ),
                    vol.Required("enforce", default=False): bool,
                    vol.Required("confirm_targets", default=False): bool,
                }
            ),
        )
