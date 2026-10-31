"""The bundled frontend route must survive config entry reloads."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch


class FrontendTests(unittest.IsolatedAsyncioTestCase):
    async def test_static_route_and_module_registered_only_once(self):
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
        hass = types.SimpleNamespace(
            data={}, http=types.SimpleNamespace(async_register_static_paths=AsyncMock())
        )
        await module.async_register_frontend(hass)
        await module.async_register_frontend(hass)
        hass.http.async_register_static_paths.assert_awaited_once()
        frontend.add_extra_js_url.assert_called_once_with(hass, module.CARD_URL)
        config = hass.http.async_register_static_paths.call_args.args[0][0]
        self.assertEqual(config[0], "/quota_guard/quota-guard-card.js")
        self.assertTrue(Path(config[1]).is_file())
        self.assertFalse(config[2])
