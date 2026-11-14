"""Pure quota decisions; no network or Home Assistant dependencies."""

import math
from datetime import datetime


def validate_thresholds(config):
    """Amounts do not require an allowance; remaining balances run downward."""
    mode = config.get("threshold_mode", "percent")
    if mode not in ("percent", "above", "below"):
        raise ValueError("Unknown threshold mode")
    warning = float(
        config.get("warning_percent", 90)
        if mode == "percent"
        else config["warning_value"]
    )
    critical = float(
        config.get("cutoff_percent", 95)
        if mode == "percent"
        else config["critical_value"]
    )
    if not all(math.isfinite(value) and value >= 0 for value in (warning, critical)):
        raise ValueError("Thresholds must be finite and non-negative")
    if mode == "below":
        if critical >= warning:
            raise ValueError("Critical remaining balance must be below warning")
    elif warning >= critical or (
        mode == "percent" and not 0 < warning < critical <= 100
    ):
        raise ValueError("Warning must be below critical")


def evaluate_config(sample, now, config):
    return evaluate(
        sample,
        now,
        config.get("max_age", 900),
        config.get("warning_percent", 90),
        config.get("cutoff_percent", 95),
        threshold_mode=config.get("threshold_mode", "percent"),
        warning_value=config.get("warning_value"),
        critical_value=config.get("critical_value"),
    )


def evaluate(
    sample,
    now,
    max_age=900,
    warning_percent=90,
    cutoff_percent=95,
    *,
    threshold_mode="percent",
    warning_value=None,
    critical_value=None,
):
    """Only a fresh, finite provider sample may trigger a cutoff."""
    try:
        validate_thresholds(
            dict(
                threshold_mode=threshold_mode,
                warning_percent=warning_percent,
                cutoff_percent=cutoff_percent,
                warning_value=warning_value,
                critical_value=critical_value,
            )
        )
        used = float(sample["used"])
        timestamp = datetime.fromisoformat(sample["timestamp"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None or not sample.get("period"):
            raise ValueError("Missing timezone or period")
        age = (now - timestamp).total_seconds()
        if sample.get("expires_at"):
            expires = datetime.fromisoformat(
                sample["expires_at"].replace("Z", "+00:00")
            )
            if expires.tzinfo is None or expires <= timestamp:
                raise ValueError("Invalid period expiry")
            if now >= expires:
                return {"status": "stale", "percent": None}
        if not math.isfinite(used) or used < 0:
            raise ValueError("Invalid numeric quota")
        if age < -60 or age > max_age:
            return {"status": "stale", "percent": None}
        result = {"status": "usage_only", "percent": None, "used": used}
        if sample.get("limit") is not None:
            limit = float(sample["limit"])
            if not math.isfinite(limit) or limit <= 0:
                raise ValueError("Invalid numeric limit")
            result.update(
                percent=used / limit * 100, limit=limit, remaining=max(0, limit - used)
            )
        if threshold_mode == "percent":
            if result["percent"] is None:
                return result
            warning, critical = (
                result["percent"] >= warning_percent,
                result["percent"] >= cutoff_percent,
            )
        elif threshold_mode == "above":
            warning, critical = (
                used >= float(warning_value),
                used >= float(critical_value),
            )
        else:
            warning, critical = (
                used <= float(warning_value),
                used <= float(critical_value),
            )
        result["status"] = "cutoff" if critical else "warning" if warning else "ok"
        return result
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
        return {"status": "unknown", "percent": None}


def crossings(previous, current, warning_percent=90, cutoff_percent=95):
    """Record all thresholds on a jump; timestamp is observation, never invented."""
    if current is None:
        return []
    previous = previous if previous is not None else -1
    return [
        x
        for x in sorted({warning_percent, cutoff_percent, 100})
        if previous < x <= current
    ]
