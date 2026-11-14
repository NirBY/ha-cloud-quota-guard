"""Deliver explicitly enabled channels through Home Assistant's notify services."""

import asyncio


def available_channels(hass):
    choices = []
    for name in hass.services.async_services().get("notify", {}):
        if name not in ("send_message", "persistent_notification"):
            choices.append(
                {
                    "value": "service:notify." + name,
                    "label": "Notification service — " + name,
                }
            )
    for entity in hass.states.async_all("notify"):
        choices.append({"value": "entity:" + entity.entity_id, "label": entity.name})
    return choices


def validate_channel(hass, channel):
    if channel["destination"] not in {
        choice["value"] for choice in available_channels(hass)
    }:
        raise ValueError("Choose a configured notification service or entity")
    if not set(channel["levels"]) <= {"warning", "critical", "cutoff"}:
        raise ValueError("Invalid alert levels")
    if channel["destination"].startswith("entity:") and channel.get("targets"):
        raise ValueError("A notification entity already identifies its recipient")


async def deliver(hass, channels, level, title, message):
    """A delivery error must never prevent the persistent alert or a cutoff."""
    failures = {}
    for channel in channels:
        if not channel.get("enabled") or level not in channel.get("levels", []):
            continue
        try:
            validate_channel(hass, channel)
            kind, destination = channel["destination"].split(":", 1)
            data = {"title": title, "message": message}
            if kind == "entity":
                service = "send_message"
                data["entity_id"] = destination
            else:
                service = destination.removeprefix("notify.")
                if channel.get("targets"):
                    data["target"] = channel["targets"]
            async with asyncio.timeout(10):
                await hass.services.async_call("notify", service, data, blocking=True)
        except Exception as error:
            failures[channel["id"]] = type(error).__name__
    return failures
