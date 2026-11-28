"""The dashboard card shipped with the integration (ADR 0042 of the API), and how the frontend loads it (ADR 0044).

Home Assistant serves the file, so the card needs no resource added by hand and always matches the integration that gives it
its data (the commands of ``websocket.py``).

Where the dashboards keep their resources in storage (the default) and the ``www`` folder of the configuration is served as
``/local``, the card is copied there, with a compressed copy for the browsers that accept it, and listed as a dashboard
resource, like the cards HACS installs. The frontend then has it from the first second of a start of Home Assistant, before
the integrations are set up: a dashboard opened while Home Assistant starts shows it, where a card added by the integration at
its setup stays missing until the page is reloaded. The URL carries a digest of the file, so a browser keeps it as long as it
does not change and reads the new one after an update.

Elsewhere (resources in YAML, no ``www`` folder when Home Assistant started, a copy that fails), the frontend loads it from the
integration's own path, with the version of the integration in the URL, as before.

MapLibre GL JS, which draws the card's Map page (ADR 0048), goes beside the card in both places: ``maplibre/`` next to the copy
in the ``www`` folder (with compressed copies too), and ``/vigie/maplibre/`` next to the integration's own path. The card asks
it there, from its own address, only when the Map page opens: no CDN, and nothing more for the other pages.
"""

from __future__ import annotations

import gzip
import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.loader import async_get_integration

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.components.lovelace import LovelaceData

_LOGGER = logging.getLogger(__name__)

CARD_FILE = Path(__file__).parent / "frontend" / "vigie-card.js"
CARD_URL = "/vigie/vigie-card.js"
# The copy in the www folder of the configuration, which the frontend serves as /local.
LOCAL_FOLDER = ("www", "vigie")
LOCAL_NAMES = ("vigie-card.js", "vigie-card.js.gz")
LOCAL_URL = "/local/vigie/vigie-card.js"
# MapLibre GL JS (frontend/maplibre/README.md): its files, compressed beside the copy of the card, and its licence as it is.
MAPLIBRE_VERSION = "6.14.0"
MAPLIBRE_FOLDER = CARD_FILE.parent / "maplibre"
MAPLIBRE_URL = "/vigie/maplibre"
MAPLIBRE_NAMES = ("maplibre-gl.mjs", "maplibre-gl-worker.mjs", "maplibre-gl.css")
MAPLIBRE_PLAIN = ("LICENSE.txt",)
# Set when the last entry took the resource away, so an entry added again in the same run puts it back.
CARD_REMOVED = f"{DOMAIN}_card_removed"


async def async_register_card(hass: HomeAssistant) -> bool:
    """Serve the card and have the frontend load it; nothing where the frontend does not run (False then)."""
    if getattr(hass, "http", None) is None or "frontend" not in hass.config.components:
        return False
    # Imported here: the integration also runs where there is no frontend (its tests, a headless install).
    from homeassistant.components.frontend import add_extra_js_url  # noqa: PLC0415
    from homeassistant.components.http import StaticPathConfig  # noqa: PLC0415

    integration = await async_get_integration(hass, DOMAIN)
    # Always served at the integration's own path too: a resource added by hand to it keeps working.
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(CARD_URL, str(CARD_FILE), cache_headers=False),
            # Its address carries the version of MapLibre: kept by the browsers until it changes.
            StaticPathConfig(MAPLIBRE_URL, str(MAPLIBRE_FOLDER), cache_headers=True),
        ]
    )
    if not await async_list_as_resource(hass):
        add_extra_js_url(hass, f"{CARD_URL}?v={integration.version}")
    return True


def _lovelace_storage(hass: HomeAssistant) -> LovelaceData | None:
    """The dashboards' data when they keep their resources in storage; None otherwise (YAML, or no dashboards)."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA, MODE_STORAGE  # noqa: PLC0415

    lovelace = hass.data.get(LOVELACE_DATA)
    return lovelace if lovelace is not None and lovelace.resource_mode == MODE_STORAGE else None


def _local_served(hass: HomeAssistant) -> bool:
    """Whether the frontend serves the www folder as /local: it does only when the folder existed when it was set up."""
    try:
        resources = list(hass.http.app.router.resources())
    except (AttributeError, TypeError):
        return False
    return any(isinstance(resource, web.StaticResource) and resource.canonical == "/local" for resource in resources)


def _write(path: Path, content: bytes) -> None:
    """Written aside then moved in place, so the frontend never serves half a file."""
    partial = path.with_name(f".{path.name}.part")
    partial.write_bytes(content)
    partial.replace(path)


def _copy_maplibre(folder: Path) -> None:
    """MapLibre beside the card, each file with its compressed copy; a file already there as it is, and its compressed copy,
    are left alone (MapLibre is 1.6 MB: it is compressed again only when it changes)."""
    folder.mkdir(parents=True, exist_ok=True)
    for name in (*MAPLIBRE_NAMES, *MAPLIBRE_PLAIN):
        data = (MAPLIBRE_FOLDER / name).read_bytes()
        path, packed = folder / name, folder / f"{name}.gz"
        same = path.is_file() and path.read_bytes() == data
        if not same:
            _write(path, data)
        if name in MAPLIBRE_NAMES and (not same or not packed.is_file()):
            _write(packed, gzip.compress(data, compresslevel=9, mtime=0))


def _copy(folder: Path) -> str:
    """Write the card and its compressed copy, and MapLibre beside them, into the folder where they differ; the digest of
    the card."""
    data = CARD_FILE.read_bytes()
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in zip(LOCAL_NAMES, (data, gzip.compress(data, compresslevel=9, mtime=0)), strict=True):
        path = folder / name
        if path.is_file() and path.read_bytes() == content:
            continue
        _write(path, content)
    _copy_maplibre(folder / "maplibre")
    return hashlib.sha256(data).hexdigest()[:12]


def _ours(item: dict) -> bool:
    return str(item.get("url", "")).split("?", 1)[0] == LOCAL_URL


async def async_list_as_resource(hass: HomeAssistant) -> bool:
    """Copy the card into the www folder and list it as a dashboard resource; False where it cannot (then nothing changed)."""
    lovelace = _lovelace_storage(hass)
    if lovelace is None or not _local_served(hass):
        return False
    try:
        digest = await hass.async_add_executor_job(_copy, Path(hass.config.path(*LOCAL_FOLDER)))
    except OSError as err:
        _LOGGER.warning("The card could not be copied into the www folder (%s): it is loaded from the integration instead", err)
        return False
    url = f"{LOCAL_URL}?v={digest}"
    resources = lovelace.resources
    try:
        await resources.async_get_info()  # reads the stored resources once
        ours = [item for item in resources.async_items() if _ours(item)]
        if not ours:
            await resources.async_create_item({"res_type": "module", "url": url})
        elif ours[0].get("url") != url or ours[0].get("type") != "module":
            await resources.async_update_item(ours[0]["id"], {"res_type": "module", "url": url})
        for extra in ours[1:]:
            await resources.async_delete_item(extra["id"])
    except HomeAssistantError as err:
        _LOGGER.warning("The card could not be listed as a dashboard resource (%s): it is loaded from the integration instead", err)
        return False
    hass.data.pop(CARD_REMOVED, None)
    return True


def _remove_copy(folder: Path) -> None:
    maplibre = folder / "maplibre"
    for name in (*MAPLIBRE_NAMES, *MAPLIBRE_PLAIN):
        (maplibre / name).unlink(missing_ok=True)
        (maplibre / f"{name}.gz").unlink(missing_ok=True)
    for name in LOCAL_NAMES:
        (folder / name).unlink(missing_ok=True)
    for empty in (maplibre, folder):
        try:
            empty.rmdir()
        except OSError:
            pass  # something else was put in it, or it is already gone


async def async_remove_card(hass: HomeAssistant) -> None:
    """The last entry removed: its resource and its copy in the www folder go too (one added by hand is left alone)."""
    lovelace = _lovelace_storage(hass)
    if lovelace is not None:
        await lovelace.resources.async_get_info()
        for item in [item for item in lovelace.resources.async_items() if _ours(item)]:
            await lovelace.resources.async_delete_item(item["id"])
    await hass.async_add_executor_job(_remove_copy, Path(hass.config.path(*LOCAL_FOLDER)))
    hass.data[CARD_REMOVED] = True


async def async_card_back(hass: HomeAssistant) -> None:
    """An entry added again after the last one was removed, in the same run of Home Assistant: the resource comes back."""
    if not hass.data.get(CARD_REMOVED) or await async_list_as_resource(hass):
        return
    from homeassistant.components.frontend import add_extra_js_url  # noqa: PLC0415

    hass.data.pop(CARD_REMOVED, None)
    add_extra_js_url(hass, f"{CARD_URL}?v={(await async_get_integration(hass, DOMAIN)).version}")
