"""The dashboard card shipped with the integration (ADR 0042 of the API).

Home Assistant serves the file and loads it on every page of its frontend, so the card needs no resource added by hand and
always matches the integration that gives it its data (the commands of ``websocket.py``). The URL carries the version of the
integration, so a browser does not keep an old card after an update.
"""

from __future__ import annotations

from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

CARD_FILE = Path(__file__).parent / "frontend" / "vigie-card.js"
CARD_URL = "/vigie/vigie-card.js"


async def async_register_card(hass: HomeAssistant) -> bool:
    """Serve the card and have the frontend load it; nothing where the frontend does not run (False then)."""
    if getattr(hass, "http", None) is None or "frontend" not in hass.config.components:
        return False
    # Imported here: the integration also runs where there is no frontend (its tests, a headless install).
    from homeassistant.components.frontend import add_extra_js_url  # noqa: PLC0415
    from homeassistant.components.http import StaticPathConfig  # noqa: PLC0415

    integration = await async_get_integration(hass, DOMAIN)
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(CARD_FILE), cache_headers=False)])
    add_extra_js_url(hass, f"{CARD_URL}?v={integration.version}")
    return True
