"""Append an English quota view without replacing an existing dashboard."""

import asyncio
from copy import deepcopy

VIEW = {
    "title": "Cloud Quotas",
    "path": "cloud-quotas",
    "type": "panel",
    "icon": "mdi:shield-chart",
    "cards": [{"type": "custom:quota-guard-card", "language": "en"}],
}


def dashboards(hass):
    data = hass.data.get("lovelace")
    return getattr(data, "dashboards", {})


async def add_view(hass, url_path):
    """Use HA's loaded storage object, never open or replace its storage files."""
    available = dashboards(hass)
    key = None if url_path == "lovelace" and url_path not in available else url_path
    dashboard = available.get(key)
    if dashboard is None or dashboard.mode != "storage":
        raise ValueError("Choose an existing storage dashboard")
    lock = hass.data.setdefault("quota_guard_dashboard_lock", asyncio.Lock())
    async with lock:
        config = await dashboard.async_load(False)
        if not isinstance(config.get("views"), list) or config.get("strategy"):
            raise ValueError("Create a dashboard with editable views first")
        for view in config["views"]:
            if view.get("path") == VIEW["path"]:
                if any(
                    card.get("type") == "custom:quota-guard-card"
                    for card in view.get("cards", [])
                ):
                    return False
                raise ValueError("The cloud-quotas view path is already in use")
        # Preserve the complete dashboard and every existing view, including edits.
        await dashboard.async_save(
            {**deepcopy(config), "views": [*deepcopy(config["views"]), deepcopy(VIEW)]}
        )
    return True
