"""Météo-France ensemble forecasts (PE-AROME and PE-ARPEGE): probabilities of rain, snow, gusts, hail and storm, read through a gateway (optional).

An ensemble is a set of 35 runs of a model started from slightly different states; the probability of an event is the share of the members
that produce it. Météo-France publishes two of them on its API portal:

- PE-AROME (``pearome/1.0``, 2.5 km, up to 51 hours, a run every 6 hours): the probabilities are published ready-made, a map per threshold and per
  hour;
- PE-ARPEGE (``pearpege/1.0``, 10 km, up to 102 hours, a run every 6 hours): one service per member with the values, so the gateway counts the members
  whose 24-hour rain reaches 1, 5, 10 and 20 mm.

The WCS of both answers one step of one parameter of one member per request, so this API does not read them: the gateway (``Axellum/vigie-meteo``,
``GET /v1/probabilities?lat=&lon=``, read on 2026-11-18) downloads each run once (119 + 140 requests), keeps a map of France reduced to blocks of
5 km (PE-AROME) or 10 km (PE-ARPEGE) and answers a point in a few milliseconds. This collector asks the gateway for a point (cached per cell of the
forecast grid, ten minutes), refuses an answer that is not these models' or that has a value out of 0..100, and drops the windows already past.

A probability of a window is not "the probability that it rains": the three-hour windows of ``short_range`` are the usual unit of Météo-France's
ensemble products, and the largest of them in the next 24 hours is reported as ``rain_peak_next_24h``. The probability that it rains at some time of
the day is higher than any of its windows and is not given.

Without ``VIGIE_METEO_GATEWAY_URL`` there is no collector and the point carries no ``probabilities``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import httpx

from app import clock
from app.attribution import credit_text, merge_credits
from app.collectors.base import SELF_HOSTED_CONNECTION, BaseCollector
from app.collectors.grid import DEFAULT_FORECAST_GRID_DEG, coord_str, snap_to_grid
from app.models.ensemble import (
    LongRange,
    LongRangeWindow,
    Probabilities,
    RainPeak,
    ShortRange,
    ShortRangeWindow,
)

PROVIDER_ID = "meteofrance_ensemble"
SHORT_MODEL = "meteofrance-pe-arome"
LONG_MODEL = "meteofrance-pe-arpege"
TTL_SECONDS = 600
PEAK_HORIZON_S = 24 * 3600
SHORT_KEYS = ("rain_1mm", "rain_5mm", "snow_1mm", "gust_30kt", "gust_50kt", "hail", "storm")
LONG_KEYS = ("rain_1mm", "rain_5mm", "rain_10mm", "rain_20mm")

NOTE = (
    "Probabilities from Météo-France's ensemble forecasts: PE-AROME for the next 51 hours (windows of three hours) and PE-ARPEGE for the next 96 hours "
    "(windows of 24 hours), the probability of the block of 5 km or 10 km around the point: a chance for the area, which over-states the chance at one "
    "exact spot (measured against ground stations on one morning, ADR 0032). A window's probability is not the probability that it "
    "rains at some time of the day. A model forecast, not an official warning: it is never part of local_alert_level, and the warnings of the national "
    "services are the nearby_hazards. `rain_probability_pct` of `daily` stays Open-Meteo's (the highest hourly probability of the day, any "
    "amount) and is not merged with these: they answer a different question (an amount in a window), so the two numbers can differ."
)


def _epoch(value: Any) -> int:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise ValueError("time without a time zone")
    return int(moment.timestamp())


def _pct(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100 or value != value:
        raise ValueError(f"{what} is missing or out of 0..100")
    return int(value)


class RawBlock:
    """The windows of a gateway block, checked: ``run_utc``, ``members`` and, per window, ``(to_utc, {key: percent})``."""

    def __init__(self, run_utc: int, members: int, windows: tuple[tuple[int, dict[str, int]], ...]) -> None:
        self.run_utc = run_utc
        self.members = members
        self.windows = windows


class RawProbabilities:
    def __init__(self, short: Optional[RawBlock], long: Optional[RawBlock]) -> None:
        self.short = short
        self.long = long


def _parse_block(block: Any, model: str, window_hours: int, keys: tuple[str, ...]) -> Optional[RawBlock]:
    if block is None:
        return None
    if not isinstance(block, dict):
        raise ValueError("gateway block is not an object")
    if block.get("source_model") != model:
        raise ValueError(f"gateway block is not {model} (source_model={str(block.get('source_model'))[:40]!r})")
    members = block.get("members")
    if isinstance(members, bool) or not isinstance(members, int) or not 1 <= members <= 100:
        raise ValueError("gateway block without a valid number of members")
    items = block.get("windows")
    if not isinstance(items, list):
        raise ValueError("gateway block without windows")
    by_end: dict[int, dict[str, int]] = {}
    for item in items:
        if not isinstance(item, dict) or item.get("window_hours") != window_hours:
            raise ValueError("gateway window is not an object of the expected length")
        values = item.get("probability_pct")
        if not isinstance(values, dict):
            raise ValueError("gateway window without probabilities")
        by_end[_epoch(item.get("time"))] = {key: _pct(values.get(key), key) for key in keys}
    if not by_end:
        return None
    return RawBlock(_epoch(block.get("run")), members, tuple((end, by_end[end]) for end in sorted(by_end)))


def parse_gateway_probabilities(payload: Any) -> RawProbabilities:
    """The blocks of a gateway answer. ``ValueError`` when it is not these models' or a value cannot be read or is impossible."""
    if not isinstance(payload, dict):
        raise ValueError("gateway answer is not an object")
    short = _parse_block(payload.get("arome"), SHORT_MODEL, 3, SHORT_KEYS)
    long = _parse_block(payload.get("arpege"), LONG_MODEL, 24, LONG_KEYS)
    return RawProbabilities(short, long)


def summarize(raw: RawProbabilities, now_s: float) -> Optional[Probabilities]:
    """The windows that are not entirely past at ``now_s``; ``None`` when none is left."""
    short: Optional[ShortRange] = None
    long: Optional[LongRange] = None
    peak: Optional[RainPeak] = None
    if raw.short is not None:
        rows = [(end, v) for end, v in raw.short.windows if end >= now_s]
        if rows:
            built = ShortRange(
                run_utc=raw.short.run_utc,
                members=raw.short.members,
                windows=[
                    ShortRangeWindow(
                        to_utc=end,
                        rain_1mm_pct=v["rain_1mm"],
                        rain_5mm_pct=v["rain_5mm"],
                        snow_1mm_pct=v["snow_1mm"],
                        gust_30kt_pct=v["gust_30kt"],
                        gust_50kt_pct=v["gust_50kt"],
                        hail_pct=v["hail"],
                        storm_pct=v["storm"],
                    )
                    for end, v in rows
                ],
            )
            short = built
            near = [w for w in built.windows if w.to_utc <= now_s + PEAK_HORIZON_S]
            if near:
                top = max(near, key=lambda w: w.rain_1mm_pct)  # max() keeps the first of equal ones: the soonest
                peak = RainPeak(rain_1mm_pct=top.rain_1mm_pct, to_utc=top.to_utc)
    if raw.long is not None:
        rows = [(end, v) for end, v in raw.long.windows if end >= now_s]
        if rows:
            built_long = LongRange(
                run_utc=raw.long.run_utc,
                members=raw.long.members,
                windows=[
                    LongRangeWindow(
                        to_utc=end,
                        rain_1mm_pct=v["rain_1mm"],
                        rain_5mm_pct=v["rain_5mm"],
                        rain_10mm_pct=v["rain_10mm"],
                        rain_20mm_pct=v["rain_20mm"],
                    )
                    for end, v in rows
                ],
            )
            long = built_long
    if short is None and long is None:
        return None
    return Probabilities(short_range=short, long_range=long, rain_peak_next_24h=peak, attribution=merge_credits(credit_text(PROVIDER_ID)), note=NOTE)


def compact(probabilities: Probabilities, now_s: float) -> Probabilities:
    """The profile that keeps a point under 16 KiB: of ``short_range`` only the windows that end within 24 hours."""
    if probabilities.short_range is None:
        return probabilities
    kept = [w for w in probabilities.short_range.windows if w.to_utc <= now_s + PEAK_HORIZON_S]
    return probabilities.model_copy(update={"short_range": probabilities.short_range.model_copy(update={"windows": kept})})


class MeteoFranceEnsembleCollector(BaseCollector[RawProbabilities]):
    """The ensemble probabilities of a point through the gateway, cached per cell of the forecast grid."""

    def __init__(
        self,
        base_url: str,
        grid_deg: float = DEFAULT_FORECAST_GRID_DEG,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(
            provider_id=PROVIDER_ID,
            name="Météo-France ensemble forecasts PE-AROME and PE-ARPEGE (vigie-meteo gateway)",
            domain="weather",
            ttl_seconds=TTL_SECONDS,
            failure_threshold=3,
            recovery_timeout_seconds=120,
            timeout_seconds=8.0,
            http_client=http_client,
            connection=SELF_HOSTED_CONNECTION,
        )
        self.base_url = base_url.rstrip("/")
        self.grid_deg = grid_deg

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> RawProbabilities:
        kwargs.pop("previous", None)
        lat, lon = float(kwargs["lat"]), float(kwargs["lon"])
        response = await self._get(
            client, f"{self.base_url}/v1/probabilities", params={"lat": coord_str(lat), "lon": coord_str(lon)}
        )
        if response.status_code == 404:
            return RawProbabilities(None, None)  # a point outside the maps: nothing to show, not a failure of the source
        response.raise_for_status()
        return parse_gateway_probabilities(response.json())

    async def probabilities(self, lat: float, lon: float) -> Optional[Probabilities]:
        """The probabilities at a point, or ``None`` (outside the maps, gateway down, runs too old, no window left)."""
        s_lat, s_lon = snap_to_grid(lat, lon, self.grid_deg)
        try:
            raw = await self.fetch(f"ensemble:{coord_str(s_lat)},{coord_str(s_lon)}", lat=s_lat, lon=s_lon)
        except Exception:
            return None
        # An expired entry is fine: the windows are checked against the clock, runs too old leave none.
        return summarize(raw, clock.wall())
