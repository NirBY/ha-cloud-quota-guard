"""Amount alerts operate with no quota; cutoff remains independently authorized."""

import unittest
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

from test_guard import RuntimeTests, guard

notifications = import_module(guard.__name__ + ".notifications")


class AmountAlertTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = RuntimeTests.asyncSetUp

    async def test_threshold_jump_alerts_once_even_with_no_limit_and_cutoff_off(self):
        metric = self.g.data["metrics"]["test"]
        metric["config"].update(
            enforce=False, threshold_mode="above", warning_value=50, critical_value=75
        )
        metric["sample"].pop("limit")
        self.g.event = AsyncMock()
        await self.g.tick()
        self.assertEqual(metric["status"], "cutoff")
        self.assertIsNone(metric["percent"])
        self.assertEqual(
            [c.args[2] for c in self.g.event.call_args_list], ["warning", "critical"]
        )
        self.entries.async_set_disabled_by.assert_not_awaited()
        await self.g.tick()
        self.assertEqual(self.g.event.await_count, 2)

    async def test_absolute_cutoff_requires_authorization_and_a_fresh_sample(self):
        metric = self.g.data["metrics"]["test"]
        metric["config"].update(
            threshold_mode="above", warning_value=50, critical_value=75
        )
        metric["sample"].pop("limit")
        metric["cutoff_authorized"] = False
        await self.g.tick()
        self.entries.async_set_disabled_by.assert_not_awaited()
        metric["cutoff_authorized"] = True
        metric["sample"]["timestamp"] = "2020-01-01T00:00:00+00:00"
        await self.g.tick()
        self.entries.async_set_disabled_by.assert_not_awaited()
        metric["sample"]["timestamp"] = guard.dt_util.utcnow().isoformat()
        await self.g.tick()
        self.entries.async_set_disabled_by.assert_awaited_once()

    async def test_low_balance_and_daily_reset_generate_new_alert(self):
        metric = self.g.data["metrics"]["test"]
        metric["config"].update(
            enforce=False, threshold_mode="below", warning_value=10, critical_value=5
        )
        metric["sample"].update(used=4)
        metric["sample"].pop("limit")
        self.g.event = AsyncMock()
        await self.g.tick()
        metric["sample"]["period"] = "p2"
        await self.g.tick()
        self.assertEqual(self.g.event.await_count, 4)

    async def test_notifications_are_off_by_default_and_failures_are_sanitized(self):
        hass = SimpleNamespace(
            services=SimpleNamespace(
                async_services=lambda: {"notify": {"telegram": {}, "greenapi": {}}},
                async_call=AsyncMock(),
            ),
            states=SimpleNamespace(async_all=lambda domain: []),
        )
        channels = [
            {
                "id": "channel",
                "destination": "service:notify.telegram",
                "levels": ["warning"],
                "targets": ["private-recipient"],
            }
        ]
        self.assertEqual(
            await notifications.deliver(hass, channels, "warning", "Demo", "Alert"), {}
        )
        hass.services.async_call.assert_not_awaited()
        channels[0]["enabled"] = True
        hass.services.async_call.side_effect = RuntimeError("private provider response")
        failures = await notifications.deliver(
            hass, channels, "warning", "Demo", "Alert"
        )
        self.assertEqual(failures, {"channel": "RuntimeError"})
        self.assertNotIn("private", str(failures))

    async def test_all_integrations_discovered_does_not_mean_verified_quota(self):
        from quota_guard.provider_policies import coverage_for

        for domain in (
            "mashov",
            "life360",
            "familylink",
            "microsoft_family_safety",
            "meitav_tariff_sync",
        ):
            self.assertEqual(
                coverage_for(domain, "cloud_polling", []), "unverified_service"
            )
            self.assertEqual(
                coverage_for(domain, "cloud_polling", ["real_sensor"]), "configured"
            )
        self.assertEqual(coverage_for("marketplace", "cloud_polling", []), "catalog")
