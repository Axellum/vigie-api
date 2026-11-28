"""The weather of the place and what the dashboard card shows (option "Weather and card", ADR 0042 of the API).

One coordinator reads ``/v1/point`` (the full answer, with the sea) every 10 minutes. At the same reads, and far less often, it
reads the tide (``/v1/tides``; 404 inland, then nothing is asked again for hours), the normals of the month
(``/v1/climate/normals``; France and its overseas territories only) and, where the normals exist, the wind measured by the
nearest Météo-France station (``/v1/stations/wind``) and the forest fire danger of the department (``/v1/forest-fire``, every
hour; Météo-France publishes it from June to September). The spots and their verdicts are read when the card asks for them
(``/v1/surf/spots``, ``/v1/activities/{spot}``), and kept for a while. So are the beaches of the card's Sea page: the forecast
of the spot (``/v1/surf/spots/{spot}``), the water at its cell (``/v1/point`` with the sea) and its monitored bathing site
(``/v1/sea/places``); and the stations of the card's Mountain page: the weather at their top and at their base
(``/v1/point?elevation=``) and their avalanche bulletin (``/v1/avalanches``); and the card's Map page: every spot with its
verdicts and the figures of its hour (``/v1/activities/overview``, ``/v1/surf/overview``) and the places of the French coast
(``/v1/sea/places``, the whole catalogue). Each is trimmed to what its page draws.

Nothing here makes up a weather value: what the API did not give stays unknown. The few numbers worked out here (a sum of the
rain steps of the next hour, the strongest gust among the next hours, the next high water) are read from the answer as it is,
and their attributes say which part of it they come from.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from astral import Observer
from astral.sun import elevation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import VigieClient, VigieError, VigieNotFoundError
from .const import (
    ACTIVITIES_REFRESH,
    BATHING_ASK_KM,
    BATHING_NEAR_KM,
    BATHING_REFRESH,
    BEACH_HOURS,
    BEACH_REFRESH,
    DOMAIN,
    FOREST_FIRE_REFRESH,
    MAP_REFRESH,
    MAX_BEACHES,
    MAX_LISTED_SPOTS,
    MAX_PLACES,
    MAX_SPOT_DISTANCE_KM,
    MAX_STATIONS,
    MOUNTAIN_REFRESH,
    NORMALS_REFRESH,
    PLACES_PAGE,
    PLACES_REFRESH,
    SPOTS_REFRESH,
    STATION_BOX_DEG,
    TIDES_REFRESH,
    WEATHER_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

# WMO weather interpretation codes (the `code` of the API's daily forecast and `weather_code` of its current weather) and the
# conditions of Home Assistant's weather entity. A code that is not in this table has no condition (unknown), never a guess.
_CONDITION_OF_CODE: dict[int, str] = {
    0: "sunny",
    1: "sunny",
    2: "partlycloudy",
    3: "cloudy",
    45: "fog",
    48: "fog",
    51: "rainy",
    53: "rainy",
    55: "rainy",
    56: "snowy-rainy",
    57: "snowy-rainy",
    61: "rainy",
    63: "rainy",
    65: "pouring",
    66: "snowy-rainy",
    67: "snowy-rainy",
    71: "snowy",
    73: "snowy",
    75: "snowy",
    77: "snowy",
    80: "rainy",
    81: "rainy",
    82: "pouring",
    85: "snowy",
    86: "snowy",
    95: "lightning-rainy",
    96: "lightning-rainy",
    99: "lightning-rainy",
}

GUST_WINDOW_S = 6 * 3600


def condition_of(code: Any, is_day: bool | None) -> str | None:
    """The Home Assistant condition of a WMO code; a clear sky at night is ``clear-night``. ``None`` for an unknown code."""
    if isinstance(code, bool) or not isinstance(code, int):
        return None
    condition = _CONDITION_OF_CODE.get(code)
    if condition == "sunny" and is_day is False:
        return "clear-night"
    return condition


def is_day_at(lat: float, lon: float, when: datetime) -> bool:
    """The sun is up at the place (its centre above the horizon, refraction included): day for the icon of a clear sky."""
    return elevation(Observer(lat, lon), when) > -0.833


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def rain_next_hour(point: dict[str, Any] | None) -> float | None:
    """The rain of the four steps of `rain_nowcast.mm_per_step` (mm), unknown when one of them is.

    They start at `rain_nowcast.start_utc` and the hour they cover depends on the source (the API's `RainNowcast`): with
    Météo-France PIAF, the hour from the 5-minute boundary at which the API read the rain; with Open-Meteo, the quarter-hour that
    ended when the provider answered, then the next three.
    """
    nowcast = point.get("rain_nowcast") if isinstance(point, dict) else None
    steps = nowcast.get("mm_per_step") if isinstance(nowcast, dict) else None
    if not isinstance(steps, list) or not steps:
        return None
    values = [_number(step) for step in steps]
    if any(value is None for value in values):
        return None
    return round(sum(values), 2)  # type: ignore[arg-type]


# The slots of the former Météo-France "rain in the hour" (start and length in minutes): 5 minutes for the first half hour, then
# 10 minutes. Each 10-minute slot keeps the stronger of its two 5-minute rates.
RAIN_SLOTS: tuple[tuple[int, int], ...] = ((0, 5), (5, 5), (10, 5), (15, 5), (20, 5), (25, 5), (30, 10), (40, 10), (50, 10))


def rain_slots(point: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    """The next hour in the nine slots of the former Météo-France API, from the twelve 5-minute rates (mm/h) of
    `rain_nowcast.rate_5m_mm_h` (Météo-France PIAF, metropolitan France only). Each slot: `start` (ISO, UTC), `minutes` (its
    length) and `mm_h`. The first slot starts at `rain_nowcast.start_utc`, the 5-minute boundary at which the API read the rain
    (from an older API without it, `server_utc` floored to 5 minutes).
    `None` without the twelve rates (outside France, a rate unknown): never a dry hour made up."""
    nowcast = point.get("rain_nowcast") if isinstance(point, dict) else None
    rates = nowcast.get("rate_5m_mm_h") if isinstance(nowcast, dict) else None
    start_utc = _number(nowcast.get("start_utc")) if isinstance(nowcast, dict) else None
    server_utc = _number(point.get("server_utc")) if isinstance(point, dict) else None
    if not isinstance(rates, list) or len(rates) != 12 or (start_utc is None and server_utc is None):
        return None
    values = [_number(rate) for rate in rates]
    if any(value is None for value in values):
        return None
    first = int(start_utc) if start_utc is not None else int(server_utc) // 300 * 300  # type: ignore[arg-type]
    slots = []
    for start, minutes in RAIN_SLOTS:
        steps = values[start // 5 : (start + minutes) // 5]
        slots.append(
            {
                "start": datetime.fromtimestamp(first + start * 60, tz=UTC).isoformat(),
                "minutes": minutes,
                "mm_h": max(steps),  # type: ignore[type-var]
            }
        )
    return slots


@dataclass(frozen=True, slots=True)
class Gust:
    """The strongest gust among the next hours, and where in the answer it was read."""

    kmh: float
    at_utc: int
    source: str  # "hourly" (the hourly forecast, 6 hours) or "short_term" (AROME-PI, its own horizon)
    horizon_minutes: int


def strongest_gust(point: dict[str, Any] | None, now_utc: int) -> Gust | None:
    """The strongest gust of the next 6 hours of the hourly forecast; without it, of the short-term forecast (AROME-PI, France).

    Unknown when neither gives a gust: no gust is taken from the mean wind.
    """
    if not isinstance(point, dict):
        return None
    hourly = point.get("hourly")
    if isinstance(hourly, list) and hourly:
        best: Gust | None = None
        for hour in hourly:
            if not isinstance(hour, dict):
                continue
            at = hour.get("time_utc")
            gust = _number(hour.get("wind_gust_kmh"))
            if not isinstance(at, int) or gust is None or not now_utc - 3600 < at < now_utc + GUST_WINDOW_S:
                continue
            if best is None or gust > best.kmh:
                best = Gust(gust, at, "hourly", GUST_WINDOW_S // 60)
        if best is not None:
            return best
    short = point.get("short_term")
    steps = short.get("steps") if isinstance(short, dict) else None
    if isinstance(short, dict) and isinstance(steps, list):
        best = None
        horizon_raw = short.get("horizon_minutes")
        horizon = horizon_raw if isinstance(horizon_raw, int) else 0
        for step in steps:
            if not isinstance(step, dict):
                continue
            at = step.get("time_utc")
            gust = _number(step.get("wind_gust_kmh"))
            if not isinstance(at, int) or gust is None or at < now_utc - 900:
                continue
            if best is None or gust > best.kmh:
                best = Gust(gust, at, "short_term", horizon)
        return best
    return None


def next_extreme(tides: dict[str, Any] | None, kind: str, now_utc: int) -> dict[str, Any] | None:
    """The next high (``kind="high"``) or low water of the answer of ``/v1/tides`` after ``now_utc``."""
    extremes = tides.get("extremes") if isinstance(tides, dict) else None
    if not isinstance(extremes, list):
        return None
    for extreme in extremes:
        if isinstance(extreme, dict) and extreme.get("kind") == kind and isinstance(extreme.get("time_utc"), int) and extreme["time_utc"] > now_utc:
            return extreme
    return None


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine, mean Earth radius)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = phi2 - phi1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(a)))


def nearest_station(answer: Any, lat: float, lon: float) -> dict[str, Any] | None:
    """The station of an answer of ``/v1/stations/wind`` nearest to the place, with its distance (calculated here) and the credit."""
    stations = answer.get("stations") if isinstance(answer, dict) else None
    if not isinstance(stations, list):
        return None
    best: tuple[float, dict[str, Any]] | None = None
    for station in stations:
        if not isinstance(station, dict):
            continue
        s_lat, s_lon = _number(station.get("lat")), _number(station.get("lon"))
        if s_lat is None or s_lon is None:
            continue
        distance = distance_km(lat, lon, s_lat, s_lon)
        if best is None or distance < best[0]:
            best = (distance, station)
    if best is None:
        return None
    return {**best[1], "distance_km": round(best[0], 1), "attribution": answer.get("attribution")}


def nearby_spots(answer: Any, lat: float, lon: float) -> list[dict[str, Any]]:
    """The spots of an answer of ``/v1/surf/spots`` within ``MAX_SPOT_DISTANCE_KM`` of the place, nearest first (at most a dozen)."""
    spots = answer.get("spots") if isinstance(answer, dict) else None
    if not isinstance(spots, list):
        return []
    found: list[dict[str, Any]] = []
    for spot in spots:
        if not isinstance(spot, dict) or not isinstance(spot.get("id"), str):
            continue
        s_lat, s_lon = _number(spot.get("lat")), _number(spot.get("lon"))
        if s_lat is None or s_lon is None:
            continue
        distance = distance_km(lat, lon, s_lat, s_lon)
        if distance <= MAX_SPOT_DISTANCE_KM:
            found.append({"id": spot["id"], "name": spot.get("name"), "place": spot.get("place"), "distance_km": round(distance, 1)})
    found.sort(key=lambda spot: spot["distance_km"])
    return found[:MAX_LISTED_SPOTS]


def spot_of(answer: Any, spot_id: str, lat: float, lon: float) -> dict[str, Any] | None:
    """A spot of an answer of ``/v1/surf/spots``, wherever it is, with its distance from the place; ``None`` for an id the
    instance does not have (a beach kept with a star on the Map page can be far from the place)."""
    spots = answer.get("spots") if isinstance(answer, dict) else None
    for spot in spots if isinstance(spots, list) else ():
        if isinstance(spot, dict) and spot.get("id") == spot_id:
            s_lat, s_lon = _number(spot.get("lat")), _number(spot.get("lon"))
            distance = round(distance_km(lat, lon, s_lat, s_lon), 1) if s_lat is not None and s_lon is not None else None
            return {"id": spot_id, "name": spot.get("name"), "place": spot.get("place"), "distance_km": distance}
    return None


# --------------------------------------------------------------------------------------------------------------- the map
# The four activities of the Map page, in the order of its buttons, and what it draws of the hour of a spot.
MAP_ACTIVITIES = ("surf", "kite", "boat", "shore_fishing")
_VERDICTS = ("good", "possible", "poor")
_MAP_ANSWER_KEEP = ("generated_utc", "stale", "wind_range", "limits", "disclaimer", "attribution")


def _rounded(value: Any, digits: int) -> float | int | None:
    """``value`` rounded to ``digits`` (a whole number for none); ``None`` when it is not a finite number."""
    number = _number(value)
    if number is None:
        return None
    return round(number) if digits == 0 else round(number, digits)


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _map_now(now: Any) -> dict[str, Any] | None:
    """The hour under way of a spot of ``/v1/surf/overview``: its swell, its wind and its tide, as the API gave them."""
    if not isinstance(now, dict):
        return None
    swell = now.get("swell") if isinstance(now.get("swell"), dict) else {}
    wind = now.get("wind") if isinstance(now.get("wind"), dict) else {}
    tide = now.get("tide") if isinstance(now.get("tide"), dict) else {}
    return {
        "time_utc": now.get("time_utc") if isinstance(now.get("time_utc"), int) else None,
        "swell_m": _rounded(swell.get("height_m"), 1),
        "period_s": _rounded(swell.get("period_s"), 0),
        "swell_from_deg": _rounded(swell.get("from_deg"), 0),
        "wind_kmh": _rounded(wind.get("speed_kmh"), 0),
        "gust_kmh": _rounded(wind.get("gust_kmh"), 0),
        "wind_from_deg": _rounded(wind.get("from_deg"), 0),
        "wind_relative": _text(wind.get("relative")),
        "tide_trend": _text(tide.get("trend")),
        "tide_stage": _text(tide.get("stage")),
    }


def _verdicts(spot: dict[str, Any]) -> dict[str, str | None]:
    found = {activity.get("id"): activity for activity in spot.get("activities") or [] if isinstance(activity, dict)}
    kept: dict[str, str | None] = {}
    for name in MAP_ACTIVITIES:
        activity = found.get(name) or {}
        verdict = activity.get("verdict")
        kept[name] = verdict if activity.get("available") is not False and verdict in _VERDICTS else None
    return kept


def _list_of(answer: Any, key: str) -> list[Any]:
    items = answer.get(key) if isinstance(answer, dict) else None
    return items if isinstance(items, list) else []


def map_spots(spots: Any, overview: Any, surf: Any) -> dict[str, Any]:
    """What the Map page draws of every spot: where it is and where it faces (``/v1/surf/spots``), the verdict of each
    activity, the best of the daylight hours of the next 24 (``/v1/activities/overview``, the verdict of the website's pins),
    and the figures of the hour under way (``/v1/surf/overview``). A verdict or a figure the API did not give is ``None``; a
    spot the list of spots does not have is left out (it has no place to be drawn)."""
    verdicts = {spot["id"]: _verdicts(spot) for spot in _list_of(overview, "spots") if isinstance(spot, dict) and isinstance(spot.get("id"), str)}
    hours = {spot["id"]: _map_now(spot.get("now")) for spot in _list_of(surf, "spots") if isinstance(spot, dict) and isinstance(spot.get("id"), str)}
    drawn = []
    for spot in _list_of(spots, "spots"):
        if not isinstance(spot, dict) or not isinstance(spot.get("id"), str):
            continue
        lat, lon = _rounded(spot.get("lat"), 5), _rounded(spot.get("lon"), 5)
        if lat is None or lon is None:
            continue
        drawn.append(
            {
                "id": spot["id"],
                "name": _text(spot.get("name")),
                "place": _text(spot.get("place")),
                "lat": lat,
                "lon": lon,
                "facing_deg": _rounded(spot.get("facing_deg"), 0),
                "verdicts": verdicts.get(spot["id"], dict.fromkeys(MAP_ACTIVITIES)),
                "now": hours.get(spot["id"]),
            }
        )
    answer = overview if isinstance(overview, dict) else {}
    return {
        **{key: answer.get(key) for key in _MAP_ANSWER_KEEP},
        "verdicts_read": isinstance(overview, dict),
        "hour_read": isinstance(surf, dict),
        "hour_attribution": surf.get("attribution") if isinstance(surf, dict) else None,
        "spots": drawn,
    }


# A place of the catalogue, as the Map page receives it: a short list rather than an object, there are thousands.
PLACE_FIELDS = ("id", "type", "lat", "lon", "name", "near", "quality")
_PLACES_KEEP = ("count", "by_type", "built_utc", "note", "licence", "attribution")


def map_place(place: Any) -> list[Any] | None:
    """A place of ``/v1/sea/places`` as ``PLACE_FIELDS``; ``None`` without a kind or a position."""
    if not isinstance(place, dict) or not isinstance(place.get("id"), str) or not isinstance(place.get("type"), str):
        return None
    lat, lon = _rounded(place.get("lat"), 5), _rounded(place.get("lon"), 5)
    if lat is None or lon is None:
        return None
    return [place["id"], place["type"], lat, lon, _text(place.get("name")), _text(place.get("near")), _text(place.get("quality"))]


@dataclass(frozen=True, slots=True)
class WeatherData:
    """What one read of the weather of the place gave. ``None`` is "not there" (inland, outside France) or "not read yet"."""

    point: dict[str, Any]
    tides: dict[str, Any] | None
    normals: dict[str, Any] | None
    station_wind: dict[str, Any] | None
    read_utc: int
    forest_fire: dict[str, Any] | None = None


class _Slow:
    """A part read far less often than the weather: kept for ``period``; a 404 is kept as "nothing there" for the same time; a
    failure keeps the last value and is asked again at the next read."""

    def __init__(self, period: timedelta) -> None:
        self.period_s = period.total_seconds()
        self.value: Any = None
        self.read_at: float | None = None

    async def get(self, fetch: Callable[[], Awaitable[Any]]) -> Any:
        if self.read_at is not None and time.monotonic() - self.read_at < self.period_s:
            return self.value
        try:
            self.value = await fetch()
        except VigieNotFoundError:
            self.value = None
        except VigieError as err:
            _LOGGER.debug("Kept the last value, the read failed: %s", err)
            return self.value
        self.read_at = time.monotonic()
        return self.value


# What the Mountain page draws of an answer of `/v1/point` (the rest of it, the sea, the air, the nowcast, is not sent to the card).
_POINT_KEEP = ("coordinates", "utc_offset_s", "server_utc", "weather_provider", "weather_attribution")
_CURRENT_KEEP = ("temp_c", "feels_like_c", "weather_code", "wind_kmh", "wind_deg", "gust_kmh")
_HOUR_KEEP = (
    "time_utc", "temp_c", "precipitation_mm", "precipitation_probability_pct", "weather_code", "is_day", "wind_kmh", "wind_deg",
    "wind_gust_kmh", "freezing_level_m",
)
_DAY_KEEP = ("date", "code", "tmin", "tmax", "rain_mm", "rain_probability_pct", "snowfall_cm", "wind_max", "sunshine_h")
# And of an answer of `/v1/avalanches`: the bulletins in force, without their long texts.
_AVALANCHE_KEEP = ("status", "covered_by", "region_id", "region_name", "max_danger_level", "max_danger_label", "stale", "attribution")
_BULLETIN_KEEP = (
    "source", "lang", "valid_from", "valid_until", "max_danger_level", "danger_ratings", "avalanche_activity_highlights", "bulletin_url",
    "attribution",
)
_PROBLEM_KEEP = ("problem_type", "danger_level", "valid_time_period", "elevation", "aspects")
_TENDENCY_KEEP = ("tendency_type", "valid_from", "highlights")


def _pick(item: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: item[key] for key in keys if key in item} if isinstance(item, dict) else {}


def mountain_point(answer: Any, elevation_m: int | None) -> dict[str, Any] | None:
    """What the Mountain page draws of an answer of ``/v1/point`` asked at ``elevation_m``: ``None`` when it is not one, or when
    the instance did not take the altitude (an instance older than the parameter answers at the altitude of its cell, which
    would draw the base and the top alike)."""
    if not isinstance(answer, dict) or not isinstance(answer.get("daily"), list):
        return None
    coordinates = answer.get("coordinates")
    if elevation_m is not None and (not isinstance(coordinates, dict) or coordinates.get("elevation_m") != elevation_m):
        return None
    hourly = answer.get("hourly")
    return {
        **_pick(answer, _POINT_KEEP),
        "current": _pick(answer.get("current"), _CURRENT_KEEP),
        "hourly": [_pick(hour, _HOUR_KEEP) for hour in hourly if isinstance(hour, dict)] if isinstance(hourly, list) else None,
        "daily": [_pick(day, _DAY_KEEP) for day in answer["daily"] if isinstance(day, dict)],
    }


def mountain_avalanches(answer: Any) -> dict[str, Any] | None:
    """What the Mountain page draws of an answer of ``/v1/avalanches``: its status and the bulletins in force, without their
    long texts (the page links to the official bulletin)."""
    if not isinstance(answer, dict) or not isinstance(answer.get("status"), str):
        return None
    bulletins = answer.get("bulletins") if isinstance(answer.get("bulletins"), list) else []
    return {
        **_pick(answer, _AVALANCHE_KEEP),
        "bulletins": [
            {
                **_pick(bulletin, _BULLETIN_KEEP),
                "avalanche_problems": [_pick(problem, _PROBLEM_KEEP) for problem in bulletin.get("avalanche_problems") or [] if isinstance(problem, dict)],
                "tendency": [_pick(tendency, _TENDENCY_KEEP) for tendency in bulletin.get("tendency") or [] if isinstance(tendency, dict)],
            }
            for bulletin in bulletins
            if isinstance(bulletin, dict) and not bulletin.get("is_expired")
        ],
    }


# --------------------------------------------------------------------------------------------------------------- beaches
# What the Sea page draws of a beach. The spot: its place, its facing and bottom (the coast of the baïnes), its lifeguards.
_SPOT_KEEP = ("id", "name", "place", "lat", "lon", "facing_deg", "bottom", "beach_surface", "lifeguarded")
_SEA_HOUR_KEEP = ("time_utc", "daylight", "waves", "swell", "wind", "tide")
_FORECAST_KEEP = ("generated_utc", "swell_model", "stale", "attribution")
_ACTIVITY_KEEP = ("id", "available", "verdict", "reason", "best_slot")
_ACTIVITY_HOUR_KEEP = ("time_utc", "daylight", "verdict", "reason")
_ACTIVITIES_KEEP = ("generated_utc", "sun", "vigilance", "stale", "attribution")
_WATER_KEEP = ("sea_surface_temperature_c", "sea_state_label", "wave_height_m", "stale", "attribution")
_BATHING_KEEP = ("id", "name", "commune", "quality", "lat", "lon", "ref")
# Closer than this, a bathing site is the beach's whatever its side; farther, one behind the beach (within 45 degrees of straight
# inland) is a lake or a pond, not the sea of the beach (the rule of the site, vigie-web `src/lib/sea/bathing.ts`).
_SAME_PLACE_KM = 0.2
_INLAND_DEG = 135.0


def _in_window(time_utc: Any, now_utc: int) -> bool:
    return isinstance(time_utc, int | float) and now_utc - 3600 <= time_utc <= now_utc + BEACH_HOURS * 3600


def beach_forecast(answer: Any, now_utc: int) -> dict[str, Any] | None:
    """What the Sea page draws of an answer of ``/v1/surf/spots/{spot}``: the spot, and its hours from the one under way to
    ``BEACH_HOURS`` ahead (waves, swell, wind and how it blows to the shore, tide), without the ratings of the surf page."""
    if not isinstance(answer, dict) or not isinstance(answer.get("hours"), list):
        return None
    return {
        **_pick(answer, _FORECAST_KEEP),
        "spot": _pick(answer.get("spot"), _SPOT_KEEP),
        "hours": [
            _pick(hour, _SEA_HOUR_KEEP)
            for hour in answer["hours"]
            if isinstance(hour, dict) and _in_window(hour.get("time_utc"), now_utc)
        ],
    }


def beach_activities(answer: Any, now_utc: int) -> dict[str, Any] | None:
    """What the Sea page draws of an answer of ``/v1/activities/{spot}``: each verdict and its hours of the window, the high and
    low waters of the next day and a half with the estimated coefficients, the sun, the coastal warnings."""
    if not isinstance(answer, dict) or not isinstance(answer.get("activities"), list):
        return None
    tide = answer.get("tide") if isinstance(answer.get("tide"), dict) else {}
    extremes = tide.get("extremes") if isinstance(tide.get("extremes"), list) else []
    coefficients = tide.get("coefficients_estimated")
    return {
        **_pick(answer, _ACTIVITIES_KEEP),
        "activities": [
            {
                **_pick(activity, _ACTIVITY_KEEP),
                "hours": [
                    _pick(hour, _ACTIVITY_HOUR_KEEP)
                    for hour in activity.get("hours") or []
                    if isinstance(hour, dict) and _in_window(hour.get("time_utc"), now_utc)
                ],
            }
            for activity in answer["activities"]
            if isinstance(activity, dict)
        ],
        "tide": {
            "extremes": [
                extreme
                for extreme in extremes
                if isinstance(extreme, dict)
                and isinstance(extreme.get("time_utc"), int | float)
                and now_utc - 12 * 3600 <= extreme["time_utc"] <= now_utc + 36 * 3600
            ],
            "coefficients_estimated": coefficients if isinstance(coefficients, list) else None,
        },
    }


def beach_water(answer: Any) -> dict[str, Any] | None:
    """The sea of an answer of ``/v1/point`` asked at the beach: the temperature of the water and the state of the sea of the
    ocean model at its cell (not a measure at the beach); ``None`` without the sea."""
    marine = answer.get("marine") if isinstance(answer, dict) else None
    return (_pick(marine, _WATER_KEEP) or None) if isinstance(marine, dict) else None


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from the first point to the second, in degrees from north."""
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def bathing_site(answer: Any, spot: dict[str, Any]) -> dict[str, Any] | None:
    """The monitored bathing site of a beach in an answer of ``/v1/sea/places``: the nearest within ``BATHING_NEAR_KM`` of the
    spot, leaving out one behind the beach when the spot's facing is known (the list does not say whether its water is the sea
    or a lake), with the class of its water and the credit; ``None`` when there is none."""
    places = answer.get("places") if isinstance(answer, dict) else None
    lat, lon, facing = _number(spot.get("lat")), _number(spot.get("lon")), _number(spot.get("facing_deg"))
    if not isinstance(places, list) or lat is None or lon is None:
        return None
    best: tuple[float, dict[str, Any]] | None = None
    for place in places:
        if not isinstance(place, dict) or place.get("type") != "bathing":
            continue
        p_lat, p_lon = _number(place.get("lat")), _number(place.get("lon"))
        if p_lat is None or p_lon is None:
            continue
        km = distance_km(lat, lon, p_lat, p_lon)
        if km > BATHING_NEAR_KM or (best is not None and km >= best[0]):
            continue
        if facing is not None and km > _SAME_PLACE_KM:
            off = abs(((bearing_deg(lat, lon, p_lat, p_lon) - facing + 540) % 360) - 180)
            if off > _INLAND_DEG:
                continue
        best = (km, place)
    if best is None:
        return None
    return {**_pick(best[1], _BATHING_KEEP), "distance_km": round(best[0], 1), "attribution": answer.get("attribution")}


class _Beach:
    """A beach of the Sea page: its forecast and its water, kept as long as its verdicts, and its bathing site, kept a day."""

    def __init__(self) -> None:
        self.forecast = _Slow(BEACH_REFRESH)
        self.water = _Slow(BEACH_REFRESH)
        self.bathing = _Slow(BATHING_REFRESH)
        self.lock = asyncio.Lock()  # two screens asking at once: one read


class _Station:
    """A station of the Mountain page: its top, its base and its avalanche bulletin, each kept for ``MOUNTAIN_REFRESH``."""

    def __init__(self) -> None:
        self.top = _Slow(MOUNTAIN_REFRESH)
        self.base = _Slow(MOUNTAIN_REFRESH)
        self.avalanches = _Slow(MOUNTAIN_REFRESH)
        self.lock = asyncio.Lock()  # two screens asking at once: one read


class VigieWeatherCoordinator(DataUpdateCoordinator[WeatherData]):
    """Reads the weather of the place of the entry, and what the card shows, every 10 minutes."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: VigieClient) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} {entry.title} weather", update_interval=WEATHER_INTERVAL)
        self.client = client
        self.latitude: float = entry.data[CONF_LATITUDE]
        self.longitude: float = entry.data[CONF_LONGITUDE]
        self._tides = _Slow(TIDES_REFRESH)
        self._normals = _Slow(NORMALS_REFRESH)
        self._spots = _Slow(SPOTS_REFRESH)
        self._forest_fire = _Slow(FOREST_FIRE_REFRESH)
        self._activities: dict[str, _Slow] = {}
        self._stations: OrderedDict[tuple[float, float, int | None, int], _Station] = OrderedDict()
        self._beaches: OrderedDict[str, _Beach] = OrderedDict()
        self._overview = _Slow(MAP_REFRESH)
        self._surf_overview = _Slow(MAP_REFRESH)
        self._places = _Slow(PLACES_REFRESH)
        self._map_lock = asyncio.Lock()  # two screens asking at once: one read
        self._places_lock = asyncio.Lock()

    async def _async_update_data(self) -> WeatherData:
        try:
            point = await self.client.point(self.latitude, self.longitude)
        except VigieError as err:
            raise UpdateFailed(str(err)) from err
        if not isinstance(point, dict) or not isinstance(point.get("daily"), list):
            raise UpdateFailed("Unreadable answer: not an answer of /v1/point")
        lat, lon = self.latitude, self.longitude
        tides = await self._tides.get(lambda: self.client.tides(lat, lon))
        normals = await self._normals.get(lambda: self.client.climate_normals(lat, lon))
        station = None
        forest_fire = None
        if isinstance(normals, dict) and normals.get("status") == "available":
            # The forest fire danger: metropolitan France only (overseas the instance answers `outside_coverage`).
            forest_fire = await self._forest_fire.get(lambda: self.client.forest_fire(lat, lon))
            # Météo-France stations: only where its climate sheets are (France and its overseas territories).
            try:
                answer = await self.client.station_wind(lon - STATION_BOX_DEG, lat - STATION_BOX_DEG, lon + STATION_BOX_DEG, lat + STATION_BOX_DEG)
            except VigieError as err:
                _LOGGER.debug("No measured wind this time: %s", err)
            else:
                station = nearest_station(answer, lat, lon)
        return WeatherData(
            point=point,
            tides=tides if isinstance(tides, dict) else None,
            normals=normals if isinstance(normals, dict) else None,
            station_wind=station,
            read_utc=int(time.time()),
            forest_fire=forest_fire if isinstance(forest_fire, dict) else None,
        )

    async def async_spots(self) -> list[dict[str, Any]]:
        """The spots near the place, nearest first (read once a day)."""
        answer = await self._spots.get(self.client.surf_spots)
        return nearby_spots(answer, self.latitude, self.longitude)

    async def async_spot(self, spot_id: str) -> dict[str, Any] | None:
        """A spot of the instance, near the place or not, with its distance; ``None`` for an id the instance does not have."""
        return spot_of(await self._spots.get(self.client.surf_spots), spot_id, self.latitude, self.longitude)

    async def async_activities(self, spot_id: str) -> dict[str, Any] | None:
        """The verdicts of a spot of the instance (read every 30 minutes at most); ``None`` for an id it does not have."""
        if await self.async_spot(spot_id) is None:
            return None
        slow = self._activities.setdefault(spot_id, _Slow(ACTIVITIES_REFRESH))
        answer = await slow.get(lambda: self.client.activities(spot_id))
        return answer if isinstance(answer, dict) else None

    async def async_beach(self, spot_id: str) -> dict[str, Any] | None:
        """A beach of the Sea page: a spot of the instance, near the place or kept with a star far from it (``None`` for an id
        it does not have), its forecast hour by hour, its verdicts, the water at its cell and its monitored bathing site, trimmed
        to what the page draws. A part that could not be read is ``None``."""
        near = await self.async_spot(spot_id)
        if near is None:
            return None
        beach = self._beaches.pop(spot_id, None) or _Beach()
        self._beaches[spot_id] = beach
        while len(self._beaches) > MAX_BEACHES:
            self._beaches.popitem(last=False)
        async with beach.lock:
            forecast = await beach.forecast.get(lambda: self.client.surf_forecast(spot_id))
            spot = forecast.get("spot") if isinstance(forecast, dict) and isinstance(forecast.get("spot"), dict) else {}
            lat, lon = _number(spot.get("lat")), _number(spot.get("lon"))
            if lat is not None and lon is not None:
                water, bathing, activities = await asyncio.gather(
                    beach.water.get(lambda: self.client.point(lat, lon)),
                    beach.bathing.get(lambda: self.client.bathing_sites(lat, lon, BATHING_ASK_KM)),
                    self.async_activities(spot_id),
                )
            else:
                water, bathing, activities = None, None, await self.async_activities(spot_id)
        now = int(time.time())
        trimmed = beach_forecast(forecast, now)
        return {
            "spot": {**near, **(trimmed["spot"] if trimmed else {})},
            "forecast": trimmed,
            "activities": beach_activities(activities, now),
            "water": beach_water(water),
            "bathing": bathing_site(bathing, spot),
        }

    async def async_map(self) -> dict[str, Any]:
        """Every spot of the Map page with its verdicts and the figures of its hour (read every ``MAP_REFRESH`` at most). An
        instance older than the overviews gives the spots without them; one that gives no spot, an empty map."""
        async with self._map_lock:
            spots, overview, surf = await asyncio.gather(
                self._spots.get(self.client.surf_spots),
                self._overview.get(self.client.activities_overview),
                self._surf_overview.get(self.client.surf_overview),
            )
        return map_spots(spots, overview, surf)

    async def _read_places(self) -> dict[str, Any]:
        first = await self.client.sea_places(0, PLACES_PAGE)
        if not isinstance(first, dict) or not isinstance(first.get("places"), list):
            raise VigieError("Unreadable answer: not an answer of /v1/sea/places")
        count = first["count"] if isinstance(first.get("count"), int) else len(first["places"])
        places = list(first["places"])
        # The pages in turn, never past MAX_PLACES nor past a page that brings nothing (an instance that ignores `offset`).
        while len(places) < min(count, MAX_PLACES):
            more = _list_of(await self.client.sea_places(len(places), PLACES_PAGE), "places")
            if not more:
                break
            places.extend(more)
        seen: set[str] = set()
        kept = []
        for place in places[:MAX_PLACES]:
            item = map_place(place)
            if item is not None and item[0] not in seen:
                seen.add(item[0])
                kept.append(item)
        return {**{key: first.get(key) for key in _PLACES_KEEP}, "fields": list(PLACE_FIELDS), "places": kept}

    async def async_places(self) -> dict[str, Any] | None:
        """The places of the French coast for the Map page, read once a day by pages; ``None`` when the instance has none
        (older than ``/v1/sea/places``) or could not give them yet."""
        async with self._places_lock:
            answer = await self._places.get(self._read_places)
        return answer if isinstance(answer, dict) else None

    async def async_mountain(self, lat: float, lon: float, base_m: int | None, top_m: int) -> dict[str, Any]:
        """A station of the Mountain page: the weather at its top and at its base (``None``: at the altitude of the model's cell),
        and its avalanche bulletin, read every ``MOUNTAIN_REFRESH`` at most. A part that could not be read is ``None``."""
        key = (round(lat, 4), round(lon, 4), base_m, top_m)
        station = self._stations.pop(key, None) or _Station()
        self._stations[key] = station
        while len(self._stations) > MAX_STATIONS:
            self._stations.popitem(last=False)
        async with station.lock:
            top, base, avalanches = await asyncio.gather(
                station.top.get(lambda: self.client.point(lat, lon, elevation_m=top_m, marine=False)),
                station.base.get(lambda: self.client.point(lat, lon, elevation_m=base_m, marine=False)),
                station.avalanches.get(lambda: self.client.avalanches(lat, lon)),
            )
        top_point, base_point = mountain_point(top, top_m), mountain_point(base, base_m)
        return {
            "top": top_point,
            "base": base_point,
            "avalanches": mountain_avalanches(avalanches),
            # The instance answered, but without the altitude asked: older than `GET /v1/point?elevation=`.
            "elevation_ignored": (top_point is None and isinstance(top, dict) and isinstance(top.get("daily"), list))
            or (base_point is None and base_m is not None and isinstance(base, dict) and isinstance(base.get("daily"), list)),
        }


def utc_datetime(timestamp: int) -> datetime:
    """A UTC epoch second as an aware datetime."""
    return datetime.fromtimestamp(timestamp, tz=UTC)
