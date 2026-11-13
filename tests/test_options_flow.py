"""Exercise options-flow saves, edits and validation against the real schema."""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from test_guard import guard


class FlowBase:
    def __init_subclass__(cls, **kwargs):
        pass

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_show_menu(self, **kwargs):
        return {"type": "menu", **kwargs}

    def async_abort(self, **kwargs):
        return {"type": "abort", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}


def load_flow():
    entries = types.ModuleType("homeassistant.config_entries")
    entries.ConfigFlow = entries.OptionsFlow = FlowBase
    core = types.ModuleType("homeassistant.core")
    core.callback = lambda function: function
    selectors = types.ModuleType("homeassistant.helpers.selector")
    for name in ("Entity", "Select", "Number"):
        setattr(selectors, name + "SelectorConfig", lambda **kwargs: kwargs)
        setattr(selectors, name + "Selector", lambda config: lambda value: value)
    spec = importlib.util.spec_from_file_location(
        "quota_guard.config_flow", Path(guard.__file__).parent / "config_flow.py"
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {
            "homeassistant.config_entries": entries,
            "homeassistant.core": core,
            "homeassistant.helpers.selector": selectors,
        },
    ):
        spec.loader.exec_module(module)
    return module


class OptionsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.flow = load_flow().OptionsFlow()
        self.manager = types.SimpleNamespace(
            data={"metrics": {}}, configure=AsyncMock()
        )
        self.flow.hass = types.SimpleNamespace(
            data={"quota_guard": self.manager},
            states={"sensor.usage": object()},
            config_entries=types.SimpleNamespace(async_entries=lambda: []),
        )
        self.input = {
            "id": "example",
            "name": "Example",
            "unit": "%",
            "kind": "free_allowance",
            "usage_entity": "sensor.usage",
            "usage_url": "/security-scanner/usage",
            "limit": 100,
            "period_mode": "monthly",
            "timestamp_attribute": "checked_at",
            "max_age": 900,
            "enforce": False,
        }

    async def test_add_saves_monitor_only_source_with_monthly_period(self):
        self.assertEqual((await self.flow.async_step_init())["type"], "menu")
        result = await self.flow.async_step_add(self.input)
        self.assertEqual(result["type"], "create_entry")
        saved = self.manager.configure.call_args.args[0].data
        self.assertEqual(saved["entry_ids"], [])
        self.assertEqual(saved["period_timezone"], "UTC")
        self.assertFalse(saved["enforce"])

    async def test_invalid_and_duplicate_sources_never_save(self):
        for change in (
            {"usage_url": "javascript:alert(1)"},
            {"period_timezone": "Bad/Timezone"},
            {"usage_entity": "sensor.missing"},
            {"enforce": True},
        ):
            with self.subTest(change=change):
                result = await self.flow.async_step_add({**self.input, **change})
                self.assertEqual(result["type"], "form")
                self.assertTrue(result["errors"])
        self.manager.data["metrics"]["example"] = {"config": self.input}
        result = await self.flow.async_step_add(self.input)
        self.assertEqual(result["errors"], {"id": "duplicate_id"})
        self.manager.configure.assert_not_awaited()

    async def test_edit_preserves_id_and_empty_optional_fields_can_be_cleared(self):
        self.manager.data["metrics"]["example"] = {"config": self.input}
        result = await self.flow.async_step_edit({"source_id": "example"})
        self.assertEqual(result["step_id"], "source")
        result = await self.flow.async_step_source(
            {**self.input, "id": "changed", "usage_url": ""}
        )
        self.assertEqual(result["type"], "create_entry")
        saved = self.manager.configure.call_args.args[0].data
        self.assertEqual(saved["id"], "example")
        self.assertNotIn("usage_url", saved)

    async def test_unloaded_integration_has_clear_error(self):
        self.flow.hass.data = {}
        self.assertEqual(
            await self.flow.async_step_init(), {"type": "abort", "reason": "not_loaded"}
        )
