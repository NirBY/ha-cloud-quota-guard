"""Known provider quota failures reported through Home Assistant Repairs."""

IMOU_QUOTA_KEY = "open_api_quota_exceeded"
MESSAGE = "{name}: IMOU monthly calls are exhausted. Next expected monthly reset: {reset_date} (provider reset time and timezone unverified). Further alerts for this outage are paused; monitoring continues. Cloud actions may fail until quota recovers."
RESOURCES = "https://open.imoulife.com/consoleNew/resourceManage/myResource"


def signals_for(issues, entries, previous=None):
    """Match only the exact repair for each IMOU entry, including restored history."""
    previous = previous or {}
    result = {}
    for entry in entries:
        if entry.domain != "imou_life":
            continue
        issue_id = f"{IMOU_QUOTA_KEY}_{entry.entry_id}"
        key = f"imou_life:{issue_id}"
        prior = previous.get(key, {})
        issue = issues.get((entry.domain, issue_id))
        if issue is None:
            if prior and entry.state.value != "loaded":
                result[key] = {**prior, "confirmed": False}
            continue
        if issue.active and issue.translation_key != IMOU_QUOTA_KEY:
            continue
        # HA restores non-persistent repairs inactive and without translations.
        # Their original date is evidence of an earlier incident, not a new alarm.
        reported = issue.created
        reset = reported.date().replace(
            year=reported.year + (reported.month == 12),
            month=1 if reported.month == 12 else reported.month + 1,
            day=1,
        )
        url = issue.learn_more_url
        if url not in (
            RESOURCES,
            "https://open.imou.com/consoleNew/resourceManage/myResource",
        ):
            url = prior.get("resources_url", RESOURCES)
        result[key] = {
            "entry_id": entry.entry_id,
            "domain": entry.domain,
            "state": "quota_exhausted",
            "confirmed": issue.active,
            "message_key": MESSAGE,
            "name": entry.title,
            "issue_id": issue_id,
            "source": "Home Assistant Repairs · IMOU",
            "reported_at": reported.isoformat(),
            "resources_url": url,
            "expected_reset_date": prior.get("expected_reset_date", reset.isoformat()),
            "reset_time_verified": False,
            "alerts_paused": True,
            "final_notice_sent": issue.active or prior.get("final_notice_sent", False),
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
