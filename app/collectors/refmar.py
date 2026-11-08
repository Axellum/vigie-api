"""REFMAR (SHOM): the latest raw measures of a tide gauge, to correct the modelled tide (ADR 0017).

``https://services.data.shom.fr/maregraphie/observation/json/{id}?sources=1&dtStart=...&dtEnd=...`` answers the raw high-frequency
measures (one a minute, UTC, metres above the chart datum) of a gauge. Read on 2026-11-08: the newest value of Brest was 3 minutes old and
that of Lorient 20 minutes. The service limits a request to 31 days; this collector asks for the last ``WINDOW_HOURS`` hours only.

One request per gauge per ``TTL_SECONDS``, whatever the number of clients (the cache key is the gauge). The tide changes by a few
millimetres in the minutes between two reads, and the correction fades over 12 hours, so a quarter of an hour is not a loss. At most 11 gauges
(``app/data/refmar-stations.json``): 1,056 requests a day if every one is asked all day. The User-Agent names the project.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from app import clock
from app.collectors.base import BaseCollector, optional_float

BASE_URL = "https://services.data.shom.fr/maregraphie/observation/json/{station}"
TTL_SECONDS = 900
# The correction averages the last 745 minutes (``tide_correction.WINDOW_MINUTES``): a little more than that is asked.
WINDOW_HOURS = 14

Observations = list[tuple[float, float]]


def parse_observations(payload: Any) -> Observations:
    """REFMAR's JSON (``data``: ``timestamp`` "YYYY/MM/DD HH:MM:SS" in UTC, ``value`` in metres) as (unix time, metres), sorted.
    A value that is missing or not a number is skipped. Raises ``ValueError`` when the answer is not REFMAR's shape."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("REFMAR answer without a data list")
    values: Observations = []
    for item in payload["data"]:
        if not isinstance(item, dict):
            continue
        value = optional_float(item.get("value"))
        if value is None:
            continue
        try:
            moment = datetime.strptime(str(item.get("timestamp")), "%Y/%m/%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        values.append((moment.timestamp(), value))
    return sorted(set(values))


class RefmarCollector(BaseCollector[Observations]):
    """The latest raw measures of a REFMAR tide gauge."""

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="shom_refmar",
            name="REFMAR tide gauges (SHOM)",
            domain="tides",
            ttl_seconds=TTL_SECONDS,
            http_client=http_client,
        )

    async def fetch_recent(self, station_id: str) -> Observations:
        """The measures of the last ``WINDOW_HOURS`` hours of a gauge (the cache of the last read when the service fails)."""
        return await self.fetch(cache_key=f"refmar:{station_id}", station_id=station_id)

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Observations:
        end = datetime.fromtimestamp(clock.wall(), tz=timezone.utc)
        params = {
            "sources": "1",
            "dtStart": (end - timedelta(hours=WINDOW_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "dtEnd": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        response = await self._get(client, BASE_URL.format(station=kwargs["station_id"]), params=params)
        response.raise_for_status()
        return parse_observations(response.json())
