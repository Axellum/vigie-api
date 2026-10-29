"""The surf forecast of a spot: the providers' swell and wind, the tide computed by Vigie, and a rating of ours, explained.

The spots are ``app/data/surf-spots.json`` (``tools/build_spots.py``: OpenStreetMap, ODbL). For each hour of the days asked:
the sea state, the primary and secondary swell and the wind waves (MFWAM), the wind (Open-Meteo), the tide (EOT20), and what
Vigie works out from them, named in ``derived``. The rating gives each criterion its points by the thresholds of ``RULES``
(ours, not a surf school's), leaves out a criterion whose value is unknown and says so, and never guesses a value.

The overview (``overview``) gives every spot at once, from one request per kind for all of them (``SurfForecastCollector.fetch_all``):
the current hour in short and the best slot of the next three days. It is worked out once per hour and per answer of the providers.
"""

from __future__ import annotations

import asyncio
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Optional

import numpy as np

from app import clock
from app.attribution import SURF, credit_text
from app.collectors.surf import MARINE_MODEL, SurfForecastCollector
from app.models.surf import (
    Criterion,
    SpotEvidence,
    SurfBest,
    SurfCriterionScore,
    SurfForecast,
    SurfHour,
    SurfNow,
    SurfOverview,
    SurfRating,
    SurfScore,
    SurfSlot,
    SurfSpot,
    SurfSpotOverview,
    SurfSpots,
    SurfTide,
    SurfWind,
    Swell,
)
from app.services import tides

SPOTS_FILE = Path(__file__).resolve().parents[1] / "data" / "surf-spots.json"
HOUR = 3600
BEST_SLOT_HOURS = 72
MAX_DAYS = 7
OVERVIEW_DAYS = 3  # as the site asks a spot's page: the same hours, the same best slot
SWELL_MODEL = "MFWAM (Météo-France, Copernicus Marine), through Open-Meteo Marine"
# Deep-water wave power per metre of crest, P = rho g^2 H^2 T / (64 pi), in kW/m for H in m and T in s (rho = 1025 kg/m3).
POWER_FACTOR = 1025 * 9.81**2 / (64 * math.pi) / 1000

# The thresholds of the rating (ours). Each band is (upper bound, points); the last bound is infinite.
SWELL_HEIGHT_BANDS = ((0.5, 0), (1.0, 1), (2.5, 2), (4.0, 1), (math.inf, 0))
SWELL_PERIOD_BANDS = ((8.0, 0), (11.0, 1), (math.inf, 2))
SWELL_ANGLE_BANDS = ((30.0, 2), (60.0, 1), (math.inf, 0))
LIGHT_WIND_KMH = 10.0
OFFSHORE_MAX_KMH = 35.0
CROSS_SHORE_MAX_KMH = 20.0
SIDE_DEG = 45.0
MIN_OUT_OF_FOR_BEST = 6  # the best slot is chosen among hours where at least three criteria could be scored

RULES: dict[str, str] = {
    "swell_height": "primary swell under 0.5 m: 0; 0.5 to 1 m: 1; 1 to 2.5 m: 2; 2.5 to 4 m: 1; 4 m and over: 0",
    "swell_period": "under 8 s: 0; 8 to 11 s: 1; 11 s and over: 2",
    "swell_direction": "angle between where the swell comes from and the facing of the spot: up to 30 degrees: 2; up to 60: 1; more: 0",
    "wind": "under 10 km/h: 2; offshore (from the land side, within 45 degrees) under 35 km/h: 2; cross-shore under 20 km/h: 1; "
    "anything else: 0",
    "tide": "2 when the tide stage is the one the spot works best with, 0 otherwise; left out while that is unknown (every spot today)",
    "best_slot": "the daylight hour of the next 72 with the most points for what could be scored, at least three criteria; the first on a tie",
}
DERIVED = [
    "spot.facing_deg",
    "spot.place",
    "hours.daylight",
    "hours.swell_power_kw_m",
    "hours.swell_angle_deg",
    "hours.wind.relative",
    "hours.tide",
    "hours.rating",
    "best_slot",
]


class UnknownSpot(LookupError):
    """No spot has this id."""


class NoSurfForecast(RuntimeError):
    """The wave model gave nothing for this spot (the provider failed, or has no sea there)."""


@lru_cache(maxsize=1)
def spot_file() -> dict[str, Any]:
    return json.loads(SPOTS_FILE.read_text(encoding="utf-8"))


def _spot(raw: dict[str, Any]) -> SurfSpot:
    return SurfSpot(
        id=raw["id"],
        name=raw["name"],
        osm_name=raw.get("osm_name"),
        place=raw.get("place"),
        lat=raw["lat"],
        lon=raw["lon"],
        facing_deg=raw["facing_deg"],
        beach_surface=raw.get("beach_surface"),
        bottom=raw.get("bottom"),
        best_tide=raw.get("best_tide"),
        best_swell_from_deg=raw.get("best_swell_from_deg"),
        osm=raw["osm"],
        evidence=[SpotEvidence(**e) for e in raw.get("evidence", [])],
    )


def spots() -> SurfSpots:
    data = spot_file()
    listed = [_spot(raw) for raw in data["spots"]]
    return SurfSpots(
        count=len(listed),
        built_utc=data["built_utc"],
        spots=listed,
        derived=["facing_deg", "place", "id"],
        licence=data["licence"],
        attribution=credit_text("osm_spots"),
    )


def find_spot(spot_id: str) -> SurfSpot:
    for raw in spot_file()["spots"]:
        if raw["id"] == spot_id:
            return _spot(raw)
    raise UnknownSpot(spot_id)


def angle_between(a: float, b: float) -> float:
    """The smaller angle between two directions, 0 to 180 degrees."""
    d = abs(a - b) % 360.0
    return 360.0 - d if d > 180.0 else d


def wind_relative(from_deg: Optional[float], facing_deg: float) -> Optional[Literal["offshore", "cross-shore", "onshore"]]:
    """Ours: offshore when the wind comes from the land side (within ``SIDE_DEG`` of the facing turned round), onshore when it
    comes from the sea side, cross-shore otherwise."""
    if from_deg is None:
        return None
    if angle_between(from_deg, facing_deg + 180.0) <= SIDE_DEG:
        return "offshore"
    if angle_between(from_deg, facing_deg) <= SIDE_DEG:
        return "onshore"
    return "cross-shore"


def swell_power_kw_m(height_m: Optional[float], period_s: Optional[float]) -> Optional[float]:
    if height_m is None or period_s is None:
        return None
    return round(POWER_FACTOR * height_m**2 * period_s, 1)


def _band(value: float, bands: tuple[tuple[float, int], ...]) -> int:
    return next(points for bound, points in bands if value < bound)


def rate(
    swell: Swell, swell_angle: Optional[float], wind: SurfWind, tide: Optional[SurfTide], best_tide: Optional[str]
) -> Optional[SurfRating]:
    """The rating of one hour (ours, ``RULES``); ``None`` without the height of the primary swell."""
    if swell.height_m is None:
        return None
    scores: list[SurfCriterionScore] = []
    unknown: list[Criterion] = []

    def score(name: Criterion, value: Optional[float], unit: str, points: Optional[int]) -> None:
        scores.append(SurfCriterionScore(name=name, value=value, unit=unit, points=points, max_points=2))
        if points is None:
            unknown.append(name)

    score("swell_height", swell.height_m, "m", _band(swell.height_m, SWELL_HEIGHT_BANDS))
    score("swell_period", swell.period_s, "s", None if swell.period_s is None else _band(swell.period_s, SWELL_PERIOD_BANDS))
    score("swell_direction", None if swell_angle is None else round(swell_angle), "deg",
          None if swell_angle is None else _band(swell_angle, SWELL_ANGLE_BANDS))
    if wind.speed_kmh is None or (wind.relative is None and wind.speed_kmh >= LIGHT_WIND_KMH):
        wind_points: Optional[int] = None
    elif wind.speed_kmh < LIGHT_WIND_KMH:
        wind_points = 2
    elif wind.relative == "offshore" and wind.speed_kmh < OFFSHORE_MAX_KMH:
        wind_points = 2
    elif wind.relative == "cross-shore" and wind.speed_kmh < CROSS_SHORE_MAX_KMH:
        wind_points = 1
    else:
        wind_points = 0
    score("wind", wind.speed_kmh, "km/h", wind_points)
    if tide is None or tide.stage is None or best_tide is None:
        tide_points: Optional[int] = None
    else:
        tide_points = 2 if best_tide in ("any", tide.stage) else 0
    score("tide", None if tide is None else tide.height_m, "m", tide_points)
    known = [s for s in scores if s.points is not None]
    return SurfRating(
        points=sum(s.points or 0 for s in known),
        out_of=sum(s.max_points for s in known),
        criteria=scores,
        unknown=unknown,
    )


def best_slot(hours: list[SurfHour], now_unix: float) -> Optional[SurfSlot]:
    """Ours: the daylight hour of the next ``BEST_SLOT_HOURS`` with the best share of points (the first one on a tie)."""
    best: Optional[tuple[float, SurfHour]] = None
    for hour in hours:
        rating = hour.rating
        if hour.daylight is not True or rating is None or rating.out_of < MIN_OUT_OF_FOR_BEST:
            continue
        if not now_unix - HOUR < hour.time_utc <= now_unix + BEST_SLOT_HOURS * HOUR:
            continue
        share = rating.points / rating.out_of
        if best is None or share > best[0]:
            best = (share, hour)
    if best is None or best[1].rating is None:
        return None
    return SurfSlot(time_utc=best[1].time_utc, rating=best[1].rating)


def _round(value: Optional[float], digits: int) -> Optional[float]:
    return None if value is None else round(value, digits)


def _direction(value: Optional[float]) -> Optional[int]:
    return None if value is None else int(round(value)) % 360


def _daylight(t: int, wind: Optional[dict[str, Any]]) -> Optional[bool]:
    if wind is None:
        return None
    pairs = list(zip(wind.get("sunrise", []), wind.get("sunset", []), strict=False))
    if not pairs or any(rise is None or sets is None for rise, sets in pairs):
        return None
    return any(rise <= t < sets for rise, sets in pairs)


def _tides(spot: SurfSpot, times: list[int]) -> dict[int, SurfTide]:
    """The tide at the spot for each hour: height, trend and stage (ours); empty where the model has no cell near."""
    if not times:
        return {}
    model = tides.model()
    try:
        cell = model.nearest_cell(spot.lat, spot.lon)
        start, end = tides.mjd_from_unix(times[0]) - 1.0, tides.mjd_from_unix(times[-1]) + 1.0
        curve = np.arange(start, end, tides.MINUTE)
        heights = model.heights(cell.constants, curve)
    except (tides.NoTideHere, ValueError):
        return {}
    extremes = tides.find_extremes(curve, heights)
    out: dict[int, SurfTide] = {}
    for t in times:
        mjd = tides.mjd_from_unix(t)
        i = int(np.clip(np.searchsorted(curve, mjd), 1, len(curve) - 1))
        height = float(np.interp(mjd, curve, heights))
        trend: Literal["rising", "falling"] = "rising" if heights[i] >= heights[i - 1] else "falling"
        before = next((e for e in reversed(extremes) if e.mjd <= mjd), None)
        after = next((e for e in extremes if e.mjd > mjd), None)
        stage: Optional[Literal["low", "mid", "high"]] = None
        if before is not None and after is not None and before.kind != after.kind:
            low = min(before.height_m, after.height_m)
            high = max(before.height_m, after.height_m)
            if high - low > 0.05:
                share = (height - low) / (high - low)
                stage = "low" if share < 1 / 3 else "high" if share > 2 / 3 else "mid"
        out[t] = SurfTide(height_m=round(height, 2), trend=trend, stage=stage)
    return out


def _at(series: dict[str, Any], name: str, index: Optional[int]) -> Optional[float]:
    if index is None:
        return None
    values = series.get(name) or []
    return values[index] if index < len(values) else None


def build_forecast(
    spot: SurfSpot,
    swell: dict[str, Any],
    wind: Optional[dict[str, Any]],
    days: int,
    now_unix: float,
    stale: bool = False,
) -> SurfForecast:
    """The forecast of ``spot`` from the parsed answers (``app/collectors/surf.py``): the hours from the current one, for ``days``."""
    first = int(now_unix // HOUR) * HOUR
    wanted = [t for t in swell["times"] if first <= t < first + days * 24 * HOUR]
    swell_index = {t: i for i, t in enumerate(swell["times"])}
    wind_index = {t: i for i, t in enumerate(wind["times"])} if wind is not None else {}
    tide_at = _tides(spot, wanted)
    hours: list[SurfHour] = []
    for t in wanted:
        s, w = swell_index.get(t), wind_index.get(t)
        primary = Swell(
            height_m=_round(_at(swell, "swell_wave_height", s), 2),
            period_s=_round(_at(swell, "swell_wave_period", s), 1),
            from_deg=_direction(_at(swell, "swell_wave_direction", s)),
        )
        wind_from = _direction(_at(wind, "wind_direction_10m", w)) if wind is not None else None
        surf_wind = SurfWind(
            speed_kmh=_round(_at(wind, "wind_speed_10m", w), 1) if wind is not None else None,
            gust_kmh=_round(_at(wind, "wind_gusts_10m", w), 1) if wind is not None else None,
            from_deg=wind_from,
            relative=wind_relative(wind_from, spot.facing_deg),
        )
        angle = None if primary.from_deg is None else angle_between(primary.from_deg, spot.facing_deg)
        tide = tide_at.get(t)
        hours.append(
            SurfHour(
                time_utc=t,
                daylight=_daylight(t, wind),
                waves=Swell(
                    height_m=_round(_at(swell, "wave_height", s), 2),
                    period_s=_round(_at(swell, "wave_period", s), 1),
                    from_deg=_direction(_at(swell, "wave_direction", s)),
                ),
                swell=primary,
                secondary_swell=Swell(
                    height_m=_round(_at(swell, "secondary_swell_wave_height", s), 2),
                    period_s=_round(_at(swell, "secondary_swell_wave_period", s), 1),
                    from_deg=_direction(_at(swell, "secondary_swell_wave_direction", s)),
                ),
                wind_wave_height_m=_round(_at(swell, "wind_wave_height", s), 2),
                swell_power_kw_m=swell_power_kw_m(primary.height_m, primary.period_s),
                swell_angle_deg=None if angle is None else int(round(angle)),
                wind=surf_wind,
                tide=tide,
                rating=rate(primary, angle, surf_wind, tide, spot.best_tide),
            )
        )
    cell = None
    if swell.get("model_lat") is not None and swell.get("model_lon") is not None:
        cell = {"lat": swell["model_lat"], "lon": swell["model_lon"]}
    return SurfForecast(
        generated_utc=int(now_unix),
        spot=spot,
        days=days,
        swell_model=SWELL_MODEL,
        swell_cell=cell,
        hours=hours,
        best_slot=best_slot(hours, now_unix),
        rules=RULES,
        derived=DERIVED,
        stale=stale,
        attribution=credit_text(*SURF),
    )


async def forecast(collector: SurfForecastCollector, spot_id: str, days: int, now_unix: float) -> SurfForecast:
    """The forecast of a spot. ``UnknownSpot`` for an id that is not in the list; ``NoSurfForecast`` when the swell cannot be had
    (the wind alone is not a surf forecast: without it, the wind is unknown and the rating says so)."""
    spot = find_spot(spot_id)
    swell, wind = await asyncio.gather(
        collector.fetch_swell(spot.id, spot.lat, spot.lon),
        collector.fetch_wind(spot.id, spot.lat, spot.lon),
        return_exceptions=True,
    )
    if isinstance(swell, BaseException):
        raise NoSurfForecast(f"the wave model gave no answer for this spot ({type(swell).__name__})")
    swell_data, swell_stale = swell
    wind_data: Optional[dict[str, Any]] = None
    wind_stale = False
    if not isinstance(wind, BaseException):
        wind_data, wind_stale = wind
    # The tide and the rating are computed in a worker thread: a few milliseconds of numpy, kept off the event loop.
    return await clock.run_blocking(build_forecast, spot, swell_data, wind_data, days, now_unix, swell_stale or wind_stale)


def _now(hour: SurfHour) -> SurfNow:
    rating = hour.rating
    return SurfNow(
        time_utc=hour.time_utc,
        daylight=hour.daylight,
        swell=hour.swell,
        wind=hour.wind,
        tide=hour.tide,
        rating=None if rating is None else SurfScore(points=rating.points, out_of=rating.out_of, unknown=rating.unknown),
    )


def build_overview(
    swell: dict[str, dict[str, Any]], wind: Optional[dict[str, dict[str, Any]]], now_unix: float, stale: bool = False
) -> SurfOverview:
    """Every spot in short from the parsed every-spot answers: its current hour and its best slot (``null`` where unknown)."""
    current = int(now_unix // HOUR) * HOUR
    listed: list[SurfSpotOverview] = []
    for raw in spot_file()["spots"]:
        spot = _spot(raw)
        now: Optional[SurfNow] = None
        best: Optional[SurfBest] = None
        if spot.id in swell:
            built = build_forecast(spot, swell[spot.id], (wind or {}).get(spot.id), OVERVIEW_DAYS, now_unix)
            first = built.hours[0] if built.hours else None
            if first is not None and first.time_utc == current:
                now = _now(first)
            if built.best_slot is not None:
                slot = built.best_slot
                best = SurfBest(time_utc=slot.time_utc, points=slot.rating.points, out_of=slot.rating.out_of)
        listed.append(
            SurfSpotOverview(
                id=spot.id,
                name=spot.name,
                place=spot.place,
                lat=spot.lat,
                lon=spot.lon,
                facing_deg=spot.facing_deg,
                now=now,
                best_slot=best,
            )
        )
    return SurfOverview(
        generated_utc=int(now_unix),
        count=len(listed),
        spots=listed,
        swell_model=SWELL_MODEL,
        rules=RULES,
        derived=["spots.facing_deg", "spots.place", "spots.now.wind.relative", "spots.now.tide", "spots.now.rating", "spots.best_slot"],
        stale=stale,
        attribution=credit_text(*SURF),
    )


# The last overview worked out: the answers it was made from, the hour, and the overview.
_overview_memo: Optional[tuple[object, object, int, SurfOverview]] = None


async def overview(collector: SurfForecastCollector, now_unix: float) -> SurfOverview:
    """Every spot in short. ``NoSurfForecast`` when the swell of no spot can be had."""
    global _overview_memo
    points = tuple((raw["id"], raw["lat"], raw["lon"]) for raw in spot_file()["spots"])
    swell, wind = await asyncio.gather(
        collector.fetch_all("swell", points), collector.fetch_all("wind", points), return_exceptions=True
    )
    if isinstance(swell, BaseException):
        raise NoSurfForecast(f"the wave model gave no answer for the spots ({type(swell).__name__})")
    swell_data, swell_stale = swell
    wind_data: Optional[dict[str, dict[str, Any]]] = None
    wind_stale = False
    if not isinstance(wind, BaseException):
        wind_data, wind_stale = wind
    hour = int(now_unix // HOUR)
    memo = _overview_memo
    if memo is not None and memo[0] is swell_data and memo[1] is wind_data and memo[2] == hour:
        return memo[3]
    # 51 spots of tide and rating: a fraction of a second of numpy, in a worker thread.
    built = await clock.run_blocking(build_overview, swell_data, wind_data, now_unix, swell_stale or wind_stale)
    _overview_memo = (swell_data, wind_data, hour, built)
    return built


__all__ = [
    "MARINE_MODEL",
    "MAX_DAYS",
    "NoSurfForecast",
    "UnknownSpot",
    "build_forecast",
    "build_overview",
    "forecast",
    "find_spot",
    "overview",
    "spots",
]
