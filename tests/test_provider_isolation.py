"""Regression coverage for HACS review #13027 and persisted cutoff consent."""

import copy
import json
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_guard import dt, guard


def own_entry(providers=None):
    return types.SimpleNamespace(data={"providers": providers or {}})


class IsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.target = types.SimpleNamespace(
            domain="github",
            disabled_by=None,
            state=types.SimpleNamespace(value="loaded"),
        )
        self.entries = types.SimpleNamespace(
            # Provider polling must not even enumerate the other entries.
            async_entries=Mock(
                side_effect=AssertionError("Borrowed integration state")
            ),
            async_get_entry=Mock(return_value=self.target),
            async_set_disabled_by=AsyncMock(return_value=True),
            async_update_entry=lambda entry, data: setattr(entry, "data", data),
        )
        self.hass = types.SimpleNamespace(
            config_entries=self.entries,
            data={},
            bus=types.SimpleNamespace(async_fire=Mock()),
            async_add_executor_job=AsyncMock(),
        )
        self.g = guard.Guard(self.hass, own_entry())
        self.g.inventory_refresh = AsyncMock()
        self.g.provider_signals_refresh = AsyncMock()

    def row(self):
        return {
            "config": {
                "id": "qg_github_own",
                "name": "Own GitHub",
                "provider_id": "own",
                "entry_ids": [],
                "kind": "rate_limit",
                "unit": "requests",
                "source": "https://api.github.com/rate_limit",
                "max_age": 180,
                "enforce": False,
            },
            "sample": {
                "used": 99,
                "limit": 100,
                "period": "window",
                "timestamp": dt.utcnow().isoformat(),
            },
        }

    async def test_unconfigured_provider_never_queries_other_integrations(self):
        with patch.object(guard, "async_get_clientsession") as session:
            await self.g.github_samples(dt.utcnow())
            await self.g.google_samples()
        session.assert_not_called()
        self.hass.async_add_executor_job.assert_not_awaited()
        self.entries.async_entries.assert_not_called()

    async def test_github_uses_only_own_token_and_is_monitor_only(self):
        self.g.entry = own_entry(
            {"own": {"type": "github", "name": "Own", "access_token": "own-secret"}}
        )
        response = Mock(headers={})
        response.json = AsyncMock(
            return_value={
                "resources": {"core": {"limit": 100, "used": 99, "reset": 123}}
            }
        )
        request = AsyncMock()
        request.__aenter__.return_value = response
        session = Mock()
        session.get.return_value = request
        with patch.object(guard, "async_get_clientsession", return_value=session):
            await self.g.github_samples(dt.utcnow())
        self.assertEqual(
            session.get.call_args.kwargs["headers"]["Authorization"],
            "Bearer own-secret",
        )
        metric = self.g.data["metrics"]["qg_github_own"]
        self.assertFalse(metric["config"]["enforce"])
        self.assertEqual(metric["config"]["entry_ids"], [])
        await self.g.evaluate_metrics()
        self.entries.async_set_disabled_by.assert_not_awaited()
        self.assertNotIn("own-secret", json.dumps(self.g.snapshot()))
        self.entries.async_entries.assert_not_called()

    async def test_google_only_passes_own_credentials_and_explicit_services(self):
        credential = {"project_id": "own-project", "private_key": "own-secret"}
        self.g.entry = own_entry(
            {
                "own": {
                    "type": "google",
                    "name": "Own",
                    "service_account_info": credential,
                    "services": ["google"],
                }
            }
        )
        self.hass.async_add_executor_job.return_value = [self.row()]
        await self.g.google_samples()
        accounts = self.hass.async_add_executor_job.call_args.args[1]
        self.assertEqual(accounts[0]["service_account_info"], credential)
        self.assertEqual(accounts[0]["services"], ["google"])
        self.entries.async_entries.assert_not_called()
        self.assertNotIn("own-secret", json.dumps(self.g.snapshot()))

    async def test_cutoff_requires_explicit_configure_and_poll_preserves_it(self):
        row = self.row()
        await self.g.apply_provider_row(copy.deepcopy(row))
        metric = self.g.data["metrics"][row["config"]["id"]]
        # Even a stale/bad enforcement flag cannot bypass administrator consent.
        metric["config"].update(enforce=True, entry_ids=["target"])
        await self.g.evaluate_metrics()
        self.entries.async_set_disabled_by.assert_not_awaited()
        config = {k: v for k, v in metric["config"].items() if k != "provider_id"}
        await self.g.configure(types.SimpleNamespace(data=guard.SCHEMA(config)))
        self.entries.async_set_disabled_by.assert_awaited_once_with("target", "user")
        await self.g.apply_provider_row(row)
        metric = self.g.data["metrics"][config["id"]]
        self.assertTrue(metric["config"]["enforce"])
        self.assertTrue(metric["cutoff_authorized"])
        self.assertEqual(metric["config"]["entry_ids"], ["target"])
        config.pop("enforce")
        await self.g.configure(types.SimpleNamespace(data=guard.SCHEMA(config)))
        self.assertFalse(self.g.data["metrics"][config["id"]]["config"]["enforce"])

    async def test_upgrade_retires_implicit_sources_and_preserves_manual_history(self):
        manual = self.row()
        manual["config"].pop("provider_id")
        manual["config"].update(
            id="manual", source="sensor.usage", enforce=True, entry_ids=["target"]
        )
        manual["points"] = [{"used": 42, "source_at": "earlier"}]
        legacy = copy.deepcopy(manual)
        legacy["config"].update(id="google_old", builtin_name=True)
        self.g.store.async_load.return_value = {
            "metrics": {"manual": manual, "google_old": legacy},
            "events": [{"message": "retained"}],
        }
        await self.g.load()
        self.assertFalse(manual["config"]["enforce"])
        self.assertFalse(manual["cutoff_authorized"])
        self.assertEqual(manual["points"], [{"used": 42, "source_at": "earlier"}])
        self.assertNotIn("google_old", self.g.data["metrics"])
        self.assertEqual(
            self.g.data["retired_metrics"]["google_old"]["points"],
            [{"used": 42, "source_at": "earlier"}],
        )
        await self.g.evaluate_metrics()
        self.entries.async_set_disabled_by.assert_not_awaited()
        self.assertEqual(self.g.data["events"][0]["message"], "retained")
        # Subsequent restarts retain new explicit consent instead of resetting it.
        manual["config"]["enforce"] = manual["cutoff_authorized"] = True
        self.g.store.async_load.return_value = copy.deepcopy(self.g.data)
        await self.g.load()
        self.assertTrue(self.g.data["metrics"]["manual"]["config"]["enforce"])

    async def test_remove_provider_erases_credentials_and_retires_metric(self):
        self.g.entry = own_entry(
            {"own": {"type": "github", "access_token": "own-secret"}}
        )
        await self.g.apply_provider_row(self.row())
        await self.g.remove_provider("own")
        self.assertEqual(self.g.providers, {})
        self.assertEqual(self.g.data["metrics"], {})
        self.assertIn("qg_github_own", self.g.data["retired_metrics"])
        self.assertNotIn("own-secret", json.dumps(self.g.data))
