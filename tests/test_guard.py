"""Exercise real orchestration against an in-memory HA service boundary."""

import importlib.util
import json
import sys
import types
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock


def module(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


for name in (
    "homeassistant",
    "homeassistant.components",
    "homeassistant.helpers",
    "homeassistant.util",
):
    module(name)
ws = module("homeassistant.components.websocket_api")
ws.websocket_command = lambda _: lambda f: f
ws.require_admin = ws.async_response = lambda f: f
module("homeassistant.components.persistent_notification").async_create = (
    lambda *a, **kw: None
)
sys.modules["homeassistant.components.persistent_notification"].async_dismiss = (
    lambda *a, **kw: None
)
module("homeassistant.config_entries").ConfigEntryDisabler = types.SimpleNamespace(
    USER="user"
)
cv = module("homeassistant.helpers.config_validation")
cv.string = str
cv.entity_id = str
cv.boolean = bool
module("homeassistant.helpers.event").async_track_time_interval = lambda *a: None
module("homeassistant.helpers.service").async_register_admin_service = (
    lambda *a, **kw: None
)


class Store:
    def __init__(self, *a):
        self.async_save = AsyncMock()
        self.async_load = AsyncMock(return_value=None)


module("homeassistant.helpers.storage").Store = Store
module("homeassistant.helpers.aiohttp_client").async_get_clientsession = lambda _: None
module("homeassistant.loader").async_get_integration = AsyncMock()
dt = module("homeassistant.util.dt")
dt.utcnow = lambda: datetime.now(UTC)
dt.parse_datetime = datetime.fromisoformat
path = Path(__file__).resolve().parents[1] / "custom_components/quota_guard"
spec = importlib.util.spec_from_file_location(
    "quota_guard", path / "__init__.py", submodule_search_locations=[str(path)]
)
guard = importlib.util.module_from_spec(spec)
sys.modules["quota_guard"] = guard
spec.loader.exec_module(guard)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_empty_report_is_not_zero_quota_or_cutoff(self):
        from unittest.mock import Mock

        pn = sys.modules["homeassistant.components.persistent_notification"]
        create, dismiss = pn.async_create, pn.async_dismiss
        pn.async_create = Mock()
        pn.async_dismiss = Mock()
        try:
            metric = self.g.data["metrics"]["test"]
            metric.pop("sample")
            metric["status"] = "ok"
            metric["reporting"] = {
                "state": "no_activity_reported",
                "reported_used": 0,
                "checked_at": dt.utcnow().isoformat(),
                "period": "today",
            }
            await self.g.tick()
            self.assertEqual(metric["status"], "no_activity_reported")
            self.assertIsNone(metric["percent"])
            self.assertNotIn("used", metric)
            self.assertNotIn("sample", metric)
            self.assertEqual(metric.get("points", []), [])
            self.entries.async_set_disabled_by.assert_not_awaited()
            pn.async_create.assert_not_called()
            pn.async_dismiss.assert_called_with(
                self.g.hass, "quota_guard_test_data_unavailable"
            )
            metric["reporting"]["checked_at"] = "2020-01-01T00:00:00+00:00"
            await self.g.tick()
            self.assertEqual(metric["status"], "unknown")
            pn.async_create.assert_called()
        finally:
            pn.async_create, pn.async_dismiss = create, dismiss

    async def asyncSetUp(self):
        self.entry = types.SimpleNamespace(
            domain="test_provider",
            state=types.SimpleNamespace(value="loaded"),
            disabled_by=None,
        )

        async def disable(eid, by):
            self.entry.disabled_by = by
            self.entry.state.value = "not_loaded"
            return True

        self.entries = types.SimpleNamespace(
            async_get_entry=lambda _: self.entry,
            async_entries=lambda: [],
            async_set_disabled_by=AsyncMock(side_effect=disable),
        )
        hass = types.SimpleNamespace(
            config_entries=self.entries,
            bus=types.SimpleNamespace(async_fire=lambda *a: None),
            services=types.SimpleNamespace(has_service=lambda *a: False),
        )
        self.g = guard.Guard(hass)
        self.g.inventory_refresh = AsyncMock()
        self.g.github_samples = AsyncMock()
        self.g.google_samples = AsyncMock()
        self.g.data["metrics"]["test"] = {
            "config": {
                "id": "test",
                "name": "Test",
                "entry_ids": ["entry"],
                "max_age": 300,
                "enforce": True,
            },
            "sample": {
                "used": 89,
                "limit": 100,
                "period": "p1",
                "timestamp": dt.utcnow().isoformat(),
            },
        }

    async def test_usage_without_limit_is_recorded_without_alert_or_cutoff(self):
        m = self.g.data["metrics"]["test"]
        m["sample"].pop("limit")
        await self.g.tick()
        self.assertEqual(m["status"], "usage_only")
        self.assertEqual(m["used"], 89)
        self.assertIsNone(m["percent"])
        self.assertNotIn("remaining", m)
        self.assertEqual(m["points"][0]["used"], 89)
        self.assertEqual(self.g.data["events"], [])
        self.entries.async_set_disabled_by.assert_not_awaited()
        call = types.SimpleNamespace(
            data={
                "id": "test",
                "used": 91,
                "period": "p1",
                "timestamp": dt.utcnow().isoformat(),
            }
        )
        await self.g.report(call)
        self.assertEqual(m["used"], 91)
        self.assertIsNone(m["percent"])

    async def test_warn_disable_latch_restart(self):
        await self.g.tick()
        self.entries.async_set_disabled_by.assert_not_awaited()
        m = self.g.data["metrics"]["test"]
        m["sample"]["used"] = 96
        await self.g.tick()
        self.assertEqual(
            [e["threshold"] for e in self.g.data["events"] if e["kind"] == "threshold"],
            [90, 95],
        )
        self.entries.async_set_disabled_by.assert_awaited_once_with("entry", "user")
        count = len(self.g.data["events"])
        await self.g.tick()
        self.assertEqual(count, len(self.g.data["events"]))
        m["sample"].update(used=1, period="p2")
        await self.g.tick()
        self.assertEqual(self.entry.disabled_by, "user")

    async def test_failed_unload_not_success(self):
        self.entries.async_set_disabled_by = AsyncMock(return_value=False)
        self.g.data["metrics"]["test"]["sample"]["used"] = 95
        await self.g.tick()
        self.assertEqual(
            self.g.data["metrics"]["test"]["cutoff_result"]["entry"], "restart_required"
        )

    async def test_unknown_does_not_disable(self):
        self.g.data["metrics"]["test"]["sample"]["timestamp"] = None
        await self.g.tick()
        self.entries.async_set_disabled_by.assert_not_awaited()
        self.assertIsNone(self.g.data["metrics"]["test"]["percent"])

    async def test_missing_alert_identifies_service_and_recovery_dismisses(self):
        from unittest.mock import Mock

        pn = sys.modules["homeassistant.components.persistent_notification"]
        create, dismiss = pn.async_create, pn.async_dismiss
        pn.async_create = Mock()
        pn.async_dismiss = Mock()
        try:
            await self.g.tick()
            m = self.g.data["metrics"]["test"]
            m["sample"]["timestamp"] = None
            await self.g.tick()
            self.assertIn("Test:", pn.async_create.call_args.args[1])
            self.assertEqual(pn.async_create.call_count, 1)
            self.assertNotIn("remaining", m)
            count = len(self.g.data["events"])
            await self.g.tick()
            self.assertEqual(count, len(self.g.data["events"]))
            self.assertEqual(pn.async_create.call_count, 1)
            # Profile/system language changes must not recreate an active alert.
            self.g.hass.config = types.SimpleNamespace(language="he")
            await self.g.tick()
            self.assertEqual(pn.async_create.call_count, 1)
            # Older versions only stored the message; migrate without alerting.
            m.pop("data_alert_key")
            await self.g.tick()
            self.assertEqual(pn.async_create.call_count, 1)
            # The latch lives in persisted metric data, not transient manager state.
            self.g.data = json.loads(json.dumps(self.g.data))
            await self.g.tick()
            self.assertEqual(pn.async_create.call_count, 1)
            m = self.g.data["metrics"]["test"]
            m["sample"]["timestamp"] = dt.utcnow().isoformat()
            await self.g.tick()
            pn.async_dismiss.assert_called_with(
                self.g.hass, "quota_guard_test_data_unavailable"
            )
            self.assertNotIn("data_alert_message", m)
            m["sample"]["timestamp"] = None
            await self.g.tick()
            self.assertEqual(pn.async_create.call_count, 2)
        finally:
            pn.async_create, pn.async_dismiss = create, dismiss


if __name__ == "__main__":
    unittest.main()
