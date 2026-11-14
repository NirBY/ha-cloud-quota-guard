"""Human-readable alert values, independent of whether an allowance is known."""

from types import SimpleNamespace
from uuid import uuid4

import voluptuous as vol
from homeassistant.helpers import selector

from .engine import validate_thresholds


class AlertFlow:
    _notification_id = None

    async def async_step_notifications(self, user_input=None):
        return self.async_show_menu(
            step_id="notifications",
            menu_options=[
                "notification_add",
                "notification_edit",
                "notification_remove",
            ],
        )

    async def async_step_notification_add(self, user_input=None):
        from .notifications import available_channels, validate_channel

        choices = available_channels(self.hass)
        if not choices:
            return self.async_abort(reason="no_notification_services")
        existing = next(
            (
                c
                for c in self.config_entry.data.get("notification_channels", [])
                if c["id"] == self._notification_id
            ),
            {},
        )
        errors = {}
        if user_input is not None:
            try:
                channel = {
                    **user_input,
                    "id": self._notification_id or uuid4().hex[:12],
                    "targets": [
                        x.strip()
                        for x in user_input.get("targets", "").split(",")
                        if x.strip()
                    ],
                }
                validate_channel(self.hass, channel)
                entry = self.config_entry
                channels = [
                    c
                    for c in entry.data.get("notification_channels", [])
                    if c["id"] != self._notification_id
                ]
                if len(channels) >= 5:
                    raise ValueError("Too many channels")
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, "notification_channels": [*channels, channel]},
                )
            except (ValueError, KeyError, TypeError):
                errors["base"] = "notification_invalid"
            else:
                return self.async_create_entry(title="", data={})
        return self.async_show_form(
            step_id="notification_add",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "name", description={"suggested_value": existing.get("name")}
                    ): str,
                    vol.Required(
                        "destination",
                        description={"suggested_value": existing.get("destination")},
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=choices)
                    ),
                    vol.Optional(
                        "targets",
                        description={
                            "suggested_value": ",".join(existing.get("targets", []))
                        },
                    ): str,
                    vol.Required(
                        "levels",
                        default=existing.get(
                            "levels", ["warning", "critical", "cutoff"]
                        ),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": "warning", "label": "Warning"},
                                {
                                    "value": "critical",
                                    "label": "Critical / quota exhausted",
                                },
                                {"value": "cutoff", "label": "Cutoff result"},
                            ],
                            multiple=True,
                        )
                    ),
                    vol.Required(
                        "enabled", default=existing.get("enabled", False)
                    ): bool,
                }
            ),
        )

    async def async_step_notification_edit(self, user_input=None):
        channels = self.config_entry.data.get("notification_channels", [])
        if not channels:
            return self.async_abort(reason="no_notification_channels")
        if user_input is not None:
            self._notification_id = user_input["channel_id"]
            return await self.async_step_notification_add()
        return self.async_show_form(
            step_id="notification_edit",
            data_schema=vol.Schema(
                {
                    vol.Required("channel_id"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {
                                    "value": c["id"],
                                    "label": c["name"]
                                    + (" — enabled" if c.get("enabled") else " — off"),
                                }
                                for c in channels
                            ]
                        )
                    )
                }
            ),
        )

    async def async_step_notification_remove(self, user_input=None):
        entry = self.config_entry
        channels = entry.data.get("notification_channels", [])
        if not channels:
            return self.async_abort(reason="no_notification_channels")
        if user_input is not None:
            self.hass.config_entries.async_update_entry(
                entry,
                data={
                    **entry.data,
                    "notification_channels": [
                        channel
                        for channel in channels
                        if channel["id"] != user_input["channel_id"]
                    ],
                },
            )
            return self.async_create_entry(title="", data={})
        return self.async_show_form(
            step_id="notification_remove",
            data_schema=vol.Schema(
                {
                    vol.Required("channel_id"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {
                                    "value": channel["id"],
                                    "label": channel["name"]
                                    + (
                                        " — enabled"
                                        if channel.get("enabled")
                                        else " — off"
                                    ),
                                }
                                for channel in channels
                            ]
                        )
                    )
                }
            ),
        )

    async def async_step_alerts(self, user_input=None):
        metrics = self.hass.data["quota_guard"].data["metrics"]
        if not metrics:
            return self.async_abort(reason="no_metrics")
        if user_input is not None:
            self._alert_id = user_input["source_id"]
            return await self.async_step_alert_mode()
        return self.async_show_form(
            step_id="alerts",
            data_schema=vol.Schema(
                {
                    vol.Required("source_id"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": key, "label": metric["config"]["name"]}
                                for key, metric in metrics.items()
                            ]
                        )
                    )
                }
            ),
        )

    async def async_step_alert_mode(self, user_input=None):
        metric = self.hass.data["quota_guard"].data["metrics"][self._alert_id]
        cfg = metric["config"]
        if user_input is not None:
            self._alert_mode = user_input["threshold_mode"]
            return await self.async_step_alert_values()
        balance = cfg.get("measurement") == "balance"
        known = cfg.get("limit") or metric.get("sample", {}).get("limit")
        default = cfg.get(
            "threshold_mode", "below" if balance else "percent" if known else "above"
        )
        if default == "percent" and not known:
            default = "below" if balance else "above"
        options = [
            {"value": "above", "label": "Usage reaches an amount"},
            {"value": "below", "label": "Remaining balance falls to an amount"},
        ]
        if known and not balance:
            options.insert(
                0, {"value": "percent", "label": "Percentage of the known allowance"}
            )
        return self.async_show_form(
            step_id="alert_mode",
            description_placeholders={"source_name": cfg["name"], "unit": cfg["unit"]},
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "threshold_mode", default=default
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options)
                    )
                }
            ),
        )

    async def async_step_alert_values(self, user_input=None):
        from . import SCHEMA

        manager = self.hass.data["quota_guard"]
        cfg = manager.data["metrics"][self._alert_id]["config"]
        mode = self._alert_mode
        percent = mode == "percent"
        fields = (
            ("warning_percent", "cutoff_percent")
            if percent
            else ("warning_value", "critical_value")
        )
        errors = {}
        if user_input is not None:
            try:
                data = {str(k): cfg[str(k)] for k in SCHEMA.schema if str(k) in cfg}
                data.update(
                    threshold_mode=mode, **{key: user_input[key] for key in fields}
                )
                validate_thresholds(data)
                await manager.configure(SimpleNamespace(data=SCHEMA(data)))
            except (vol.Invalid, ValueError, KeyError, TypeError):
                errors["base"] = "invalid_alert_values"
            else:
                return self.async_create_entry(title="", data={})
        unit = "%" if percent else cfg["unit"]
        description = (
            "Alert when the measured amount falls to or below each value. Critical must be lower than warning."
            if mode == "below"
            else "Alert when measured usage reaches or exceeds each value. Critical must be higher than warning."
        )
        return self.async_show_form(
            step_id="alert_values",
            errors=errors,
            description_placeholders={
                "source_name": cfg["name"],
                "unit": unit,
                "rule": description,
            },
            data_schema=vol.Schema(
                {
                    vol.Required(
                        key,
                        description={
                            "suggested_value": cfg.get(
                                key,
                                90
                                if key == "warning_percent"
                                else 95
                                if key == "cutoff_percent"
                                else None,
                            )
                        },
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0.01 if percent else 0,
                            **({"max": 100} if percent else {}),
                            step="any",
                            mode="box",
                            unit_of_measurement=unit,
                        )
                    )
                    for key in fields
                }
            ),
        )
