"""The hourly swell and wind of a surf spot: Météo-France's wave model through Open-Meteo Marine, and Open-Meteo's wind.

Two requests per spot, each refreshed at the pace of what it carries (``docs/data-sources.md``, "Surf"):

- ``swell``: the waves, the primary and secondary swell and the wind waves of **MFWAM** (Météo-France, Copernicus Marine;
  ``models=meteofrance_wave``), every hour for 7 days (10 variables, weight 1.0). MFWAM is the model of Open-Meteo Marine that
  gives a secondary swell on these coasts; its data is 3-hourly and updated every 12 hours
  (https://open-meteo.com/en/docs/marine-weather-api), so the entry is kept 3 hours.
- ``wind``: the wind at 10 m, its direction and gusts, every hour, and the sunrise and sunset of each day (5 variables, weight
  1.0), from Open-Meteo's forecast; the models are updated every hour at best, so the entry is kept 1 hour.

A spot is a fixed point of ``app/data/surf-spots.json``: the cache key is the spot, never the number of days asked (7 are
always fetched, the service cuts them). A variable the provider leaves out stays ``None``; an answer with no swell at all
raises (the spot then has no forecast).

**Every spot at once** (``GET /v1/surf/overview``): one request names every spot (Open-Meteo answers a list, one item per
location, and weighs it one call per location), the swell kept 3 hours and the wind 3 hours too: the overview is a summary, the
hourly wind of a spot is on its own page. While these entries are fresh, a spot's own forecast is read from them (the swell, and
the wind while it is under an hour old) instead of being asked again.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Optional

import httpx

from app import clock
from app.collectors.base import OPEN_METEO_CONNECTION, BaseCollector, optional_float
from app.collectors.grid import coord_str
from app.collectors.marine import DEFAULT_OPEN_METEO_MARINE_URL, open_meteo_marine_url
from app.collectors.open_meteo_hosts import get_open_meteo
from app.collectors.weather import DEFAULT_OPEN_METEO_FORECAST_URL, open_meteo_forecast_url
from app.collectors.weights import open_meteo_weight

SWELL_VARIABLES = (
    "wave_height",
    "wave_direction",
    "wave_period",
    "swell_wave_height",
    "swell_wave_direction",
    "swell_wave_period",
    "secondary_swell_wave_height",
    "secondary_swell_wave_direction",
    "secondary_swell_wave_period",
    "wind_wave_height",
)
WIND_VARIABLES = ("wind_speed_10m", "wind_direction_10m", "wind_gusts_10m")
DAYLIGHT_VARIABLES = ("sunrise", "sunset")
MARINE_MODEL = "meteofrance_wave"
FORECAST_DAYS = 7


def _series(block: Mapping[str, Any], name: str, length: int) -> list[Optional[float]]:
    """The values of one variable, as numbers or ``None``, padded with ``None`` to ``length``."""
    values = [optional_float(v) for v in (block.get(name) or [])][:length]
    return values + [None] * (length - len(values))


def _times(block: Mapping[str, Any]) -> list[int]:
    times = block.get("time") or []
    if not isinstance(times, list) or not all(isinstance(t, (int, float)) for t in times):
        raise ValueError("Open-Meteo answer without unix times")
    return [int(t) for t in times]


def parse_swell(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The hourly swell of an Open-Meteo Marine answer (``timeformat=unixtime``). Raises when it holds no swell height."""
    hourly = payload.get("hourly") or {}
    times = _times(hourly)
    series = {name: _series(hourly, name, len(times)) for name in SWELL_VARIABLES}
    if not any(h is not None for h in series["swell_wave_height"]) and not any(h is not None for h in series["wave_height"]):
        raise ValueError("Open-Meteo Marine answer has no wave or swell height")
    return {
        "times": times,
        "model_lat": optional_float(payload.get("latitude")),
        "model_lon": optional_float(payload.get("longitude")),
        **series,
    }


def parse_wind(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The hourly wind and the daylight of an Open-Meteo forecast answer (``timeformat=unixtime``)."""
    hourly = payload.get("hourly") or {}
    daily = payload.get("daily") or {}
    times = _times(hourly)
    days = len(daily.get("time") or [])
    series = {name: _series(hourly, name, len(times)) for name in WIND_VARIABLES}
    if not any(v is not None for v in series["wind_speed_10m"]):
        raise ValueError("Open-Meteo answer has no wind")
    return {
        "times": times,
        "model_lat": optional_float(payload.get("latitude")),
        "model_lon": optional_float(payload.get("longitude")),
        **series,
        "sunrise": [int(v) if v is not None else None for v in _series(daily, "sunrise", days)],
        "sunset": [int(v) if v is not None else None for v in _series(daily, "sunset", days)],
    }


def _items(payload: Any, count: int) -> list[Optional[Mapping[str, Any]]]:
    """The items of a multi-location answer, in the order of the locations asked (``location_id``, absent on the first)."""
    items: list[Optional[Mapping[str, Any]]] = [None] * count
    if not isinstance(payload, list):
        payload = [payload]
    numbered = any(isinstance(item, Mapping) and "location_id" in item for item in payload)
    for position, item in enumerate(payload):
        if not isinstance(item, Mapping):
            continue
        index = item.get("location_id", 0) if numbered else position
        if isinstance(index, int) and 0 <= index < count:
            items[index] = item
    return items


Point = tuple[str, float, float]


def _model_cell(parsed: Mapping[str, Any]) -> Optional[tuple[float, float]]:
    """Grid cell coordinate returned by Open-Meteo, rounded to 5 decimals for deterministic matching."""
    lat, lon = parsed.get("model_lat"), parsed.get("model_lon")
    if lat is None or lon is None:
        return None
    return (round(float(lat), 5), round(float(lon), 5))


class SurfForecastCollector(BaseCollector[dict[str, Any]]):
    """The swell (MFWAM) and the wind of the surf spots, from Open-Meteo's free APIs (one quota with the other Open-Meteo ones)."""

    MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
    FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
    SWELL_TTL_SECONDS = 3 * 3600
    WIND_TTL_SECONDS = 3600
    OVERVIEW_WIND_TTL_SECONDS = 3 * 3600

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        # Told of every answer read from the provider (never of a cache hit): ``(kind, {spot id: parsed answer})``, set by the archive.
        self.on_fetched: Optional[Callable[[str, dict[str, dict[str, Any]]], None]] = None
        self.MARINE_URL = open_meteo_marine_url()
        self.FORECAST_URL = open_meteo_forecast_url()
        # Learned Open-Meteo model grid cell per spot id, populated only from real upstream responses.
        self._cell_of: dict[str, dict[str, tuple[float, float]]] = {"swell": {}, "wind": {}}
        super().__init__(
            provider_id="open_meteo_surf",
            name="Open-Meteo surf (MFWAM swell, wind)",
            domain="marine",
            ttl_seconds=self.WIND_TTL_SECONDS,
            http_client=http_client,
            connection=OPEN_METEO_CONNECTION,
        )

    def call_weight(self, params: Mapping[str, str]) -> float:
        """Weight under the Open-Meteo rule: 10 or 5 variables over 7 days, one model, weigh 1.0."""
        return open_meteo_weight(params, default_forecast_days=FORECAST_DAYS)

    def _from_all(self, kind: str, spot_id: str, max_age_s: float) -> Optional[dict[str, Any]]:
        """The spot's part of the every-spot entry of ``kind``, when that entry is under ``max_age_s`` old."""
        entry = self._cache.get(f"surf-{kind}:all")
        if entry is None or clock.monotonic() - entry.stored_at > max_age_s:
            return None
        return entry.value.get(spot_id)

    def _from_peer_cell(self, kind: str, spot_id: str, max_age_s: float) -> Optional[dict[str, Any]]:
        """Reuse a fresh single-spot cache entry from another spot learned to share the same Open-Meteo grid cell."""
        cell = self._cell_of[kind].get(spot_id)
        if cell is None:
            return None
        now = clock.monotonic()
        prefix = f"surf-{kind}:"
        for peer_id, peer_cell in self._cell_of[kind].items():
            if peer_cell != cell or peer_id == spot_id:
                continue
            entry = self._cache.get(f"{prefix}{peer_id}")
            if entry is not None and now - entry.stored_at <= max_age_s and _model_cell(entry.value) == cell:
                return entry.value
        return None

    async def fetch_all(self, kind: str, points: tuple[Point, ...]) -> tuple[dict[str, dict[str, Any]], bool]:
        """The swell or the wind (``kind``) of every spot of ``points`` (id, lat, lon), by spot id, in one request; and whether
        it is a stale copy. Neighbouring spots already learned to share an Open-Meteo grid cell are collapsed to one coordinate
        per cell on refresh while preserving the full per-spot mapping."""
        ttl = self.SWELL_TTL_SECONDS if kind == "swell" else self.OVERVIEW_WIND_TTL_SECONDS
        fetched = await self.fetch_ex(cache_key=f"surf-{kind}:all", kind=kind, points=points, ttl_seconds=ttl)
        return fetched.value, fetched.stale

    async def fetch_swell(self, spot_id: str, lat: float, lon: float) -> tuple[dict[str, Any], bool]:
        """The hourly swell of a spot, and whether it is a stale copy."""
        shared = self._from_all("swell", spot_id, self.SWELL_TTL_SECONDS) or self._from_peer_cell(
            "swell", spot_id, self.SWELL_TTL_SECONDS
        )
        if shared is not None:
            return shared, False
        fetched = await self.fetch_ex(
            cache_key=f"surf-swell:{spot_id}", kind="swell", spot_id=spot_id, lat=lat, lon=lon, ttl_seconds=self.SWELL_TTL_SECONDS
        )
        return fetched.value, fetched.stale

    async def fetch_wind(self, spot_id: str, lat: float, lon: float) -> tuple[dict[str, Any], bool]:
        """The hourly wind and the daylight of a spot, and whether it is a stale copy."""
        shared = self._from_all("wind", spot_id, self.WIND_TTL_SECONDS) or self._from_peer_cell(
            "wind", spot_id, self.WIND_TTL_SECONDS
        )
        if shared is not None:
            return shared, False
        fetched = await self.fetch_ex(
            cache_key=f"surf-wind:{spot_id}", kind="wind", spot_id=spot_id, lat=lat, lon=lon, ttl_seconds=self.WIND_TTL_SECONDS
        )
        return fetched.value, fetched.stale

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> dict[str, Any]:
        kind = str(kwargs["kind"])
        swell = kind == "swell"
        points: Optional[tuple[Point, ...]] = kwargs.get("points")
        if points is None:
            req_points: tuple[Point, ...] = ()
            lats, lons = coord_str(float(kwargs["lat"])), coord_str(float(kwargs["lon"]))
        else:
            # Collapse spots with a known Open-Meteo model grid cell to one representative coordinate per unique cell.
            known = self._cell_of[kind]
            seen_cells: set[tuple[float, float]] = set()
            chosen: list[Point] = []
            for pt in points:
                cell = known.get(pt[0])
                if cell is None:
                    chosen.append(pt)
                elif cell not in seen_cells:
                    seen_cells.add(cell)
                    chosen.append(pt)
            req_points = tuple(chosen)
            lats = ",".join(coord_str(lat) for _, lat, _ in req_points)
            lons = ",".join(coord_str(lon) for _, _, lon in req_points)
        common = {
            "latitude": lats,
            "longitude": lons,
            "forecast_days": str(FORECAST_DAYS),
            "timeformat": "unixtime",
            "timezone": "GMT",
        }
        if swell:
            params = {**common, "hourly": ",".join(SWELL_VARIABLES), "models": MARINE_MODEL}
            response = await get_open_meteo(self, client, self.MARINE_URL, DEFAULT_OPEN_METEO_MARINE_URL, params)
        else:
            params = {**common, "hourly": ",".join(WIND_VARIABLES), "daily": ",".join(DAYLIGHT_VARIABLES)}
            response = await get_open_meteo(self, client, self.FORECAST_URL, DEFAULT_OPEN_METEO_FORECAST_URL, params)
        response.raise_for_status()
        parse = parse_swell if swell else parse_wind
        if points is None:
            parsed = parse(response.json())
            sid = kwargs.get("spot_id")
            if sid is not None:
                cell = _model_cell(parsed)
                if cell is not None:
                    self._cell_of[kind][str(sid)] = cell
            self._tell(kind, {str(sid): parsed} if sid is not None else {})
            return parsed

        rep_parsed: dict[str, dict[str, Any]] = {}
        cell_answers: dict[tuple[float, float], dict[str, Any]] = {}
        known = self._cell_of[kind]
        for (spot_id, _, _), item in zip(req_points, _items(response.json(), len(req_points)), strict=True):
            if item is None:
                continue
            try:
                parsed = parse(item)
            except ValueError:
                continue
            rep_parsed[spot_id] = parsed
            new_cell = _model_cell(parsed)
            prev_cell = known.get(spot_id)
            if prev_cell is not None and new_cell != prev_cell:
                # Provider grid shifted: forget peers on prev_cell so the next refresh probes them individually.
                for peer in [k for k, v in known.items() if v == prev_cell]:
                    del known[peer]
            if new_cell is not None:
                known[spot_id] = new_cell
                cell_answers[new_cell] = parsed

        out: dict[str, Any] = {}
        for spot_id, _, _ in points:
            if spot_id in rep_parsed:
                out[spot_id] = rep_parsed[spot_id]
            else:
                cell = known.get(spot_id)
                if cell is not None and cell in cell_answers:
                    out[spot_id] = cell_answers[cell]
        if not out:
            raise ValueError("Open-Meteo answer has no spot that could be read")
        self._tell(kind, out)
        return out

    def _tell(self, kind: str, answers: dict[str, dict[str, Any]]) -> None:
        if self.on_fetched is None or not answers:
            return
        try:
            self.on_fetched(kind, answers)
        except Exception:  # noqa: BLE001 - the archive is a side effect: it never fails a read
            pass
