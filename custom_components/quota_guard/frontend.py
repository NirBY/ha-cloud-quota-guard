"""Serve the dashboard card bundled with the integration."""

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig

CARD_PATH = "/quota_guard/quota-guard-card.js"
CARD_URL = CARD_PATH + "?v=0.5.0"


async def async_register_frontend(hass):
    """Register once per HA process; static routes survive entry reloads."""
    if hass.data.get("quota_guard_frontend_registered"):
        return
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                CARD_PATH,
                str(Path(__file__).parent / "frontend" / "quota-guard-card.js"),
                False,
            )
        ]
    )
    add_extra_js_url(hass, CARD_URL)
    hass.data["quota_guard_frontend_registered"] = True
