"""Known provider quota failures reported through Home Assistant Repairs."""

import re
from datetime import UTC, datetime

IMOU_QUOTA_KEY = "open_api_quota_exceeded"
MESSAGE = "{name}: IMOU monthly calls are exhausted. Next expected monthly reset: {reset_date} (provider reset time and timezone unverified). Further alerts for this outage are paused; monitoring continues. Cloud actions may fail until quota recovers."
RESOURCES = "https://open.imoulife.com/consoleNew/resourceManage/myResource"


def signals_for(issues, entries, previous=None, now=None):
    """Match only the exact repair for each IMOU entry, including restored history."""
    previous = previous or {}
    now = now or datetime.now(UTC)
    result = {}
    for entry in entries:
        if entry.domain != "imou_life":
            continue
        issue_id = f"{IMOU_QUOTA_KEY}_{entry.entry_id}"
        key = f"imou_life:{issue_id}"
        prior = previous.get(key, {})
        issue = issues.get((entry.domain, issue_id))
        # A failed first refresh can report OP1013 before a repair is registered.
        # Match the provider code, never an unrelated network/authentication failure.
        reason = " ".join(
            str(value)
            for value in (
                getattr(entry, "reason", None),
                *(
                    getattr(entry, "error_reason_translation_placeholders", None) or {}
                ).values(),
            )
            if value is not None
        )
        setup_quota = entry.state.value in ("setup_retry", "setup_error") and bool(
            re.search(r"\bOP1013\b", reason, re.IGNORECASE)
        )
        if issue is None:
            if not setup_quota and prior and entry.state.value != "loaded":
                result[key] = {**prior, "confirmed": False}
            if not setup_quota:
                continue
        if (
            issue
            and issue.active
            and issue.translation_key != IMOU_QUOTA_KEY
            and not setup_quota
        ):
            continue
        if issue and not issue.active and entry.state.value == "loaded":
            continue
        # HA restores non-persistent repairs inactive and without translations.
        # Their original date is evidence of an earlier incident, not a new alarm.
        reported = issue.created if issue else now
        if setup_quota:
            # A live failure confirms this month's outage, even if HA restored an
            # older inactive repair. Keep the timestamp stable within the month.
            previous_time = prior.get("reported_at")
            prior_date = (
                datetime.fromisoformat(previous_time) if previous_time else None
            )
            reported = (
                prior_date
                if prior_date
                and (prior_date.year, prior_date.month) == (now.year, now.month)
                else now
            )
        reset = reported.date().replace(
            year=reported.year + (reported.month == 12),
            month=1 if reported.month == 12 else reported.month + 1,
            day=1,
        )
        url = issue.learn_more_url if issue else None
        if url not in (
            RESOURCES,
            "https://open.imou.com/consoleNew/resourceManage/myResource",
        ):
            url = prior.get("resources_url", RESOURCES)
        result[key] = {
            "entry_id": entry.entry_id,
            "domain": entry.domain,
            "state": "quota_exhausted",
            "confirmed": setup_quota or issue.active,
            "message_key": MESSAGE,
            "name": entry.title,
            "issue_id": issue_id,
            "source": "Home Assistant setup error · IMOU (OP1013)"
            if setup_quota
            else "Home Assistant Repairs · IMOU",
            "reported_at": reported.isoformat(),
            "resources_url": url,
            "expected_reset_date": reset.isoformat(),
            "reset_time_verified": False,
            "alerts_paused": True,
        }
    return result


def collect_signals(hass, previous):
    from homeassistant.helpers import issue_registry as ir

    return signals_for(
        ir.async_get(hass).issues, hass.config_entries.async_entries(), previous
    )


def subscribe(hass, manager):
    from homeassistant.helpers import issue_registry as ir

    async def changed(event):
        if event.data.get("domain") == "imou_life":
            async with manager.lock:
                await manager.provider_signals_refresh()
                for listener in manager.listeners:
                    listener()

    return hass.bus.async_listen(ir.EVENT_REPAIRS_ISSUE_REGISTRY_UPDATED, changed)
