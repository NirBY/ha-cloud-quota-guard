"""Pure quota decisions; no network or Home Assistant dependencies."""

import math
from datetime import datetime


def evaluate(sample, now, max_age=900):
    """Only a fresh, finite provider sample may trigger a cutoff."""
    try:
        used, limit = float(sample["used"]), float(sample["limit"])
        timestamp = datetime.fromisoformat(sample["timestamp"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None or not sample.get("period"):
            raise ValueError("Missing timezone or period")
        age = (now - timestamp).total_seconds()
        if not all(map(math.isfinite, (used, limit))) or used < 0 or limit <= 0:
            raise ValueError("Invalid numeric quota")
        if age < -60 or age > max_age:
            return {"status": "stale", "percent": None}
        percent = used / limit * 100
        return {
            "status": "cutoff"
            if percent >= 95
            else "warning"
            if percent >= 90
            else "ok",
            "percent": percent,
            "used": used,
            "limit": limit,
            "remaining": max(0, limit - used),
        }
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
        return {"status": "unknown", "percent": None}


def crossings(previous, current):
    """Record all thresholds on a jump; timestamp is observation, never invented."""
    if current is None:
        return []
    previous = previous if previous is not None else -1
    return [x for x in (90, 95, 100) if previous < x <= current]
