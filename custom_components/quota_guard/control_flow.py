"""Review exact provider targets before optional automatic control is enabled."""

import voluptuous as vol
from homeassistant.helpers import selector

from .provider_controls import CONTROLS, targets, validate


def metric_service(provider, config):
    if provider["type"] == "google_ai":
        return "generativelanguage.googleapis.com"
    if provider["type"] == "google":
        from .google_provider import SERVICES

        for domain, row in SERVICES.items():
            if config["id"].endswith("_" + domain):
                return row[0]
    return None


class ControlFlow:
    async def async_step_provider_cutoff(self, user_input=None):
        manager = self.hass.data["quota_guard"]
        if user_input is not None:
            self._control_id = user_input["source_id"]
            return await self.async_step_provider_targets()
        return self.async_show_form(
            step_id="provider_cutoff",
            data_schema=vol.Schema(
                {
                    vol.Required("source_id"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": k, "label": m["config"]["name"]}
                                for k, m in manager.data["metrics"].items()
                            ]
                        )
                    )
                }
            ),
        )

    async def async_step_provider_targets(self, user_input=None):
        manager = self.hass.data["quota_guard"]
        metric = manager.data["metrics"][self._control_id]
        cfg = metric["config"]
        provider = dict(manager.providers.get(cfg.get("provider_id"), {}))
        kind = provider.get("type")
        if kind not in CONTROLS:
            return self.async_abort(reason="provider_control_unsupported")
        old = manager.data.get("remote_controls", {}).get(self._control_id, {})
        errors = {}
        if user_input is not None:
            try:
                if not user_input["enabled"]:
                    async with manager.lock:
                        manager.data.setdefault("remote_controls", {}).setdefault(
                            self._control_id, {}
                        )["enabled"] = False
                        cfg["remote_enforce"] = False
                        await manager.store.async_save(manager.data)
                    return self.async_create_entry(title="", data={})
                if not user_input.get("confirm_scope"):
                    raise ValueError("Confirm exact targets and scope")
                ids = targets(user_input["targets"])
                if cfg.get("threshold_mode", "percent") == "percent" and not (
                    cfg.get("limit") or metric.get("sample", {}).get("limit")
                ):
                    raise ValueError(
                        "Configure amount thresholds or a verified allowance first"
                    )
                if user_input.get("control_api_key"):
                    provider["control_api_key"] = user_input["control_api_key"].strip()
                service = metric_service(provider, cfg)
                checked = await self.hass.async_add_executor_job(
                    validate, provider, ids, service
                )
                self._control_draft = {
                    "targets": ids,
                    "verified": checked,
                    "service": service,
                    "enabled": True,
                }
                self._control_provider = provider
            except Exception:
                errors["base"] = "provider_control_invalid"
            else:
                return await self.async_step_provider_confirm()
        return self.async_show_form(
            step_id="provider_targets",
            errors=errors,
            description_placeholders={
                "action": CONTROLS[kind][0],
                "instructions": CONTROLS[kind][1],
            },
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "targets",
                        description={
                            "suggested_value": "\n".join(old.get("targets", []))
                        },
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    ),
                    **(
                        {
                            vol.Optional("control_api_key"): selector.TextSelector(
                                selector.TextSelectorConfig(
                                    type=selector.TextSelectorType.PASSWORD
                                )
                            )
                        }
                        if kind == "openrouter"
                        else {}
                    ),
                    vol.Required("enabled", default=False): bool,
                    vol.Required("confirm_scope", default=False): bool,
                }
            ),
        )

    async def async_step_provider_confirm(self, user_input=None):
        manager = self.hass.data["quota_guard"]
        cfg = manager.data["metrics"][self._control_id]["config"]
        if user_input is not None and user_input.get("confirm"):
            async with manager.lock:
                provider_id = cfg["provider_id"]
                entry = self.config_entry
                providers = {
                    **entry.data.get("providers", {}),
                    provider_id: self._control_provider,
                }
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, "providers": providers}
                )
                manager.data.setdefault("remote_controls", {})[self._control_id] = (
                    self._control_draft
                )
                cfg["remote_enforce"] = True
                await manager.store.async_save(manager.data)
            return self.async_create_entry(title="", data={})
        return self.async_show_form(
            step_id="provider_confirm",
            description_placeholders={
                "source": cfg["name"],
                "action": CONTROLS[self._control_provider["type"]][0],
                "targets": "\n".join(
                    f"- {t['name']} ({t['id']})"
                    for t in self._control_draft["verified"]
                ),
            },
            data_schema=vol.Schema({vol.Required("confirm", default=False): bool}),
        )
