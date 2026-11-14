"""Dashboard setup must append once and preserve unrelated user configuration."""

import asyncio
import copy
import types
import unittest
from importlib import import_module
from unittest.mock import AsyncMock

from test_guard import guard

add_view = import_module(guard.__name__ + ".dashboard_setup").add_view


class DashboardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original = {
            "title": "My home",
            "custom": {"keep": True},
            "views": [{"path": "home", "cards": [{"type": "entities"}]}],
        }
        self.config = copy.deepcopy(self.original)

        async def save(config):
            self.config = config

        self.dashboard = types.SimpleNamespace(
            mode="storage",
            async_load=AsyncMock(side_effect=lambda _: self.config),
            async_save=AsyncMock(side_effect=save),
        )
        self.hass = types.SimpleNamespace(
            data={
                "lovelace": types.SimpleNamespace(
                    dashboards={"my-dashboard": self.dashboard}
                )
            }
        )

    async def test_append_once_under_concurrent_calls_preserves_existing_views(self):
        results = await asyncio.gather(
            add_view(self.hass, "my-dashboard"), add_view(self.hass, "my-dashboard")
        )
        self.assertEqual(sorted(results), [False, True])
        self.dashboard.async_save.assert_awaited_once()
        self.assertEqual(self.config["views"][:-1], self.original["views"])
        self.assertEqual(self.config["custom"], self.original["custom"])
        self.assertEqual(self.config["views"][-1]["cards"][0]["language"], "en")

    async def test_collisions_yaml_and_strategies_never_overwritten(self):
        for config in (
            {"strategy": {"type": "home"}},
            {"views": [{"path": "cloud-quotas", "cards": []}]},
        ):
            self.config = config
            with self.assertRaises(ValueError):
                await add_view(self.hass, "my-dashboard")
        self.dashboard.mode = "yaml"
        with self.assertRaises(ValueError):
            await add_view(self.hass, "my-dashboard")
        self.dashboard.async_save.assert_not_awaited()

    async def test_missing_dashboard_is_not_created(self):
        with self.assertRaises(ValueError):
            await add_view(self.hass, "missing")
        self.dashboard.async_save.assert_not_awaited()
