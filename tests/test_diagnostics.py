"""Explain real failure causes without flagging valid monitoring as a bad config."""

import copy
import importlib.util
import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "quota_diagnostics", ROOT / "custom_components/quota_guard/diagnostics.py"
)
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 14, 12, tzinfo=UTC)
        self.metric = {
            "config": {
                "id": "sensor_source",
                "name": "Source",
                "entry_ids": [],
                "max_age": 900,
                "enforce": False,
                "usage_entity": "sensor.usage",
            },
            "sample": {
                "used": 43.65,
                "limit": 100,
                "period": "2026-11",
                "timestamp": self.now.isoformat(),
            },
        }
        self.states = {"sensor.usage": SimpleNamespace(state="43.65")}

    def issues(self):
        return diagnostics.metric_issues(self.metric, self.states, {}, self.now)

    def test_valid_monitor_only_and_unknown_allowance_are_not_configuration_errors(
        self,
    ):
        self.assertEqual(self.issues(), [])
        self.metric["sample"].pop("limit")
        self.assertEqual(self.issues(), [])

    def test_missing_entity_identifies_field_without_inventing_zero(self):
        self.metric["config"]["limit_entity"] = "sensor.removed"
        issue = self.issues()[0]
        self.assertEqual(issue["code"], "entity_missing")
        self.assertEqual(issue["field"], "limit_entity")
        self.assertEqual(issue["details"]["entity_id"], "sensor.removed")
        self.assertEqual(self.metric["sample"]["used"], 43.65)

    def test_missing_attribute_and_invalid_values_have_distinct_remedies(self):
        self.metric["config"]["usage_attribute"] = "bad_attribute"
        self.metric["sample"]["used"] = None
        self.assertEqual(self.issues()[0]["details"]["attribute"], "bad_attribute")
        self.metric["sample"]["used"] = "not a number"
        self.assertEqual(self.issues()[0]["code"], "usage_invalid")
        self.metric["sample"]["used"] = 10
        self.metric["sample"]["limit"] = 0
        self.assertEqual(self.issues()[0]["code"], "limit_invalid")

    def test_timestamp_diagnostics_distinguish_missing_timezone_future_and_age(self):
        for timestamp, code in [
            (None, "timestamp_missing"),
            ("bad", "timestamp_invalid"),
            ("2026-11-14T12:00:00", "timestamp_timezone"),
            ((self.now + timedelta(hours=1)).isoformat(), "timestamp_future"),
            ((self.now - timedelta(hours=1)).isoformat(), "measurement_stale"),
        ]:
            with self.subTest(code=code):
                self.metric["sample"]["timestamp"] = timestamp
                self.assertEqual(self.issues()[0]["code"], code)
        self.assertEqual(self.issues()[0]["details"]["max_age_seconds"], 900)

    def test_previous_month_and_missing_period_are_explicit(self):
        self.metric["sample"]["period"] = None
        self.assertIn("period_missing", [x["code"] for x in self.issues()])
        self.metric["config"]["period_mode"] = "monthly"
        self.metric["sample"]["timestamp"] = "2026-10-31T23:59:00+00:00"
        self.assertIn("previous_month", [x["code"] for x in self.issues()])

    def test_provider_failure_does_not_expose_raw_exception_or_invent_timestamp_issue(
        self,
    ):
        self.metric["sample"] = {}
        self.metric.update(
            provider_error="secret token in raw exception",
            provider_error_code="authentication_failed",
            provider_http_status=401,
        )
        result = self.issues()
        self.assertEqual([x["code"] for x in result], ["authentication_failed"])
        self.assertNotIn("secret token", json.dumps(result))

    def test_fresh_empty_provider_report_is_not_bad_configuration(self):
        self.metric["sample"] = {}
        self.metric.update(
            verified_limit=100,
            reporting={
                "state": "no_activity_reported",
                "checked_at": self.now.isoformat(),
            },
        )
        self.assertEqual(self.issues(), [])

    def test_target_and_consent_failures_are_actionable(self):
        self.metric["config"].update(enforce=True, entry_ids=["gone"])
        self.assertEqual(
            {x["code"] for x in self.issues()},
            {"target_missing", "cutoff_not_authorized"},
        )

    def test_legacy_sources_require_attention_but_intentional_removals_do_not(self):
        retired = copy.deepcopy(self.metric)
        retired["config"]["id"] = "google_legacy"
        self.assertEqual(
            diagnostics.retired_issues(retired)[0]["code"], "credentials_required"
        )
        retired["acknowledged"] = True
        self.assertEqual(diagnostics.retired_issues(retired), [])
        retired.pop("acknowledged")
        retired["config"]["provider_id"] = "own"
        self.assertEqual(diagnostics.retired_issues(retired), [])

    def test_integration_setup_failure_is_separate_from_missing_coverage(self):
        self.assertEqual(
            diagnostics.connection_issues(
                {"state": "loaded", "coverage": "needs_setup"}
            ),
            [],
        )
        self.assertEqual(
            diagnostics.connection_issues({"state": "not_loaded", "disabled": True}), []
        )
        self.assertEqual(
            diagnostics.connection_issues({"state": "setup_retry"})[0]["code"],
            "integration_failed",
        )

    def test_every_explanation_and_remedy_has_hebrew_translation(self):
        import ast

        catalog = json.loads(
            (ROOT / "custom_components/quota_guard/languages/he.json").read_text(
                encoding="utf-8"
            )
        )
        tree = ast.parse(Path(diagnostics.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "issue"
            ):
                for value in node.args[1:3]:
                    if isinstance(value, ast.Constant):
                        self.assertIn(value.value, catalog)
