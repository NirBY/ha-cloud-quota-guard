"""Read explicitly configured sensor measurements without inventing freshness."""

from datetime import UTC, datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import voluptuous as vol


def usage_url(value):
    """Allow dashboard paths and HTTP(S) links, never executable URLs or credentials."""
    value = str(value).strip()
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise vol.Invalid("Invalid usage URL") from error
    if (
        not value
        or "\\" in value
        or any(ord(char) < 32 for char in value)
        or parsed.username
        or parsed.password
        or not (
            (value.startswith("/") and not value.startswith("//") and not parsed.netloc)
            or (parsed.scheme in ("http", "https") and parsed.hostname)
        )
    ):
        raise vol.Invalid("Use an HTTP(S) URL or a local dashboard path")
    return value


def period_timezone(value):
    """Validate an explicitly selected provider calendar timezone."""
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as error:
        raise vol.Invalid("Unknown period timezone") from error
    return value


def sensor_sample(config, states, now=None):
    """Read usage, allowance, period and provider timestamp from chosen sources."""
    usage = states.get(config["usage_entity"])
    limit = states.get(config["limit_entity"]) if config.get("limit_entity") else None
    period = (
        states.get(config["period_entity"]) if config.get("period_entity") else None
    )
    timestamp = (
        states.get(config["timestamp_entity"])
        if config.get("timestamp_entity")
        else None
    )
    attrs = usage.attributes if usage else {}
    sample = {
        "used": attrs.get(config["usage_attribute"])
        if config.get("usage_attribute")
        else usage.state
        if usage
        else None,
        # An unavailable configured limit sensor must not fall back to a fixed limit.
        "limit": limit.state
        if limit
        else None
        if config.get("limit_entity")
        else config.get("limit"),
        "period": period.state
        if period
        else None
        if config.get("period_entity")
        else attrs.get(config.get("period_attribute", "period")),
        "timestamp": timestamp.state
        if timestamp
        else None
        if config.get("timestamp_entity")
        else attrs.get(config.get("timestamp_attribute", "sampled_at")),
    }
    if sample["period"] in ("unknown", "unavailable", ""):
        sample["period"] = None
    if config.get("period_mode") == "monthly":
        sample["period"] = None
        try:
            stamp = datetime.fromisoformat(
                str(sample["timestamp"]).replace("Z", "+00:00")
            )
            zone = ZoneInfo(config.get("period_timezone", "UTC"))
            if stamp.tzinfo is not None:
                month = stamp.astimezone(zone).strftime("%Y-%m")
                if month == (now or datetime.now(UTC)).astimezone(zone).strftime(
                    "%Y-%m"
                ):
                    sample["period"] = month
        except (ValueError, TypeError, ZoneInfoNotFoundError):
            pass
    return sample
