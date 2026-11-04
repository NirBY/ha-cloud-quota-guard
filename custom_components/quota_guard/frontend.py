"""Serve the complete bundled card with a content-addressed upgrade URL."""

import hashlib
import json
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig

CARD_PATH = "/quota_guard/quota-guard-card.js"
STATE_KEY = "quota_guard_frontend_registered"


def asset_details():
    """Read assets off the event loop; manifest is the version source of truth."""
    root = Path(__file__).parent
    card = root / "frontend/quota-guard-card.js"
    version = json.loads((root / "manifest.json").read_text(encoding="utf-8"))[
        "version"
    ]
    fingerprint = hashlib.sha256(card.read_bytes()).hexdigest()[:12]
    return {
        "version": version,
        "url": f"/quota_guard/{fingerprint}/quota-guard-card.js",
        "path": str(card),
    }


async def async_register_frontend(hass):
    """Reconcile module registration without duplicating existing static routes."""
    asset = await hass.async_add_executor_job(asset_details)
    state = hass.data.get(STATE_KEY)
    paths = (
        set(state.get("paths", []))
        if isinstance(state, dict)
        else {CARD_PATH}
        if state
        else set()
    )
    needed = [path for path in (CARD_PATH, asset["url"]) if path not in paths]
    if needed:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(path, asset["path"], False) for path in needed]
        )
    if not isinstance(state, dict) or state.get("url") != asset["url"]:
        add_extra_js_url(hass, asset["url"])
    hass.data[STATE_KEY] = {**asset, "paths": sorted(paths | set(needed))}
