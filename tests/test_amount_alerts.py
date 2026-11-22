"""Amount alerts operate with no quota; cutoff remains independently authorized."""

import unittest
from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import test_guard
from test_guard import guard

notifications = import_module(guard.__name__ + ".notifications")


class AmountAlertTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_guard.RuntimeTests.asyncSetUp

    async def test_legacy_counter_cannot_enforce_without_integration_opt_in(self):
        from quota_guard.request_counters import RequestCounters, metric_id

        self.entry.entry_id = "entry"
        self.entry.runtime_data = None
        self.entries.async_entries = lambda: [self.entry]
        metric = self.g.data["metrics"].pop("test")
        key = metric_id("entry")
        metric["config"].update(
            id=key,
            automatic_counter=True,
            threshold_mode="above",
            warning_value=50,
            critical_value=75,
        )
        metric["counter"] = {"state": "observing"}
        history = [{"used": 80, "period": "older"}]
        metric["points"] = history.copy()
        self.g.data["metrics"][key] = metric
        self.g.request_counters = RequestCounters(self.g.hass, self.g.data)
        self.g.event = AsyncMock()
        await self.g.tick(refresh_providers=False)
        self.assertEqual(metric["status"], "counter_unavailable")
        self.assertEqual(metric["counter"]["state"], "not_opted_in")
        self.assertNotIn("sample", metric)
        self.assertNotIn("used", metric)
        self.assertEqual(metric["points"], history)
        self.assertEqual(self.g.metric_diagnostics(metric), [])
        self.entries.async_set_disabled_by.assert_not_awaited()
        self.g.event.assert_not_awaited()

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

    async def test_remote_cutoff_needs_fresh_explicit_opt_in_and_stops_after_success(
        self,
    ):
        metric = self.g.data["metrics"]["test"]
        metric["config"].update(
            enforce=False,
            provider_id="provider",
            threshold_mode="above",
            warning_value=50,
            critical_value=75,
        )
        metric["sample"].pop("limit")
        self.g.entry = SimpleNamespace(
            data={"providers": {"provider": {"type": "openai"}}}
        )
        self.g.ai_samples = AsyncMock()
        executor = self.g.hass.async_add_executor_job = AsyncMock(
            return_value={"project/key": "revoked"}
        )
        control = {"targets": ["project/key"]}
        self.g.data["remote_controls"] = {"test": control}
        await self.g.tick()
        executor.assert_not_awaited()
        control["enabled"] = True
        await self.g.tick()
        executor.assert_not_awaited()
        metric["config"]["remote_enforce"] = True
        metric["sample"]["timestamp"] = "2020-01-01T00:00:00+00:00"
        await self.g.tick()
        executor.assert_not_awaited()
        metric["sample"]["timestamp"] = guard.dt_util.utcnow().isoformat()
        await self.g.tick()
        executor.assert_awaited_once()
        self.assertEqual(control["attempts"], 1)
        self.assertEqual(control["result"], {"project/key": "revoked"})
        control["checked_at"] = (
            guard.dt_util.utcnow() - timedelta(hours=1)
        ).isoformat()
        metric["sample"]["period"] = "next-period"
        await self.g.tick()
        executor.assert_awaited_once()

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
