import importlib.util
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "engine",
    Path(__file__).resolve().parents[1] / "custom_components/quota_guard/engine.py",
)
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


class TestQuota(unittest.TestCase):
    now = datetime(2026, 10, 30, tzinfo=UTC)

    def sample(self, used, **kw):
        return dict(
            used=used, limit=100, period="2026-10", timestamp=self.now.isoformat(), **kw
        )

    def test_boundaries(self):
        for used, status in [
            (89.99, "ok"),
            (90, "warning"),
            (94.99, "warning"),
            (95, "cutoff"),
            (110, "cutoff"),
        ]:
            self.assertEqual(
                engine.evaluate(self.sample(used), self.now)["status"], status
            )

    def test_invalid_and_unavailable_never_zero(self):
        for used in ["unknown", None, float("nan"), float("inf"), -1]:
            self.assertIsNone(engine.evaluate(self.sample(used), self.now)["percent"])

    def test_stale_cannot_disable(self):
        self.assertEqual(
            engine.evaluate(self.sample(99), self.now + timedelta(hours=1))["status"],
            "stale",
        )

    def test_future_and_zero_limit(self):
        self.assertEqual(
            engine.evaluate(self.sample(99), self.now - timedelta(hours=1))["status"],
            "stale",
        )
        s = self.sample(99)
        s["limit"] = 0
        self.assertEqual(engine.evaluate(s, self.now)["status"], "unknown")

    def test_usage_without_limit_still_requires_fresh_finite_sample(self):
        sample = self.sample(25)
        sample.pop("limit")
        result = engine.evaluate(sample, self.now)
        self.assertEqual(result, {"status": "usage_only", "percent": None, "used": 25})
        self.assertEqual(
            engine.evaluate(sample, self.now + timedelta(hours=1))["status"], "stale"
        )
        sample["used"] = float("nan")
        self.assertEqual(engine.evaluate(sample, self.now)["status"], "unknown")

    def test_jump_and_no_duplicate(self):
        self.assertEqual(engine.crossings(80, 105), [90, 95, 100])
        self.assertEqual(engine.crossings(96, 97), [])
        self.assertEqual(engine.crossings(None, None), [])


if __name__ == "__main__":
    unittest.main()
