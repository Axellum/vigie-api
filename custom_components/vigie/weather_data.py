"""The weather of the place and what the dashboard card shows (option "Weather and card", ADR 0042 of the API).

One coordinator reads ``/v1/point`` (the full answer, with the sea) every 10 minutes. At the same reads, and far less often, it
reads the tide (``/v1/tides``; 404 inland, then nothing is asked again for hours), the normals of the month
(``/v1/climate/normals``; France and its overseas territories only) and, where the normals exist, the wind measured by the
nearest Météo-France station (``/v1/stations/wind``). The spots and their verdicts are read when the card asks for them
(``/v1/surf/spots``, ``/v1/activities/{spot}``), and kept for a while.

Nothing here makes up a weather value: what the API did not give stays unknown. The few numbers worked out here (a sum of the
rain steps of the next hour, the strongest gust among the next hours, the next high water) are read from the answer as it is,
and their attributes say which part of it they come from.
"""

from __future__ import annotations

import logging
import math
import time
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
    DOMAIN,
    MAX_LISTED_SPOTS,
    MAX_SPOT_DISTANCE_KM,
    NORMALS_REFRESH,
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
    """The rain of the next hour (mm): the sum of the steps of `rain_nowcast.mm_per_step`, unknown when one of them is."""
    nowcast = point.get("rain_nowcast") if isinstance(point, dict) else None
    steps = nowcast.get("mm_per_step") if isinstance(nowcast, dict) else None
    if not isinstance(steps, list) or not steps:
        return None
    values = [_number(step) for step in steps]
    if any(value is None for value in values):
        return None
    return round(sum(values), 2)  # type: ignore[arg-type]


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


@dataclass(frozen=True, slots=True)
class WeatherData:
    """What one read of the weather of the place gave. ``None`` is "not there" (inland, outside France) or "not read yet"."""

    point: dict[str, Any]
    tides: dict[str, Any] | None
    normals: dict[str, Any] | None
    station_wind: dict[str, Any] | None
    read_utc: int


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
        self._activities: dict[str, _Slow] = {}

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
        if isinstance(normals, dict) and normals.get("status") == "available":
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
        )

    async def async_spots(self) -> list[dict[str, Any]]:
        """The spots near the place, nearest first (read once a day)."""
        answer = await self._spots.get(self.client.surf_spots)
        return nearby_spots(answer, self.latitude, self.longitude)

    async def async_activities(self, spot_id: str) -> dict[str, Any] | None:
        """The verdicts of a spot near the place (read every 30 minutes at most); ``None`` for a spot that is not near it."""
        if spot_id not in {spot["id"] for spot in await self.async_spots()}:
            return None
        slow = self._activities.setdefault(spot_id, _Slow(ACTIVITIES_REFRESH))
        answer = await slow.get(lambda: self.client.activities(spot_id))
        return answer if isinstance(answer, dict) else None


def utc_datetime(timestamp: int) -> datetime:
    """A UTC epoch second as an aware datetime."""
    return datetime.fromtimestamp(timestamp, tz=UTC)
