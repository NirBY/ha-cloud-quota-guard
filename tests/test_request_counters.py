"""Real HTTP tracing: retries/redirects, isolation, reloads and daily rollover."""

import unittest
from datetime import UTC, datetime, timedelta
from importlib import import_module
from types import SimpleNamespace

from aiohttp import ClientSession, web
from test_guard import guard

counters_module = import_module(guard.__name__ + ".request_counters")
RequestCounters = counters_module.RequestCounters


class RequestCounterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = datetime(2026, 11, 14, 12, tzinfo=UTC)
        self.hass = SimpleNamespace(data={})
        self.data = {}
        self.counters = RequestCounters(self.hass, self.data, lambda: self.now)
        self.session = ClientSession()
        self.entry = SimpleNamespace(
            entry_id="entry",
            domain="example",
            disabled_by=None,
            state=SimpleNamespace(value="loaded"),
            runtime_data=SimpleNamespace(quota_guard_sessions=(self.session,)),
        )
        app = web.Application()

        async def ok(_request):
            return web.Response(text="private payload")

        async def redirect(_request):
            raise web.HTTPFound("/ok?private=secret")

        async def error(_request):
            return web.Response(status=429)

        app.router.add_get("/ok", ok)
        app.router.add_get("/redirect", redirect)
        app.router.add_get("/error", error)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = "http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1])

    async def asyncTearDown(self):
        self.counters.close()
        await self.session.close()
        await self.runner.cleanup()

    async def get(self, path, session=None):
        async with (session or self.session).get(self.url + path) as response:
            await response.read()

    def used(self):
        return list(self.counters.rows())[0]["sample"]["used"]

    async def test_first_install_redirect_retry_errors_and_privacy(self):
        self.counters.refresh([self.entry])
        self.assertEqual(self.used(), 0)
        await self.get("/redirect")  # two actual HTTP requests
        await self.get("/error")
        await self.get("/error")  # application retry counted separately
        self.assertEqual(self.used(), 4)
        saved = repr(self.data)
        for private in ("private", "secret", "payload", "127.0.0.1", "headers"):
            self.assertNotIn(private, saved)
        row = list(self.counters.rows())[0]
        self.assertTrue(row["counter"]["partial_period"])
        self.assertNotIn("limit", row["sample"])
        self.assertFalse(row["config"]["enforce"])

    async def test_discovery_is_idempotent_and_leaves_other_clients_untouched(self):
        self.counters.refresh([])
        await self.get("/ok")
        self.counters.refresh([self.entry])
        self.counters.refresh([self.entry])
        self.assertEqual(len(self.session.trace_configs), 1)
        async with ClientSession() as unrelated:
            await self.get("/ok", unrelated)
            self.assertEqual(len(unrelated.trace_configs), 0)
        self.assertEqual(self.used(), 0)
        await self.get("/ok")
        self.assertEqual(self.used(), 1)
        self.counters.refresh([])
        self.assertEqual(len(self.session.trace_configs), 0)
        await self.get("/ok")
        self.assertEqual(self.data["request_counters"]["entry"]["used"], 1)

    async def test_reloaded_client_reconnects_without_double_counting(self):
        self.counters.refresh([self.entry])
        await self.get("/ok")
        async with ClientSession() as replacement:
            self.entry.runtime_data.quota_guard_sessions = (replacement,)
            self.counters.refresh([self.entry])
            await self.get("/ok")  # obsolete session no longer attributed
            await self.get("/ok", replacement)
            self.assertEqual(self.used(), 2)
        self.counters.refresh([self.entry])
        self.assertNotIn("sample", list(self.counters.rows())[0])

    async def test_restart_retains_counts_and_utc_midnight_resets(self):
        self.counters.refresh([self.entry])
        await self.get("/ok")
        self.counters.close()
        self.counters = RequestCounters(self.hass, self.data, lambda: self.now)
        self.counters.refresh([self.entry])
        await self.get("/ok")
        self.assertEqual(self.used(), 2)
        self.now += timedelta(days=1)
        self.assertEqual(self.used(), 0)
        await self.get("/ok")
        self.assertEqual(self.used(), 1)

    async def test_off_disabled_and_unknown_integrations_do_not_fabricate_usage(self):
        unknown = SimpleNamespace(
            entry_id="unknown", domain="marketplace", runtime_data=None
        )
        self.counters.refresh([unknown])
        self.assertEqual(list(self.counters.rows()), [])
        self.data["automatic_request_counters"] = False
        self.counters.refresh([self.entry])
        self.assertEqual(list(self.counters.rows()), [])
        self.data["automatic_request_counters"] = True
        self.entry.disabled_by = "user"
        self.counters.refresh([self.entry])
        self.assertEqual(list(self.counters.rows()), [])

    async def test_shared_session_is_not_attributed_to_multiple_entries(self):
        others = [
            SimpleNamespace(**{**vars(self.entry), "entry_id": str(i)})
            for i in range(3)
        ]
        self.counters.refresh(others)
        self.assertEqual(len(self.session.trace_configs), 0)
        self.assertEqual(list(self.counters.rows()), [])

    async def test_domains_never_grant_access_to_private_clients(self):
        class PublicInterfaceOnly:
            def __getattr__(self, name):
                if name == "quota_guard_sessions":
                    raise AttributeError(name)
                raise AssertionError("Private runtime attributes must not be inspected")

        class NoHassData:
            def get(self, *args):
                raise AssertionError("Other integration data must not be inspected")

        self.hass.data = NoHassData()
        for domain in (
            "mashov",
            "life360",
            "xiaomi_cloud_map_extractor",
            "meitav_tariff_sync",
            "unrelated",
        ):
            with self.subTest(domain=domain):
                self.entry.domain = domain
                self.entry.runtime_data = PublicInterfaceOnly()
                self.assertIsNone(counters_module.sessions_for(self.entry))
                self.counters.refresh([self.entry])
                self.assertEqual(list(self.counters.rows()), [])
                self.assertEqual(self.session.trace_configs, [])

    async def test_empty_opt_in_is_visible_then_counts_when_its_client_opens(self):
        self.entry.runtime_data.quota_guard_sessions = ()
        self.counters.refresh([self.entry])
        row = list(self.counters.rows())[0]
        self.assertEqual(row["counter"]["state"], "waiting_for_client")
        self.assertNotIn("sample", row)
        self.assertFalse(row["config"]["enforce"])
        self.entry.runtime_data.quota_guard_sessions = (self.session,)
        self.counters.refresh([self.entry])
        await self.get("/ok")
        self.assertEqual(self.used(), 1)

    async def test_withdrawing_opt_in_detaches_and_preserves_history(self):
        self.counters.refresh([self.entry])
        await self.get("/ok")
        del self.entry.runtime_data.quota_guard_sessions
        self.counters.refresh([self.entry])
        self.assertEqual(self.session.trace_configs, [])
        row = list(self.counters.rows())[0]
        self.assertEqual(row["counter"]["state"], "not_opted_in")
        self.assertNotIn("sample", row)
        await self.get("/ok")
        self.assertEqual(self.data["request_counters"]["entry"]["used"], 1)

    async def test_legacy_counter_without_observations_is_retained_as_paused(self):
        self.entry.runtime_data = None
        self.data["metrics"] = {
            counters_module.metric_id("entry"): {"config": {"automatic_counter": True}}
        }
        self.counters.refresh([self.entry])
        row = list(self.counters.rows())[0]
        self.assertEqual(row["counter"]["state"], "not_opted_in")
        self.assertNotIn("sample", row)
        self.assertEqual(self.session.trace_configs, [])
