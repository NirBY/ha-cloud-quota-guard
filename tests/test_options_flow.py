"""Exercise options-flow saves, edits and validation against the real schema."""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

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
    for name in ("Entity", "Select", "Number", "Text"):
        setattr(selectors, name + "SelectorConfig", lambda **kwargs: kwargs)
        setattr(selectors, name + "Selector", lambda config: lambda value: value)
    selectors.TextSelectorType = types.SimpleNamespace(PASSWORD="password")
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
            data={"metrics": {}}, configure=AsyncMock(), listeners=[]
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

    async def test_github_validation_saves_only_own_entry_and_failure_never_saves(self):
        self.flow.config_entry = types.SimpleNamespace(data={"existing": "preserved"})
        update = self.flow.hass.config_entries.async_update_entry = Mock()
        response = Mock()
        request = AsyncMock()
        request.__aenter__.return_value = response
        session = Mock()
        session.get.return_value = request
        with patch(
            "homeassistant.helpers.aiohttp_client.async_get_clientsession",
            return_value=session,
        ):
            result = await self.flow.async_step_github(
                {"name": "Own", "access_token": "own-secret"}
            )
            self.assertEqual(result["type"], "create_entry")
            self.assertIs(update.call_args.args[0], self.flow.config_entry)
            self.assertEqual(update.call_args.kwargs["data"]["existing"], "preserved")
            provider = next(iter(update.call_args.kwargs["data"]["providers"].values()))
            self.assertEqual(provider["access_token"], "own-secret")
            update.reset_mock()
            response.raise_for_status.side_effect = ValueError("own-secret")
            result = await self.flow.async_step_github(
                {"name": "Own", "access_token": "own-secret"}
            )
            self.assertEqual(result["errors"], {"base": "cannot_connect"})
            self.assertNotIn("own-secret", str(result))
            update.assert_not_called()

    async def test_google_services_and_credentials_are_explicit_and_private(self):
        self.flow.config_entry = types.SimpleNamespace(data={})
        update = self.flow.hass.config_entries.async_update_entry = Mock()
        executor = self.flow.hass.async_add_executor_job = AsyncMock(
            return_value={"private_key": "own-secret"}
        )
        result = await self.flow.async_step_google(
            {"name": "Own", "service_account_info": "own-json", "services": ["google"]}
        )
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(executor.call_args.args[1], "own-json")
        provider = next(iter(update.call_args.kwargs["data"]["providers"].values()))
        self.assertEqual(provider["services"], ["google"])
        update.reset_mock()
        executor.side_effect = ValueError("own-secret")
        result = await self.flow.async_step_google(
            {"name": "Own", "service_account_info": "own-json", "services": ["google"]}
        )
        self.assertEqual(result["errors"], {"base": "invalid_credentials"})
        self.assertNotIn("own-secret", str(result))
        update.assert_not_called()

    async def test_cutoff_screen_uses_administrator_configure_handler(self):
        self.manager.data["metrics"]["example"] = {
            "config": guard.SCHEMA(
                {**self.input, "entry_ids": [], "source": "sensor.usage"}
            )
        }
        result = await self.flow.async_step_mapping({"source_id": "example"})
        self.assertFalse(result["data_schema"]({})["enforce"])
        result = await self.flow.async_step_cutoff(
            {"enforce": True, "entry_ids": ["chosen"]}
        )
        self.assertEqual(result["type"], "create_entry")
        cfg = self.manager.configure.call_args.args[0].data
        self.assertTrue(cfg["enforce"])
        self.assertEqual(cfg["entry_ids"], ["chosen"])
