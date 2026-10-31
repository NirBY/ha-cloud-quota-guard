import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "google_provider",
    Path(__file__).resolve().parents[1]
    / "custom_components/quota_guard/google_provider.py",
)
provider = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provider)


def series(points, metric="m"):
    return {
        "metric": {"labels": {"quota_metric": metric}},
        "metricKind": "DELTA",
        "points": [
            {"interval": {"endTime": t}, "value": {"int64Value": str(v)}}
            for t, v in points
        ],
    }


class GoogleTests(unittest.TestCase):
    def test_sum_across_methods_and_minutes(self):
        data = [series([("01", 16), ("02", 16)]), series([("01", 1)])]
        self.assertEqual(provider.aggregate(data, "m", "day"), (33, "02"))
        self.assertEqual(provider.aggregate(data, "m", "minute_peak"), (17, "02"))

    def test_missing_not_zero(self):
        self.assertEqual(provider.aggregate([], "m", "day"), (None, None))
        self.assertEqual(
            provider.aggregate([series([("01", 2)], "other")], "m", "day"), (None, None)
        )

    def test_reject_gauge(self):
        data = series([("01", 2)])
        data["metricKind"] = "GAUGE"
        with self.assertRaises(ValueError):
            provider.aggregate([data], "m", "day")


if __name__ == "__main__":
    unittest.main()
