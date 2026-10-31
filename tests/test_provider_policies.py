"""Published limits must retain API/mode scope without leaking account data."""

import importlib.util
import unittest
from pathlib import Path

path = (
    Path(__file__).resolve().parents[1]
    / "custom_components/quota_guard/provider_policies.py"
)
spec = importlib.util.spec_from_file_location("provider_policies", path)
policies = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policies)


class PolicyTests(unittest.TestCase):
    def test_tuya_sharing_is_not_given_cloud_project_limits(self):
        policy = policies.policy_for("tuya", {"user_code": "secret-value"})
        self.assertIn("Sharing SDK", policy["scope"])
        self.assertIn("מידע ייחוס", policy["scope"])
        self.assertNotIn("secret-value", str(policy))
        self.assertTrue(all("/v2.0/cloud/" in row["scope"] for row in policy["limits"]))

    def test_xiaomi_auto_does_not_claim_cloud_only_or_numeric_allowance(self):
        policy = policies.policy_for(
            "xiaomi_miot", {"conn_mode": "auto", "password": "private"}
        )
        self.assertEqual(policy["connection_mode"], "auto")
        self.assertIn("LAN", policy["scope"])
        self.assertEqual(policy["limits"], [])
        self.assertNotIn("private", str(policy))
        self.assertEqual(
            policies.policy_for(
                "xiaomi_miot", {"conn_mode": "auto"}, {"conn_mode": "local"}
            )["connection_mode"],
            "local",
        )

    def test_telegram_sizes_and_group_scope(self):
        policy = policies.policy_for("telegram_bot")
        limits = {row["name"]: row for row in policy["limits"]}
        self.assertEqual(limits["העלאת תמונה"]["value"], "10")
        self.assertEqual(limits["העלאת קובץ אחר"]["value"], "50")
        self.assertEqual(limits["שליחה לקבוצה"]["scope"], "לכל קבוצה")
        self.assertEqual(limits["שידור חינמי כולל"]["value"], "≈30")
        self.assertNotIn(
            "https://local.example",
            str(
                policies.policy_for(
                    "telegram_bot", {"api_endpoint": "https://local.example"}
                )
            ),
        )

    def test_unlisted_provider_has_no_invented_policy(self):
        self.assertIsNone(policies.policy_for("other"))
