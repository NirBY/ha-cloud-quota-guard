"""Saved providers become visible without inventing quota measurements."""

import importlib.util
import json
import sys
import types
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

from test_guard import guard


class SensorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.registered = {}
        self.devices = {}
        self.added = []
        self.unload = []
        entity_registry = types.ModuleType("homeassistant.helpers.entity_registry")
        entity_registry.async_get = lambda _: self
        entity_registry.async_entries_for_config_entry = lambda *_: list(
            self.registered.values()
        )
        entity_registry.async_entries_for_device = lambda _, id: [
            e for e in self.registered.values() if e.device_id == id
        ]
        device_registry = types.ModuleType("homeassistant.helpers.device_registry")
        device_registry.async_get = lambda _: self
        device_registry.async_entries_for_config_entry = lambda *_: list(
            self.devices.values()
        )
        device_registry.DeviceEntryType = types.SimpleNamespace(SERVICE="service")
        sensor_base = types.ModuleType("homeassistant.components.sensor")
        sensor_base.SensorEntity = object
        sensor_base.SensorDeviceClass = types.SimpleNamespace(ENUM="enum")
        core = types.ModuleType("homeassistant.core")
        core.callback = lambda func: func
        spec = importlib.util.spec_from_file_location(
            "quota_guard.sensor", Path(guard.__file__).parent / "sensor.py"
        )
        self.sensor = importlib.util.module_from_spec(spec)
        with patch.dict(
            sys.modules,
            {
                "homeassistant.components.sensor": sensor_base,
                "homeassistant.core": core,
                "homeassistant.helpers.entity_registry": entity_registry,
                "homeassistant.helpers.device_registry": device_registry,
            },
        ):
            spec.loader.exec_module(self.sensor)
        self.manager = types.SimpleNamespace(
            providers={},
            data={"metrics": {}},
            listeners=[],
            inventory=[],
            metric_name=lambda config: config["name"],
        )
        self.entry = types.SimpleNamespace(
            entry_id="quota", async_on_unload=self.unload.append
        )
        self.hass = types.SimpleNamespace(data={"quota_guard": self.manager})

    def async_remove(self, id):
        self.registered.pop(id)

    def async_remove_device(self, id):
        self.devices.pop(id)

    def add_entities(self, entities):
        for entity in entities:
            self.added.append(entity)
            unique_id = entity._attr_unique_id
            info = getattr(entity, "_attr_device_info", None)
            device_id = next(iter(info["identifiers"]))[1] if info else None
            self.registered[unique_id] = types.SimpleNamespace(
                unique_id=unique_id, entity_id=unique_id, device_id=device_id
            )
            if info:
                self.devices[device_id] = types.SimpleNamespace(
                    id=device_id,
                    config_entries={"quota"},
                    identifiers=info["identifiers"],
                )

    def metric(self, status="ok", **changes):
        return {
            "config": {
                "id": "github",
                "name": "GitHub REST",
                "unit": "requests",
                "provider_id": "account",
                "max_age": 180,
            },
            "status": status,
            "used": 12,
            "percent": 12,
            "remaining": 88,
            "sample": {
                "used": 12,
                "limit": 100,
                "period": "window",
                "timestamp": datetime.now(UTC).isoformat(),
            },
            **changes,
        }

    async def test_new_saved_connection_and_later_measurement_appear_once(self):
        await self.sensor.async_setup_entry(self.hass, self.entry, self.add_entities)
        self.assertEqual(len(self.added), 1)
        self.manager.providers["account"] = {
            "type": "github",
            "name": "Personal",
            "access_token": "secret-token",
        }
        self.manager.listeners[0]()
        connection = self.added[-1]
        self.assertEqual(connection.native_value, "pending")
        self.assertEqual(connection._attr_device_info["name"], "GitHub — Personal")
        self.manager.data["metrics"]["github"] = self.metric()
        self.manager.listeners[0]()
        self.manager.listeners[0]()
        self.assertEqual(len(self.added), 3)
        self.assertEqual(connection.native_value, "connected")
        usage = self.added[-1]
        self.assertEqual(usage.native_value, 12)
        self.assertTrue(usage.available)
        public = [
            connection._attr_device_info,
            connection.extra_state_attributes,
            usage.extra_state_attributes,
        ]
        self.assertNotIn("secret-token", json.dumps(public, default=list))
        self.unload[0]()
        self.assertEqual(self.manager.listeners, [])

    async def test_removal_cleans_only_own_entities_and_keeps_metric_history(self):
        self.manager.providers["account"] = {"type": "github", "name": "Personal"}
        self.manager.data["metrics"]["github"] = self.metric()
        await self.sensor.async_setup_entry(self.hass, self.entry, self.add_entities)
        archived = self.manager.data["metrics"].pop("github")
        self.manager.data["retired_metrics"] = {"github": archived}
        self.manager.providers.clear()
        self.registered["unrelated"] = types.SimpleNamespace(
            unique_id="other", entity_id="unrelated", device_id="other"
        )
        self.manager.listeners[0]()
        self.assertEqual(set(self.registered), {"quota_guard_coverage", "unrelated"})
        self.assertEqual(self.devices, {})
        self.assertIs(self.manager.data["retired_metrics"]["github"], archived)

    async def test_manual_source_and_threshold_values_are_visible(self):
        metric = self.metric()
        metric["config"].pop("provider_id")
        metric["config"].update(
            name="Security Scanner", usage_entity="sensor.scanner", unit="%"
        )
        self.manager.data["metrics"]["scanner"] = metric
        await self.sensor.async_setup_entry(self.hass, self.entry, self.add_entities)
        usage = self.added[-1]
        self.assertEqual(usage.name, "Security Scanner")
        self.assertEqual(usage._attr_device_info["model"], "Sensor source")
        for status in ("ok", "warning", "cutoff", "usage_only"):
            metric["status"] = status
            self.assertTrue(usage.available)
            self.assertEqual(usage.native_value, 12)

    async def test_stale_missing_and_empty_reports_never_show_zero(self):
        self.manager.providers["account"] = {"type": "github", "name": "Personal"}
        self.manager.data["metrics"]["github"] = self.metric()
        usage = self.sensor.Usage(self.manager, "github")
        connection = self.sensor.Connection(self.manager, "account")
        for status in ("stale", "unknown", "no_activity_reported"):
            self.manager.data["metrics"]["github"]["status"] = status
            self.assertIsNone(usage.native_value)
            self.assertFalse(usage.available)
            self.assertIsNone(usage.extra_state_attributes["percentage"])
        self.assertEqual(connection.native_value, "connected")
        self.manager.data["metrics"]["github"]["status"] = "ok"
        self.manager.data["metrics"]["github"]["sample"]["timestamp"] = (
            "2020-01-01T00:00:00+00:00"
        )
        self.assertFalse(usage.available)

    async def test_google_connection_waits_for_all_selected_sources(self):
        self.manager.providers["account"] = {
            "type": "google",
            "name": "Home",
            "services": ["google", "google_cloud"],
        }
        self.manager.data["metrics"]["calendar"] = self.metric()
        connection = self.sensor.Connection(self.manager, "account")
        self.assertEqual(connection.native_value, "pending")
        self.manager.data["metrics"]["tts"] = self.metric(status="unknown")
        self.assertEqual(connection.native_value, "needs_attention")
        self.manager.data["metrics"]["tts"]["status"] = "no_activity_reported"
        self.assertEqual(connection.native_value, "connected")

    async def test_save_provider_notifies_entity_discovery_immediately(self):
        from test_options_flow import load_flow

        flow = load_flow().OptionsFlow()
        flow.config_entry = types.SimpleNamespace(data={})
        notify = Mock()
        flow.hass = types.SimpleNamespace(
            data={"quota_guard": types.SimpleNamespace(listeners=[notify])},
            config_entries=types.SimpleNamespace(async_update_entry=Mock()),
        )
        flow.save_provider({"type": "github", "name": "Personal"})
        notify.assert_called_once_with()
