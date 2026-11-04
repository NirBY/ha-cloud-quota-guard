"""Frontend URLs follow bundle content and survive entry reloads."""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


class FrontendTests(unittest.IsolatedAsyncioTestCase):
    def load_frontend(self):
        frontend = types.ModuleType("homeassistant.components.frontend")
        frontend.add_extra_js_url = Mock()
        http = types.ModuleType("homeassistant.components.http")
        http.StaticPathConfig = lambda *args: args
        path = (
            Path(__file__).resolve().parents[1]
            / "custom_components/quota_guard/frontend.py"
        )
        spec = importlib.util.spec_from_file_location("quota_frontend_test", path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(
            sys.modules,
            {
                "homeassistant.components.frontend": frontend,
                "homeassistant.components.http": http,
            },
        ):
            spec.loader.exec_module(module)
        return module, frontend

    async def test_registration_reconciles_reloads_and_upgrades(self):
        module, frontend = self.load_frontend()

        async def executor(func):
            return func()

        hass = types.SimpleNamespace(
            data={},
            async_add_executor_job=executor,
            http=types.SimpleNamespace(async_register_static_paths=AsyncMock()),
        )
        await module.async_register_frontend(hass)
        await module.async_register_frontend(hass)
        hass.http.async_register_static_paths.assert_awaited_once()
        first = module.asset_details()
        frontend.add_extra_js_url.assert_called_once_with(hass, first["url"])
        configs = hass.http.async_register_static_paths.call_args.args[0]
        self.assertEqual([c[0] for c in configs], [module.CARD_PATH, first["url"]])
        self.assertTrue(all(Path(c[1]).is_file() and c[2] is False for c in configs))
        updated = {
            **first,
            "url": "/quota_guard/012345abcdef/quota-guard-card.js",
            "version": "0.5.2",
        }
        with patch.object(module, "asset_details", return_value=updated):
            await module.async_register_frontend(hass)
        self.assertEqual(
            hass.http.async_register_static_paths.call_args.args[0][0][0],
            updated["url"],
        )
        self.assertEqual(
            len(hass.http.async_register_static_paths.call_args.args[0]), 1
        )
        self.assertEqual(hass.data[module.STATE_KEY]["version"], "0.5.2")
        frontend.add_extra_js_url.assert_called_with(hass, updated["url"])

    async def test_old_boolean_registration_marker_migrates_without_duplicate_route(
        self,
    ):
        module, frontend = self.load_frontend()

        async def executor(func):
            return func()

        hass = types.SimpleNamespace(
            data={module.STATE_KEY: True},
            async_add_executor_job=executor,
            http=types.SimpleNamespace(async_register_static_paths=AsyncMock()),
        )
        await module.async_register_frontend(hass)
        configs = hass.http.async_register_static_paths.call_args.args[0]
        self.assertEqual(len(configs), 1)
        self.assertNotEqual(configs[0][0], module.CARD_PATH)
        self.assertIn(module.CARD_PATH, hass.data[module.STATE_KEY]["paths"])
