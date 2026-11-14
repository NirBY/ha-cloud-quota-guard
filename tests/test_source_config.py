"""Sensor sources preserve provider freshness and explicit allowance semantics."""

import importlib.util
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import voluptuous as vol

spec = importlib.util.spec_from_file_location(
    "source_config",
    Path(__file__).resolve().parents[1]
    / "custom_components/quota_guard/source_config.py",
)
source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)


class SourceTests(unittest.TestCase):
    def test_daily_screen_time_expires_at_local_midnight_and_never_relabels_yesterday(
        self,
    ):
        self.config.update(
            period_mode="daily",
            period_timezone="Asia/Jerusalem",
            period_attribute="date",
            timestamp_mode="state_reported",
        )
        self.states["sensor.usage"].last_reported = self.now
        self.states["sensor.usage"].attributes["date"] = "2026-11-13"
        sample = source.sensor_sample(self.config, self.states, self.now)
        self.assertEqual(sample["period"], "2026-11-13")
        self.assertEqual(sample["expires_at"], "2026-11-14T00:00:00+02:00")
        self.assertEqual(sample["timestamp"], self.now.isoformat())
        later = datetime(2026, 11, 13, 22, tzinfo=UTC)
        self.assertIsNone(
            source.sensor_sample(self.config, self.states, later)["period"]
        )

    def setUp(self):
        self.now = datetime(2026, 11, 13, 8, tzinfo=UTC)
        self.config = {"usage_entity": "sensor.usage", "limit": 100}
        self.states = {
            "sensor.usage": SimpleNamespace(
                state="39.914",
                attributes={
                    "checked_at": self.now.isoformat(),
                    "period": "2026-11",
                    "requests": 42,
                },
            )
        }

    def test_attribute_timestamp_never_falls_back_to_current_time(self):
        sample = source.sensor_sample(self.config, self.states, self.now)
        self.assertIsNone(sample["timestamp"])
        self.config["timestamp_attribute"] = "checked_at"
        self.assertEqual(
            source.sensor_sample(self.config, self.states, self.now)["timestamp"],
            self.now.isoformat(),
        )

    def test_separate_sensors_and_usage_attribute(self):
        self.config.update(
            usage_attribute="requests",
            period_entity="sensor.period",
            timestamp_entity="sensor.stamp",
            limit_entity="sensor.limit",
        )
        self.states.update(
            {
                "sensor.period": SimpleNamespace(state="window-7"),
                "sensor.stamp": SimpleNamespace(state=self.now.isoformat()),
                "sensor.limit": SimpleNamespace(state="200"),
            }
        )
        self.assertEqual(
            source.sensor_sample(self.config, self.states, self.now),
            {
                "used": 42,
                "limit": "200",
                "period": "window-7",
                "timestamp": self.now.isoformat(),
            },
        )
        del self.states["sensor.limit"]
        self.assertIsNone(
            source.sensor_sample(self.config, self.states, self.now)["limit"]
        )
        self.states["sensor.period"].state = "unavailable"
        self.assertIsNone(
            source.sensor_sample(self.config, self.states, self.now)["period"]
        )

    def test_month_is_explicit_and_never_relabels_old_usage_after_reset(self):
        self.config.update(
            period_mode="monthly",
            period_timezone="UTC",
            timestamp_attribute="checked_at",
        )
        self.assertEqual(
            source.sensor_sample(self.config, self.states, self.now)["period"],
            "2026-11",
        )
        self.assertIsNone(
            source.sensor_sample(
                self.config, self.states, datetime(2026, 12, 1, tzinfo=UTC)
            )["period"]
        )
        self.states["sensor.usage"].attributes["checked_at"] = "2026-11-13T08:00:00"
        self.assertIsNone(
            source.sensor_sample(self.config, self.states, self.now)["period"]
        )

    def test_usage_links_reject_executable_and_credential_urls(self):
        for url in (
            "/security-scanner/usage",
            "https://example.com/usage",
            "http://localhost:8123/usage",
        ):
            self.assertEqual(source.usage_url(url), url)
        for url in (
            "javascript:alert(1)",
            "//evil.example/path",
            "/\\evil.example",
            "https://user:secret@example.com/",
            "https://[",
            "https://exa\nmple.com/",
        ):
            with self.subTest(url=url), self.assertRaises(vol.Invalid):
                source.usage_url(url)

    def test_period_timezone_validation(self):
        self.assertEqual(source.period_timezone("UTC"), "UTC")
        with self.assertRaises(vol.Invalid):
            source.period_timezone("Not/A_Zone")
