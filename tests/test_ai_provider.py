"""Provider contract fixtures: amounts, scope, pagination, freshness and secrets."""

import types
import unittest
from datetime import UTC, datetime
from importlib import import_module
from unittest.mock import Mock, patch

from test_guard import guard

provider_module = import_module(guard.__name__ + ".ai_provider")
AIProvider = provider_module.AIProvider
account_report = provider_module.account_report
gemini_report = provider_module.gemini_report
number = provider_module.number

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)


def session_for(*bodies):
    session = Mock()
    session.request.side_effect = [
        Mock(status_code=200, json=Mock(return_value=body)) for body in bodies
    ]
    return session


def cost_bucket(value, currency="usd"):
    return {
        "start_time": int(NOW.replace(day=1).timestamp()),
        "results": [{"amount": {"value": value, "currency": currency}}],
    }


class ProviderTests(unittest.TestCase):
    def test_openai_sums_all_pages_and_empty_is_unknown(self):
        session = session_for(
            {"data": [cost_bucket("1.12")], "has_more": True, "next_page": "two"},
            {"data": [cost_bucket("2.23")], "has_more": False},
        )
        result = account_report({"type": "openai", "api_key": "test-key"}, session, NOW)
        self.assertEqual(result["used"], 3.35)
        self.assertEqual(result["expires_at"], "2026-12-01T00:00:00+00:00")
        self.assertEqual(session.request.call_args.kwargs["params"]["page"], "two")
        self.assertFalse(session.request.call_args.kwargs["allow_redirects"])
        self.assertIsNone(
            account_report(
                {"type": "openai", "api_key": "test-key"},
                session_for({"data": [], "has_more": False}),
                NOW,
            )
        )

    def test_invalid_currency_and_incomplete_pagination_rejected(self):
        for body in (
            {"data": [cost_bucket(1, "EUR")], "has_more": False},
            {"data": [cost_bucket(1)], "has_more": True},
        ):
            with self.assertRaises(ValueError):
                account_report(
                    {"type": "openai", "api_key": "test-key"}, session_for(body), NOW
                )

    def test_claude_fractional_cents_use_snapshot_timestamp(self):
        body = {
            "data": [{"cost": {"amount": "123.45", "currency": "USD"}}],
            "has_more": False,
            "data_refreshed_at": "2026-11-14T09:00:00Z",
        }
        result = account_report(
            {"type": "claude", "api_key": "test-key"}, session_for(body), NOW
        )
        self.assertEqual(result["used"], 1.2345)
        self.assertEqual(result["timestamp"], "2026-11-14T09:00:00+00:00")
        body["data_refreshed_at"] = None
        with self.assertRaises(ValueError):
            account_report(
                {"type": "claude", "api_key": "test-key"}, session_for(body), NOW
            )

    def test_cursor_billing_cycle_and_on_demand_only(self):
        cycle = int(datetime(2026, 10, 28, tzinfo=UTC).timestamp() * 1000)
        body = {
            "subscriptionCycleStart": cycle,
            "totalPages": 1,
            "totalMembers": 2,
            "teamMemberSpend": [
                {"userId": "a", "spendCents": 125.5, "overallSpendCents": 9999},
                {"userId": "b", "spendCents": 250},
            ],
        }
        result = account_report(
            {"type": "cursor", "api_key": "test-key"}, session_for(body), NOW
        )
        self.assertEqual(result["used"], 3.755)
        self.assertIn("2026-10-28", result["period"])
        body["totalMembers"] = 3
        with self.assertRaises(ValueError):
            account_report(
                {"type": "cursor", "api_key": "test-key"}, session_for(body), NOW
            )

    def test_xai_partial_reports_never_become_measurements(self):
        provider = {"type": "grok", "api_key": "test-key", "team_id": "team_1"}
        body = {
            "limitReached": False,
            "timeSeries": [
                {"dataPoints": [{"timestamp": "2026-11-13T00:00:00Z", "values": [1.5]}]}
            ],
        }
        self.assertEqual(account_report(provider, session_for(body), NOW)["used"], 1.5)
        body["limitReached"] = True
        with self.assertRaises(ValueError):
            account_report(provider, session_for(body), NOW)

    def test_openrouter_uses_key_month_only_not_lifetime_or_byok(self):
        body = {
            "data": {
                "usage_monthly": 12.5,
                "usage": 300,
                "byok_usage_monthly": 90,
                "limit": 500,
            }
        }
        result = account_report(
            {"type": "openrouter", "api_key": "test-key"}, session_for(body), NOW
        )
        self.assertEqual(result["used"], 12.5)
        self.assertNotIn("limit", result)

    def test_mistral_full_aggregate_not_group_page_or_mixed_usage_values(self):
        start = NOW.replace(day=1, hour=0).isoformat()
        body = {
            "aggregates": {"total_api_calls": 123, "total_value": 999999},
            "groups": [],
            "metadata": {
                "has_more": True,
                "query_params": {"start": start, "end": NOW.isoformat()},
            },
        }
        result = account_report(
            {"type": "mistral", "api_key": "test-key"}, session_for(body), NOW
        )
        self.assertEqual(result["used"], 123)
        body["aggregates"]["total_api_calls"] = None
        with self.assertRaises(ValueError):
            account_report(
                {"type": "mistral", "api_key": "test-key"}, session_for(body), NOW
            )

    def test_deepseek_is_balance_with_no_allowance_and_poll_is_throttled(self):
        import requests

        session = session_for(
            {
                "balance_infos": [
                    {"currency": "USD", "total_balance": "7.50"},
                    {"currency": "CNY", "total_balance": "8"},
                ]
            }
        )
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        provider = {
            "id": "deepseek1",
            "type": "deepseek",
            "name": "Demo",
            "api_key": "test-key",
            "currency": "USD",
            "limit": 100,
        }
        reader = AIProvider()
        with patch.object(requests, "Session", return_value=session):
            row = reader.fetch(provider)[0]
            self.assertEqual(reader.fetch(provider), [])
        self.assertEqual(row["sample"]["used"], 7.5)
        self.assertEqual(row["config"]["measurement"], "balance")
        self.assertNotIn("limit", row["config"])
        self.assertIsNone(row["sample"]["limit"])
        self.assertFalse(row["config"]["enforce"])

    def test_failures_are_sanitized_and_redirects_not_accepted(self):
        import requests

        session = session_for({})
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        session.request.side_effect = requests.HTTPError(
            "secret-key body", response=Mock(status_code=403)
        )
        with patch.object(requests, "Session", return_value=session):
            row = AIProvider().fetch(
                {
                    "id": "one",
                    "type": "openrouter",
                    "name": "Demo",
                    "api_key": "secret-key",
                }
            )[0]
        self.assertEqual(row["provider_error_code"], "permission_denied")
        self.assertNotIn("sample", row)
        self.assertNotIn("secret-key", str(row))
        response = Mock(status_code=302)
        session.request.side_effect = None
        session.request.return_value = response
        with self.assertRaises(ValueError):
            account_report(
                {"type": "openrouter", "api_key": "secret-key"}, session, NOW
            )
        response.json.assert_not_called()

    def test_numbers_reject_invalid_values(self):
        for value in (None, True, "NaN", "Infinity", "-1", "1e9999"):
            with self.assertRaises((ValueError, ArithmeticError)):
                number(value)

    def test_gemini_output_token_count_only_and_delta_required(self):
        import sys

        metric = "generativelanguage.googleapis.com/generate_content_usage_output_token_count"
        series = {
            "metricKind": "DELTA",
            "metric": {"type": metric},
            "points": [
                {
                    "interval": {"endTime": NOW.isoformat()},
                    "value": {"int64Value": "42"},
                }
            ],
        }
        session = session_for({"timeSeries": [series]})
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock()
        auth = types.ModuleType("google.auth.transport.requests")
        auth.AuthorizedSession = Mock(return_value=session)
        oauth = types.ModuleType("google.oauth2")
        oauth.service_account = types.SimpleNamespace(
            Credentials=types.SimpleNamespace(from_service_account_info=Mock())
        )
        with patch.dict(
            sys.modules,
            {"google.auth.transport.requests": auth, "google.oauth2": oauth},
        ):
            result = gemini_report(
                {"service_account_info": {"project_id": "demo-project"}}, NOW
            )
        self.assertEqual(result["used"], 42)
        self.assertEqual(result["period"], "2026-11 UTC")
        self.assertEqual(result["expires_at"], "2026-12-01T00:00:00+00:00")
