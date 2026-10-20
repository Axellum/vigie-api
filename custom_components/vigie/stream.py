"""The push stream: a hint that something changed for the place, never the data itself.

``WS /v1/stream`` of the API sends, to a client that follows a position, a ``vigilance_push`` each time a warning for
that position appeared, went up or ended. It does so only when the instance runs its scheduler
(``VIGIE_SCHEDULER=1``); without it nothing is ever pushed, and the integration polls.

The push is used as a **trigger to read again**, not as data: the coordinator then asks ``GET /v1/vigilances`` with the
river radius and the position the user set, so there is one source of states and one place that computes the events.
The poll stays on at its interval as the fallback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from .api import VigieClient, VigieError
from .const import STREAM_MAX_DELAY_S, STREAM_MIN_DELAY_S, STREAM_RECHECK_PUSH_S, STREAM_STABLE_AFTER_S

_LOGGER = logging.getLogger(__name__)

# The API rounds a followed point to four decimals (FOLLOW_DECIMALS in app/services/vigilance.py).
FOLLOW_DECIMALS = 4

STATE_IDLE = "idle"
STATE_NOT_OFFERED = "not_offered"  # the instance runs no scheduler: it would never push
STATE_CONNECTING = "connecting"
STATE_CONNECTED = "connected"
STATE_RECONNECTING = "reconnecting"

_CLOSED = (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR)


class VigieStream:
    """Keeps a WebSocket open on the instance while it offers push, and calls ``on_push`` when the place changed."""

    def __init__(
        self,
        client: VigieClient,
        latitude: float,
        longitude: float,
        on_push: Callable[[], Awaitable[None]],
    ) -> None:
        self._client = client
        self._lat = round(latitude, FOLLOW_DECIMALS)
        self._lon = round(longitude, FOLLOW_DECIMALS)
        self._on_push = on_push
        self.state = STATE_IDLE
        self.last_error: str | None = None
        self.server_version: str | None = None
        self.pushes_received = 0

    async def _sleep(self, seconds: float) -> None:
        """The wait between two attempts (a test replaces it)."""
        await asyncio.sleep(seconds)

    async def _offers_push(self) -> bool:
        """True when the instance runs its scheduler: only then does it send ``vigilance_push``."""
        health = await self._client.health()
        version = health.get("version")
        self.server_version = version if isinstance(version, str) else None
        scheduler = health.get("scheduler")
        return isinstance(scheduler, dict) and scheduler.get("running") is True

    def _is_our_push(self, text: str) -> bool:
        """True for a ``vigilance_push`` about our position (the pushes of the watched zones reach every client)."""
        try:
            message = json.loads(text)
        except ValueError:
            return False
        if not isinstance(message, dict) or message.get("event") != "vigilance_push":
            return False
        point = message.get("point")
        if not isinstance(point, dict):
            return False
        lat, lon = point.get("lat"), point.get("lon")
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (lat, lon)):
            return False
        tolerance = 0.5 * 10**-FOLLOW_DECIMALS
        return abs(lat - self._lat) <= tolerance and abs(lon - self._lon) <= tolerance

    async def _listen(self) -> float:
        """One connection: follow the place and read until it closes. Returns the seconds it stayed up."""
        self.state = STATE_CONNECTING
        started = time.monotonic()
        async with self._client.connect_stream() as socket:
            await socket.send_json({"action": "follow", "lat": self._lat, "lon": self._lon})
            self.state = STATE_CONNECTED
            started = time.monotonic()
            async for message in socket:
                if message.type is aiohttp.WSMsgType.TEXT:
                    if self._is_our_push(message.data):
                        self.pushes_received += 1
                        await self._on_push()
                elif message.type in _CLOSED:
                    break
        return time.monotonic() - started

    async def run(self) -> None:
        """Until cancelled: connect while the instance offers push; wait and try again (a growing delay) when it fails."""
        delay = float(STREAM_MIN_DELAY_S)
        while True:
            stayed = 0.0
            try:
                if not await self._offers_push():
                    self.state = STATE_NOT_OFFERED
                    await self._sleep(STREAM_RECHECK_PUSH_S)
                    continue
                stayed = await self._listen()
                self.last_error = "the instance closed the stream"
            except asyncio.CancelledError:
                raise
            except (VigieError, aiohttp.ClientError, TimeoutError, OSError, ValueError) as err:
                self.last_error = f"{type(err).__name__}: {err}"
            _LOGGER.debug("Push stream of %s: %s", self._client.base_url, self.last_error)
            self.state = STATE_RECONNECTING
            if stayed >= STREAM_STABLE_AFTER_S:
                delay = float(STREAM_MIN_DELAY_S)
            await self._sleep(delay * (1 + random.random() * 0.25))
            delay = min(delay * 2, float(STREAM_MAX_DELAY_S))

    def diagnostics(self) -> dict[str, Any]:
        """What the diagnostics show of the stream."""
        return {
            "state": self.state,
            "last_error": self.last_error,
            "server_version": self.server_version,
            "pushes_received": self.pushes_received,
        }
