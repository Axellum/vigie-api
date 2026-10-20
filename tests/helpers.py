"""Helpers shared by the tests: a simulated provider behind ``httpx.MockTransport``."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any, Optional

import httpx

from app import clock as app_clock
from app.collectors.base import CacheEntry
from app.collectors.disasters import GDACSCollector, NOAANWSCollector
from app.collectors.flood_air import (
    OpenMeteoAirQualityCollector,
    OpenMeteoFloodCollector,
    USGSVolcanoCollector,
)
from app.collectors.marine import OpenMeteoMarineCollector
from app.collectors.meteoalarm import MeteoAlarmCapCollector, MeteoAlarmCollector
from app.collectors.seismic import EMSCCollector, USGSCollector
from app.collectors.space_weather import NOAASWPCCollector
from app.collectors.weather import MetNorwayCollector, OpenMeteoCollector, RainViewerCollector
from app.services.aggregator import AggregatorService
from app.services.storage_wal import SQLiteWALStorage
from tests import samples

# Budget of the compact ``GET /v1/snapshot`` (the credit of every item included): 16 KiB.
SNAPSHOT_BUDGET_BYTES = 16 * 1024


class SimClock:
    """Simulated clock: ``monotonic()`` and ``time()`` move only when the test says so.

    The ``sim_clock`` fixture of ``conftest.py`` installs one as the time source of the collectors.
    """

    EPOCH = 1_792_000_000.0  # a fixed wall-clock start (October 2026)

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return self.EPOCH + self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def http_date(epoch: float) -> str:
    """An epoch time as an HTTP date (``Expires: Tue, 20 Oct 2026 08:40:14 GMT``)."""
    return format_datetime(datetime.fromtimestamp(epoch, tz=timezone.utc), usegmt=True)


def expired_entry(value: Any) -> CacheEntry[Any]:
    """A cache entry stored long ago and no longer fresh: the value is only good as a stale fallback."""
    now = app_clock.monotonic()
    return CacheEntry(stored_at=now - 10_000.0, value=value, fresh_until=now - 9_000.0)


class Provider:
    """A simulated provider: answers from a function and records every request it receives.

    ``answer`` receives the request and returns the response; by default every request is answered with
    ``payload`` as JSON. The answer takes ``latency_ticks`` event-loop turns, so that concurrent
    requests overlap as they would on a network.
    """

    def __init__(
        self,
        payload: Any = None,
        answer: Optional[Callable[[httpx.Request], httpx.Response]] = None,
        latency_ticks: int = 2,
    ) -> None:
        self.payload = payload
        self.answer = answer
        self.latency_ticks = latency_ticks
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for _ in range(self.latency_ticks):
            await asyncio.sleep(0)
        if self.answer is not None:
            return self.answer(request)
        return httpx.Response(200, json=self.payload)

    @property
    def count(self) -> int:
        """Number of requests received."""
        return len(self.requests)

    def client(self) -> httpx.AsyncClient:
        """An ``httpx.AsyncClient`` whose requests are answered by this provider."""
        return httpx.AsyncClient(transport=httpx.MockTransport(self))

    def query(self, index: int = -1) -> dict[str, str]:
        """The query parameters of the ``index``-th request."""
        return dict(self.requests[index].url.params)

    def with_param(self, name: str) -> list[httpx.Request]:
        """The requests that carry the query parameter ``name``."""
        return [request for request in self.requests if name in request.url.params]


def forecast_provider(payload: Optional[dict[str, Any]] = None) -> Provider:
    """Open-Meteo forecast: answers only the blocks (``current``, ``minutely_15``, ``daily``) a request asks for."""
    full = payload if payload is not None else samples.open_meteo_payload()

    def answer(request: httpx.Request) -> httpx.Response:
        asked = request.url.params
        body: dict[str, Any] = {"utc_offset_seconds": full.get("utc_offset_seconds")}
        body.update({block: full[block] for block in ("current", "minutely_15", "daily") if block in asked})
        return httpx.Response(200, json=body)

    return Provider(answer=answer)


def unavailable_client() -> httpx.AsyncClient:
    """A client whose every request is answered with HTTP 503: the provider is down, and nothing is slow."""
    return Provider(answer=lambda request: httpx.Response(503)).client()


def make_service(db_path: Path, **collectors: Any) -> AggregatorService:
    """An ``AggregatorService`` with the given collectors; every other provider answers HTTP 503.

    A collector built without a client would create a real ``httpx`` client (and its SSL context) at each
    call, which is slow on Windows; the guard of ``conftest.py`` would refuse its request anyway.
    """
    down = unavailable_client()
    defaults: dict[str, Any] = {
        "usgs": USGSCollector(http_client=down),
        "emsc": EMSCCollector(http_client=down),
        "gdacs": GDACSCollector(http_client=down),
        "noaa_nws": NOAANWSCollector(http_client=down),
        "volcano": USGSVolcanoCollector(http_client=down),
        "open_meteo": OpenMeteoCollector(http_client=down),
        "met_norway": MetNorwayCollector(http_client=down),
        "rainviewer": RainViewerCollector(http_client=down),
        "marine": OpenMeteoMarineCollector(http_client=down),
        "flood": OpenMeteoFloodCollector(http_client=down),
        "air_quality": OpenMeteoAirQualityCollector(http_client=down),
        "swpc": NOAASWPCCollector(http_client=down),
        "meteoalarm": MeteoAlarmCollector(http_client=down),
        "meteoalarm_cap": MeteoAlarmCapCollector(http_client=down),
    }
    defaults.update(collectors)
    return AggregatorService(storage=SQLiteWALStorage(db_path=db_path), **defaults)
