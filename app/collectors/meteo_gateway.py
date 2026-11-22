"""Météo-France PIAF rain over the next hour, read through a vigie-meteo gateway (optional).

PIAF ("Prévision Immédiate Agrégée Fusionnée", ``/pro/piaf`` on ``api.meteofrance.fr``) is Météo-France's rain nowcast over
France: a new run every 5 minutes, one value per 5 minutes up to 3 hours ahead, about 1 km apart. Its WCS answers a point per
request and per lead time, so this API does not read it itself: a self-hosted gateway does, and caches it
(``Axellum/vigie-meteo``, ``GET /v1/rain-nowcast?lat=&lon=``). Read on 2026-11-16 from that gateway:

- the answer is ``{"status", "slices": [{"time", "rain_level", "rain_label", "rain_mm_h"}], "updated_at", "source_model"}``;
- ``source_model`` is ``meteofrance-piaf`` only when the slices come from PIAF; when PIAF failed the gateway answers another
  model (AROME, 15-minute steps) or nothing, and this collector refuses it: Open-Meteo then keeps the rain;
- the ``time`` of a slice is the **end** of its 5 minutes (the first slice of a run is its run time plus 5 minutes) and
  ``rain_mm_h`` is the PIAF amount over those 5 minutes times 12 (the gateway's code, read on 2026-11-16; the unit was
  compared with the 6-minute rain of Météo-France stations under rain the same evening, not by a test of this repository).

The next hour is the twelve 5-minute slices that end after the current 5-minute boundary. A run is published 10 to 15
minutes after its time, so the gateway must read about 18 lead times (``METEO_PIAF_STEPS``) for twelve of them to still be
ahead; when one of the twelve is missing the hour is unknown (``None``), never completed.

Without ``VIGIE_METEO_GATEWAY_URL`` there is no collector (``meteo_gateway_url_from_env``), and the rain comes from Open-Meteo.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx

from app import clock
from app.collectors.base import BaseCollector
from app.collectors.grid import DEFAULT_RAIN_NOWCAST_GRID_DEG, coord_str, snap_to_grid

URL_ENV = "VIGIE_METEO_GATEWAY_URL"
PIAF_SOURCE_MODEL = "meteofrance-piaf"
TTL_SECONDS = 300  # a PIAF run every 5 minutes
STEP_S = 300
STEPS_PER_HOUR = 12
# The box this API asks the gateway about (degrees). PIAF covers metropolitan France and Corsica; a point outside the grid
# answers no PIAF value and the gateway falls back to another model, which this collector refuses (a wasted request, no error
# shown). The box is ours, drawn around metropolitan France and Corsica: it was not compared with the bounds of the PIAF grid.
PIAF_LAT = (41.0, 51.5)
PIAF_LON = (-6.0, 10.5)
MAX_MM_H = 500.0  # above this a value is not rain


def meteo_gateway_url_from_env(env: Optional[Mapping[str, str]] = None) -> tuple[Optional[str], Optional[str]]:
    """``(base_url, problem)`` read from ``VIGIE_METEO_GATEWAY_URL``. A value that is not an http(s) address is not used."""
    raw = ((env if env is not None else os.environ).get(URL_ENV) or "").strip()
    if not raw:
        return None, None
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None, f"{URL_ENV} is set but is not an http(s) address: the PIAF rain is off"
    return raw.rstrip("/"), None


def in_piaf_box(lat: float, lon: float) -> bool:
    """Whether the point is inside the box where the gateway is asked for PIAF rain."""
    return PIAF_LAT[0] <= lat <= PIAF_LAT[1] and PIAF_LON[0] <= lon <= PIAF_LON[1]


@dataclass(frozen=True)
class RainSlices:
    """The 5-minute rain rates of one PIAF run at a point: ``ends_utc[i]`` is the end of the slice whose rate is ``mm_h[i]``."""

    ends_utc: tuple[int, ...]
    mm_h: tuple[float, ...]


def _epoch(value: Any) -> int:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise ValueError("slice time without a time zone")
    return int(moment.timestamp())


def parse_gateway_rain(payload: Any) -> RainSlices:
    """The PIAF slices of a gateway answer. ``ValueError`` when the answer is not PIAF or a slice cannot be read."""
    if not isinstance(payload, dict):
        raise ValueError("gateway answer is not an object")
    model = payload.get("source_model")
    if model != PIAF_SOURCE_MODEL:
        raise ValueError(f"gateway rain is not PIAF (source_model={str(model)[:40]!r})")
    slices = payload.get("slices")
    if not isinstance(slices, list) or not slices:
        raise ValueError("gateway answer without slices")
    by_end: dict[int, float] = {}
    for item in slices:
        if not isinstance(item, dict):
            raise ValueError("gateway slice is not an object")
        rate = item.get("rain_mm_h")
        if isinstance(rate, bool) or not isinstance(rate, (int, float)):
            raise ValueError("gateway slice without a rain rate")
        rate = float(rate)
        if not math.isfinite(rate) or rate < 0.0 or rate > MAX_MM_H:
            raise ValueError("gateway slice with a rain rate out of range")
        by_end[_epoch(item.get("time"))] = rate
    ends = tuple(sorted(by_end))
    return RainSlices(ends_utc=ends, mm_h=tuple(by_end[t] for t in ends))


def next_hour_rates(slices: RainSlices, now_s: float) -> Optional[list[float]]:
    """The twelve rates (mm/h) of the 5-minute slices from the current 5-minute boundary on; ``None`` when one is missing."""
    start = int(now_s) // STEP_S * STEP_S
    rates = dict(zip(slices.ends_utc, slices.mm_h, strict=True))
    wanted = [start + STEP_S * (i + 1) for i in range(STEPS_PER_HOUR)]
    if any(end not in rates for end in wanted):
        return None
    return [round(rates[end], 2) for end in wanted]


def quarter_hour_amounts(rates: list[float]) -> list[float]:
    """Four 15-minute amounts (mm) from twelve 5-minute rates (mm/h): each rate lasts 5 minutes, 1/12 of an hour."""
    return [round(sum(rates[i : i + 3]) / 12.0, 2) for i in range(0, STEPS_PER_HOUR, 3)]


class MeteoGatewayRainCollector(BaseCollector[RainSlices]):
    """PIAF rain of a point through the gateway, cached per cell of the PIAF grid (0.01 degree, ``VIGIE_GRID_RAIN_NOWCAST_DEG``)."""

    def __init__(
        self,
        base_url: str,
        grid_deg: float = DEFAULT_RAIN_NOWCAST_GRID_DEG,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(
            provider_id="meteofrance_piaf",
            name="Météo-France PIAF rain (vigie-meteo gateway)",
            domain="weather",
            ttl_seconds=TTL_SECONDS,
            failure_threshold=3,
            recovery_timeout_seconds=120,
            timeout_seconds=8.0,
            http_client=http_client,
        )
        self.base_url = base_url.rstrip("/")
        self.grid_deg = grid_deg

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> RainSlices:
        kwargs.pop("previous", None)
        lat, lon = float(kwargs["lat"]), float(kwargs["lon"])
        response = await self._get(
            client, f"{self.base_url}/v1/rain-nowcast", params={"lat": coord_str(lat), "lon": coord_str(lon)}
        )
        response.raise_for_status()
        return parse_gateway_rain(response.json())

    async def rain_next_hour(self, lat: float, lon: float) -> Optional[list[float]]:
        """Twelve 5-minute rates (mm/h) over the next hour, or ``None`` (outside the box, gateway down, run too old)."""
        if not in_piaf_box(lat, lon):
            return None
        s_lat, s_lon = snap_to_grid(lat, lon, self.grid_deg)
        try:
            slices = await self.fetch(f"piaf:{coord_str(s_lat)},{coord_str(s_lon)}", lat=s_lat, lon=s_lon)
        except Exception:
            return None
        # An expired entry is fine: the window is checked against the clock, a run too old leaves the hour unknown.
        return next_hour_rates(slices, clock.wall())
