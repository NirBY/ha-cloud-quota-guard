"""Scope, confirmation and idempotence contracts; never call a real provider."""

import unittest
from importlib import import_module
from unittest.mock import MagicMock, patch

from test_guard import guard

controls = import_module(guard.__name__ + ".provider_controls")


class ControlTests(unittest.TestCase):
    def session(self, *bodies):
        session = MagicMock()
        session.__enter__.return_value = session
        session.__exit__.return_value = False
        session.request.side_effect = [
            MagicMock(status_code=200, json=lambda b=b: b) for b in bodies
        ]
        return session

    def test_claude_disables_only_selected_key_and_verifies_effective_status(self):
        session = self.session(
            {"id": "key", "status": "active"}, {}, {"id": "key", "status": "inactive"}
        )
        with patch.object(controls, "session_for", return_value=session):
            self.assertEqual(
                controls.stop({"type": "claude"}, ["key"], {}), {"key": "disabled"}
            )
        calls = session.request.call_args_list
        self.assertEqual([c.args[0] for c in calls], ["GET", "POST", "GET"])
        self.assertEqual(calls[1].kwargs["json"], {"status": "inactive"})
        self.assertTrue(all(c.kwargs["allow_redirects"] is False for c in calls))

    def test_all_targets_validate_before_any_mutation(self):
        session = self.session({"id": "first"}, {"id": "different"})
        with (
            patch.object(controls, "session_for", return_value=session),
            self.assertRaises(ValueError),
        ):
            controls.stop({"type": "claude"}, ["first", "second"], {})
        self.assertEqual(
            [c.args[0] for c in session.request.call_args_list], ["GET", "GET"]
        )

    def test_openai_permanent_revocation_requires_exact_confirmation(self):
        session = self.session({"id": "key"}, {"id": "key", "deleted": True})
        with patch.object(controls, "session_for", return_value=session):
            self.assertEqual(
                controls.stop({"type": "openai"}, ["proj/key"], {}),
                {"proj/key": "revoked"},
            )
        self.assertIn("/projects/proj/api_keys/key", session.request.call_args.args[1])
        with patch.object(controls, "session_for") as create:
            controls.stop({"type": "openai"}, ["proj/key"], {"proj/key": "revoked"})
            create.assert_not_called()

    def test_openrouter_disables_and_does_not_treat_request_acceptance_as_success(self):
        session = self.session(
            {"data": {"hash": "key", "disabled": False}},
            {},
            {"data": {"hash": "key", "disabled": False}},
        )
        with patch.object(controls, "session_for", return_value=session):
            result = controls.stop({"type": "openrouter"}, ["key"], {})
        self.assertEqual(result, {"key": "failed_or_unconfirmed"})
        self.assertEqual(
            session.request.call_args_list[1].kwargs["json"], {"disabled": True}
        )

    def test_google_cannot_disable_other_apis_or_dependencies(self):
        provider = {
            "type": "google",
            "service_account_info": {"project_id": "test-project"},
        }
        session = self.session()
        with (
            patch.object(controls, "session_for", return_value=session),
            self.assertRaises(ValueError),
        ):
            controls.stop(
                provider,
                ["monitoring.googleapis.com"],
                {},
                "calendar-json.googleapis.com",
            )
        session.request.assert_not_called()
        service = "calendar-json.googleapis.com"
        session = self.session(
            {"config": {"name": service}, "state": "ENABLED"},
            {"name": "operation"},
            {"config": {"name": service}, "state": "DISABLED"},
        )
        with patch.object(controls, "session_for", return_value=session):
            self.assertEqual(
                controls.stop(provider, [service], {}, service), {service: "disabled"}
            )
        self.assertFalse(
            session.request.call_args_list[1].kwargs["json"]["disableDependentServices"]
        )

    def test_cursor_checks_effective_limit_and_never_removes_a_limit(self):
        def body(limit):
            return {
                "teamMemberSpend": [
                    {
                        "email": "demo@example.invalid",
                        "effectivePerUserLimitDollars": limit,
                    }
                ],
                "totalPages": 1,
            }

        session = self.session(body(100), {"outcome": "success"}, body(100))
        with patch.object(controls, "session_for", return_value=session):
            result = controls.stop({"type": "cursor"}, ["demo@example.invalid"], {})
        self.assertEqual(result, {"demo@example.invalid": "failed_or_unconfirmed"})
        self.assertEqual(
            session.request.call_args_list[1].kwargs["json"]["spendLimitDollars"], 0
        )

    def test_mistral_verifies_deletion_and_retains_no_key_material(self):
        session = self.session(
            {
                "keys": [
                    {
                        "key_id": "key",
                        "name": "Demo",
                        "can_delete": True,
                        "key": "private",
                    }
                ]
            },
            {},
            {"keys": []},
        )
        with patch.object(controls, "session_for", return_value=session):
            self.assertEqual(
                controls.stop({"type": "mistral"}, ["key"], {}), {"key": "revoked"}
            )

    def test_grok_requires_configured_team(self):
        session = self.session(
            {"apiKeys": [{"apiKeyId": "key", "teamId": "other", "disabled": False}]}
        )
        with (
            patch.object(controls, "session_for", return_value=session),
            self.assertRaises(ValueError),
        ):
            controls.stop({"type": "grok", "team_id": "mine"}, ["key"], {})
        self.assertEqual([c.args[0] for c in session.request.call_args_list], ["GET"])

    def test_unsafe_target_ids_rejected(self):
        for value in (
            "https://evil.invalid/key",
            "../secret",
            "key?token=private",
            "",
            "key#fragment",
        ):
            with self.assertRaises(ValueError):
                controls.targets(value)
