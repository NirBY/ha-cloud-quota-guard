"""Explain measurement/configuration failures without exposing credentials."""

import math
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def issue(code, message, action, *, severity="error", field=None, **details):
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "action": action,
        "field": field,
        "details": details,
    }


def metric_issues(metric, states, entries, now):
    """Missing coverage is not a configuration error; inspect configured sources only."""
    cfg = metric["config"]
    if (
        cfg.get("automatic_counter")
        and metric.get("counter", {}).get("state") != "observing"
    ):
        return []
    sample = metric.get("sample") or {}
    result = []
    missing = set()
    for field in ("usage_entity", "limit_entity", "period_entity", "timestamp_entity"):
        entity_id = cfg.get(field)
        if not entity_id:
            continue
        state = states.get(entity_id)
        if state is None:
            missing.add(field)
            result.append(
                issue(
                    "entity_missing",
                    "A configured sensor does not exist.",
                    "Select an existing sensor or restore the missing entity.",
                    field=field,
                    entity_id=entity_id,
                )
            )
        elif state.state in ("unknown", "unavailable") and not (
            field == "usage_entity" and cfg.get("usage_attribute")
        ):
            missing.add(field)
            result.append(
                issue(
                    "entity_unavailable",
                    "A configured sensor is unavailable.",
                    "Check the source integration and wait for a valid sensor reading.",
                    severity="warning",
                    field=field,
                    entity_id=entity_id,
                )
            )

    # Provider failures need their own remedy, not an invented stale timestamp.
    provider_error = metric.get("provider_error")
    if provider_error:
        code = metric.get("provider_error_code", "provider_unavailable")
        messages = {
            "authentication_failed": (
                "The provider rejected Quota Guard's credentials.",
                "Replace this connection's credentials in Quota Guard settings.",
            ),
            "permission_denied": (
                "The provider denied access to quota data.",
                "Check API access and read permissions for the credentials supplied to Quota Guard.",
            ),
            "rate_limited": (
                "The provider is limiting quota requests.",
                "Wait for the provider's request window to reset.",
            ),
        }
        message, action = messages.get(
            code,
            (
                "The provider quota query failed.",
                "Check the provider connection and Quota Guard credentials, then refresh.",
            ),
        )
        result.append(
            issue(code, message, action, http_status=metric.get("provider_http_status"))
        )

    reporting = metric.get("reporting") or {}
    try:
        checked = datetime.fromisoformat(
            reporting.get("checked_at", "").replace("Z", "+00:00")
        )
        empty_fresh = (
            reporting.get("state") == "no_activity_reported"
            and checked.tzinfo
            and 0 <= (now - checked).total_seconds() <= cfg["max_age"]
        )
    except (ValueError, TypeError, AttributeError):
        empty_fresh = False
    if not provider_error and not empty_fresh:
        used = sample.get("used")
        if "usage_entity" not in missing:
            if used is None or used == "":
                field = (
                    "usage_attribute" if cfg.get("usage_attribute") else "usage_entity"
                )
                result.append(
                    issue(
                        "usage_missing",
                        "The configured source has no usage value.",
                        "Check the usage sensor and the selected usage attribute, or submit a fresh report.",
                        field=field,
                        attribute=cfg.get("usage_attribute"),
                    )
                )
            else:
                try:
                    if (
                        isinstance(used, bool)
                        or not math.isfinite(float(used))
                        or float(used) < 0
                    ):
                        raise ValueError
                except (ValueError, TypeError, OverflowError):
                    result.append(
                        issue(
                            "usage_invalid",
                            "Usage must be a finite, non-negative number.",
                            "Choose the numeric usage state or attribute for the current reporting period.",
                            field="usage_entity",
                        )
                    )

        stamp = None
        if "timestamp_entity" not in missing and "usage_entity" not in missing:
            raw = sample.get("timestamp")
            field = (
                "timestamp_entity"
                if cfg.get("timestamp_entity")
                else "timestamp_attribute"
            )
            try:
                stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    result.append(
                        issue(
                            "timestamp_timezone",
                            "The measurement timestamp has no timezone.",
                            "Use the source's ISO timestamp including its UTC offset.",
                            field=field,
                        )
                    )
                    stamp = None
                else:
                    age = (now - stamp).total_seconds()
                    if age < -60:
                        result.append(
                            issue(
                                "timestamp_future",
                                "The measurement timestamp is in the future.",
                                "Correct the source clock or timestamp mapping; do not substitute the current time.",
                                field=field,
                            )
                        )
                    elif age > cfg.get("max_age", 900):
                        result.append(
                            issue(
                                "measurement_stale",
                                "The last measurement is older than the allowed age.",
                                "Check source updates and the maximum measurement age setting.",
                                severity="warning",
                                field="max_age",
                                age_seconds=round(age),
                                max_age_seconds=cfg.get("max_age", 900),
                                timestamp=stamp.isoformat(),
                            )
                        )
            except (ValueError, TypeError):
                result.append(
                    issue(
                        "timestamp_missing"
                        if raw is None or raw == ""
                        else "timestamp_invalid",
                        "The measurement timestamp is missing or invalid.",
                        "Select the attribute or sensor containing the source's ISO timestamp.",
                        field=field,
                        attribute=cfg.get("timestamp_attribute", "sampled_at"),
                    )
                )

        if cfg.get("period_mode") == "monthly":
            try:
                zone = ZoneInfo(cfg.get("period_timezone", "UTC"))
                if stamp and stamp.astimezone(zone).strftime("%Y-%m") != now.astimezone(
                    zone
                ).strftime("%Y-%m"):
                    result.append(
                        issue(
                            "previous_month",
                            "The source measurement belongs to a different calendar month.",
                            "Wait for a measurement from the current provider month; do not relabel old usage.",
                            severity="warning",
                            field="period_mode",
                        )
                    )
            except (ZoneInfoNotFoundError, TypeError, ValueError):
                result.append(
                    issue(
                        "timezone_invalid",
                        "The calendar-month timezone is invalid.",
                        "Choose the provider's reset timezone, for example UTC.",
                        field="period_timezone",
                    )
                )
        elif (
            "period_entity" not in missing
            and "usage_entity" not in missing
            and not sample.get("period")
        ):
            result.append(
                issue(
                    "period_missing",
                    "The reporting period is missing.",
                    "Select the provider period attribute or sensor, or explicitly choose calendar month.",
                    field="period_entity"
                    if cfg.get("period_entity")
                    else "period_attribute",
                    attribute=cfg.get("period_attribute", "period"),
                )
            )

    if "limit_entity" not in missing:
        limit = sample.get("limit", cfg.get("limit", metric.get("verified_limit")))
        if limit is not None:
            try:
                if (
                    isinstance(limit, bool)
                    or not math.isfinite(float(limit))
                    or float(limit) <= 0
                ):
                    raise ValueError
            except (ValueError, TypeError, OverflowError):
                result.append(
                    issue(
                        "limit_invalid",
                        "The allowance must be a finite, positive number.",
                        "Check the fixed allowance or the selected limit sensor.",
                        field="limit_entity" if cfg.get("limit_entity") else "limit",
                    )
                )
        elif (
            cfg.get("enforce")
            and cfg.get("threshold_mode", "percent") == "percent"
            and not provider_error
        ):
            result.append(
                issue(
                    "limit_required",
                    "Automatic cutoff has no verified allowance.",
                    "Provide a verified allowance or turn automatic cutoff off.",
                    field="limit",
                )
            )

    for entry_id in cfg.get("entry_ids", []):
        if entry_id not in entries or entries[entry_id] == "quota_guard":
            result.append(
                issue(
                    "target_missing",
                    "A mapped cutoff integration no longer exists or is not a valid target.",
                    "Edit the selected cutoff targets in Quota Guard settings.",
                    field="entry_ids",
                    entry_id=entry_id,
                )
            )
    if cfg.get("enforce") and (
        not (
            cfg.get("entry_ids")
            or (cfg.get("provider_kind") == "oracle" and cfg.get("oracle_instance_ids"))
        )
        or not metric.get("cutoff_authorized")
    ):
        result.append(
            issue(
                "cutoff_not_authorized",
                "Automatic cutoff has not been explicitly authorized for valid targets.",
                "Select cutoff targets and explicitly enable cutoff, or leave monitoring only.",
                field="enforce",
            )
        )
    if cfg.get("enforce") and any(
        value in ("power_off_failed", "state_not_eligible")
        for value in metric.get("cutoff_result", {}).values()
    ):
        result.append(
            issue(
                "oracle_power_off_failed",
                "Oracle power-off did not complete for a selected instance.",
                "Check the instance state and INSTANCE_POWER_ACTION permission. Monitoring continues; no restart or termination is attempted.",
                severity="warning",
                field="oracle_instance_ids",
            )
        )
    return result


def retired_issues(metric):
    """Explain old sources without treating intentional removal as an error."""
    cfg = metric["config"]
    legacy = not cfg.get("provider_id")
    if metric.get("acknowledged") or not legacy:
        return []
    if cfg["id"].startswith("tuya_observed_"):
        return [
            issue(
                "observer_removed",
                "The old Tuya request counter was removed in the update.",
                "Open Quota Guard settings, choose Tuya usage, then connect a sensor or enter a console reading. The Tuya integration itself was not disabled.",
                severity="warning",
            )
        ]
    return [
        issue(
            "credentials_required",
            "This legacy source stopped after the credential isolation update.",
            "Connect GitHub or Google in Quota Guard settings using credentials supplied directly to this plugin.",
            severity="warning",
        )
    ]


def connection_issues(entry):
    if entry.get("disabled") or entry.get("provider_signal"):
        return []
    if entry.get("state") in (
        "setup_error",
        "setup_retry",
        "migration_error",
        "failed_unload",
    ):
        return [
            issue(
                "integration_failed",
                "Home Assistant reports a problem loading this integration.",
                "Open the integration to inspect its setup error or repair. A missing quota counter is a separate issue.",
                severity="error",
                state=entry["state"],
            )
        ]
    return []
