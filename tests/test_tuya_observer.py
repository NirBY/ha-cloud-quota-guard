"""Passive observation must preserve HTTP behavior and keep secrets out of storage."""

import importlib.util
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

path = (
    Path(__file__).resolve().parents[1]
    / "custom_components/quota_guard/tuya_observer.py"
)
spec = importlib.util.spec_from_file_location("tuya_observer", path)
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


class ObserverTests(unittest.TestCase):
    def test_pass_through_count_errors_and_restore(self):
        response = SimpleNamespace(status_code=429)
        original = Mock(return_value=response)
        session = SimpleNamespace(request=original)
        counter = observer.RequestCounter(session)
        self.assertIs(
            session.request("GET", "secret-url", headers={"token": "private"}), response
        )
        original.assert_called_once_with(
            "GET", "secret-url", headers={"token": "private"}
        )
        snapshot = counter.snapshot()
        self.assertEqual(snapshot["sample"]["used"], 1)
        self.assertEqual(snapshot["observation"]["rate_limited"], 1)
        self.assertNotIn("private", str(snapshot))
        original.side_effect = RuntimeError("upstream")
        with self.assertRaisesRegex(RuntimeError, "upstream"):
            session.request("POST", "secret")
        self.assertEqual(counter.snapshot()["sample"]["used"], 2)
        self.assertEqual(counter.snapshot()["observation"]["errors"], 2)
        counter.close()
        self.assertIs(session.request, original)

    def test_thread_safe_and_daily_reset(self):
        session = SimpleNamespace(request=lambda *a: SimpleNamespace(status_code=200))
        counter = observer.RequestCounter(session)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: session.request(), range(100)))
        self.assertEqual(counter.snapshot()["sample"]["used"], 100)
        counter.period = "old period"
        self.assertEqual(counter.snapshot()["sample"]["used"], 0)

    def test_attach_once_reload_and_unload_without_extra_calls(self):
        request = Mock(return_value=SimpleNamespace(status_code=200))
        session = SimpleNamespace(request=request)
        entry = SimpleNamespace(
            domain="tuya",
            entry_id="ABC",
            state=SimpleNamespace(value="loaded"),
            runtime_data=SimpleNamespace(
                manager=SimpleNamespace(customer_api=SimpleNamespace(session=session))
            ),
        )
        adapter = observer.TuyaObserver()
        row = adapter.collect([entry], {})[0]
        self.assertEqual(row["sample"]["used"], 0)
        self.assertFalse(row["config"]["enforce"])
        request.assert_not_called()
        session.request()
        self.assertEqual(adapter.collect([entry], {})[0]["sample"]["used"], 1)
        adapter.collect([], {})
        self.assertIs(session.request, request)
