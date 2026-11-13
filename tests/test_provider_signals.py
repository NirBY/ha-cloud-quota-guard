"""Actual OP1013 setup errors must remain visible without a repair notice."""

import importlib.util
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location(
    "provider_signals",
    Path(__file__).resolve().parents[1]
    / "custom_components/quota_guard/provider_signals.py",
)
signals = importlib.util.module_from_spec(spec)
spec.loader.exec_module(signals)


class ProviderSignalTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 13, tzinfo=UTC)
        self.entry = SimpleNamespace(
            domain="imou_life",
            entry_id="imou",
            title="Imou Life Official",
            state=SimpleNamespace(value="setup_retry"),
            reason="Error fetching Imou devices: OP1013:Call interface times exceed limit (total).",
        )

    def test_setup_failure_without_repair_is_confirmed_and_stable(self):
        first = signals.signals_for({}, [self.entry], now=self.now)
        signal = next(iter(first.values()))
        self.assertTrue(signal["confirmed"])
        self.assertEqual(signal["expected_reset_date"], "2026-12-01")
        self.assertNotIn("percent", signal)
        self.assertEqual(
            first,
            signals.signals_for(
                {}, [self.entry], first, datetime(2026, 11, 14, tzinfo=UTC)
            ),
        )
        self.assertEqual(
            next(
                iter(
                    signals.signals_for(
                        {}, [self.entry], first, datetime(2026, 12, 1, tzinfo=UTC)
                    ).values()
                )
            )["expected_reset_date"],
            "2027-01-01",
        )

    def test_no_false_match_on_auth_network_other_code_or_loaded_entry(self):
        for reason in ("Timeout", "invalid credentials", "OP10130: unrelated"):
            self.entry.reason = reason
            self.assertEqual(signals.signals_for({}, [self.entry], now=self.now), {})
        self.entry.reason = "OP1013"
        self.entry.state.value = "loaded"
        self.assertEqual(signals.signals_for({}, [self.entry], now=self.now), {})

    def test_translated_error_and_restored_old_issue(self):
        self.entry.reason = None
        self.entry.error_reason_translation_placeholders = {
            "error": "OP1013:Call interface times exceed limit (total)."
        }
        issue = SimpleNamespace(
            active=False,
            translation_key=None,
            created=datetime(2026, 10, 1, tzinfo=UTC),
            learn_more_url=None,
        )
        issues = {("imou_life", signals.IMOU_QUOTA_KEY + "_imou"): issue}
        current = signals.signals_for(issues, [self.entry], now=self.now)
        self.assertEqual(
            next(iter(current.values()))["expected_reset_date"], "2026-12-01"
        )
        self.entry.state.value = "loaded"
        self.assertEqual(
            signals.signals_for(issues, [self.entry], current, self.now), {}
        )
