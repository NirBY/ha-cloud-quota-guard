"""English fallback and Hebrew notifications share the dashboard catalog."""

import importlib.util
import unittest
from pathlib import Path

path = (
    Path(__file__).resolve().parents[1]
    / "custom_components/quota_guard/localization.py"
)
spec = importlib.util.spec_from_file_location("quota_localization", path)
localization = importlib.util.module_from_spec(spec)
spec.loader.exec_module(localization)


class LocalizationTests(unittest.TestCase):
    def test_notification_language_and_interpolation(self):
        message = "{name}: detected usage {percent:.2f}% — threshold {threshold}%"
        values = {"name": "My quota", "percent": 90.123, "threshold": 90}
        self.assertEqual(
            localization.translate(message, **values),
            "My quota: detected usage 90.12% — threshold 90%",
        )
        self.assertIn(
            "זוהה שימוש של 90.12%", localization.translate(message, "he", **values)
        )
        self.assertEqual(
            localization.translate("Cloud service quotas", "iw-IL"), "מכסות שירותים"
        )
        self.assertEqual(
            localization.translate("Cloud service quotas", "fr"), "Cloud service quotas"
        )
