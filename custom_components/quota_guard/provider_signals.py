"""Known provider quota failures reported through Home Assistant Repairs."""

from datetime import UTC, datetime

IMOU_QUOTA_KEY = "open_api_quota_exceeded"
MESSAGE = "{name}: IMOU monthly calls are exhausted. Next expected monthly reset: {reset_date} (provider reset time and timezone unverified). Further alerts for this outage are paused; monitoring continues. Cloud actions may fail until quota recovers."
RESOURCES = "https://open.imoulife.com/consoleNew/resourceManage/myResource"


def signals_for(issues, entries, previous=None, now=None):
    """Use an exact entry-scoped repair; never infer usage from arbitrary errors."""
    previous = previous or {}
    now = now or datetime.now(UTC)
    reset_date = (
        now.replace(
            year=now.year + (now.month == 12),
            month=1 if now.month == 12 else now.month + 1,
            day=1,
        )
        .date()
        .isoformat()
    )
    result = {}
    for entry in entries:
        if entry.domain != "imou_life":
            continue
        issue_id = f"{IMOU_QUOTA_KEY}_{entry.entry_id}"
        key = f"imou_life:{issue_id}"
        issue = issues.get(("imou_life", issue_id))
        if (
            issue is not None
            and issue.active
            and issue.translation_key == IMOU_QUOTA_KEY
        ):
            url = issue.learn_more_url
            if url not in (
                RESOURCES,
                "https://open.imou.com/consoleNew/resourceManage/myResource",
            ):
                url = RESOURCES
            result[key] = {
                "entry_id": entry.entry_id,
                "domain": entry.domain,
                "state": "quota_exhausted",
                "confirmed": True,
                "message_key": MESSAGE,
                "name": entry.title,
                "issue_id": issue_id,
                "source": "Home Assistant Repairs · IMOU",
                "reported_at": issue.created.isoformat(),
                "resources_url": url,
                "expected_reset_date": previous.get(key, {}).get(
                    "expected_reset_date", reset_date
                ),
                "reset_time_verified": False,
                "alerts_paused": True,
            }
        elif key in previous and (
            (issue is not None and not issue.active) or entry.state.value != "loaded"
        ):
            # Non-persistent repairs are restored inactive at startup. Preserve the
            # latch until the integration confirms or clears its issue.
            result[key] = {**previous[key], "confirmed": False}
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
