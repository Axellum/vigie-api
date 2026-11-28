"""The beaches of the card's Sea page, kept for each entry in Home Assistant's storage (ADR 0048 of the API).

A star on the card's Map page, or a box ticked in its editor, keeps a spot of the instance among the beaches of the Sea page,
at most ``MAX_BEACHES``. The list belongs to the entry, not to a dashboard nor to a person: the tablet of the hall and the phone
show the same beaches, and a star needs neither the rights of an administrator nor a dashboard saved again. Until the list
is written once, an entry has none (``None``), and the card keeps the beaches written in its own configuration.

The file is ``.storage/vigie.favorites``: ``{"entries": {entry_id: [spot_id, ...]}}``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .const import DOMAIN, FAVORITES_STORAGE_KEY, FAVORITES_STORAGE_VERSION, SIGNAL_FAVORITES

DATA_FAVORITES = f"{DOMAIN}_favorites_store"


class VigieFavorites:
    """The kept beaches of every entry, read once from the storage and written at each change."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, FAVORITES_STORAGE_VERSION, FAVORITES_STORAGE_KEY)
        self._entries: dict[str, list[str]] | None = None
        self._lock = asyncio.Lock()

    async def _async_load(self) -> dict[str, list[str]]:
        if self._entries is None:
            stored = await self._store.async_load()
            entries = stored.get("entries") if isinstance(stored, dict) else None
            self._entries = {
                str(entry_id): [spot for spot in spots if isinstance(spot, str)]
                for entry_id, spots in (entries.items() if isinstance(entries, dict) else ())
                if isinstance(spots, list)
            }
        return self._entries

    async def async_get(self, entry_id: str) -> list[str] | None:
        """The kept beaches of the entry; ``None`` when none were ever kept (the card's own configuration then)."""
        async with self._lock:
            spots = (await self._async_load()).get(entry_id)
        return list(spots) if spots is not None else None

    async def async_set(self, entry_id: str, spots: list[str]) -> list[str]:
        """Keep these beaches for the entry (already checked by the caller), write them, and tell the open cards."""
        async with self._lock:
            entries = await self._async_load()
            entries[entry_id] = list(spots)
            await self._store.async_save({"entries": entries})
        async_dispatcher_send(self._hass, SIGNAL_FAVORITES, entry_id, list(spots))
        return list(spots)

    async def async_remove(self, entry_id: str) -> None:
        """The entry is removed: its beaches go with it."""
        async with self._lock:
            entries = await self._async_load()
            if entries.pop(entry_id, None) is not None:
                await self._store.async_save({"entries": entries})


@callback
def favorites_of(hass: HomeAssistant) -> VigieFavorites:
    """The one store of Home Assistant, made at its first use."""
    store = hass.data.get(DATA_FAVORITES)
    if store is None:
        store = hass.data[DATA_FAVORITES] = VigieFavorites(hass)
    return store
