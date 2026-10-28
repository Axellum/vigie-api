"""The aurora service: SWPC's products as SWPC gives them, the OVATION grid as map bands, and the estimate for a place (ADR 0013).

Nothing here sends a request of its own: the four SWPC collectors keep one copy each for the whole instance, and the clouds of a
place come from the daily forecast request the weather already sends (``OpenMeteoCollector.fetch_hourly_clouds``, MET Norway when
it fails). The work that would hold the event loop (drawing the bands, the Sun and the Moon of every hour) runs in a thread.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, TypeVar

from shapely.geometry import box, mapping
from shapely.ops import unary_union

from app import clock
from app.attribution import SPACE_WEATHER, credit_text, merge_credits
from app.collectors.aurora import (
    KpInterval,
    OvationGrid,
    SolarWind,
    SpaceWeatherMessages,
    SWPCAlertsCollector,
    SWPCKpForecastCollector,
    SWPCOvationCollector,
    SWPCSolarWindCollector,
)
from app.models.sky import (
    AuroraEstimate,
    AuroraHour,
    AuroraOvalCollection,
    AuroraOvalFeature,
    AuroraPlace,
    AuroraResponse,
    AuroraWindow,
    KpPeriod,
    OvalSummary,
    OvationNearby,
    SolarWindNow,
    SpaceWeatherMessageOut,
)
from app.services import aurora_method as method
from app.services.geodesy import haversine_km

T = TypeVar("T")

KP_INTERVAL_S = 3 * 3600
HOUR_S = 3600
LOOK_AHEAD_H = 36  # the coming night is looked for in the next 36 hours
MESSAGES_WINDOW_S = 72 * 3600
OVAL_LEVELS = (5, 10, 20, 40, 60)  # the bands of the map (ours); the values are SWPC's
OVAL_SIMPLIFY_DEG = 0.25
OVATION_REACH_KM = 1000.0  # SWPC: "can be observed from as much as a 1000 km away when the aurora is bright"
METHOD = "Vigie estimate (ADR 0013, docs/decisions/0013-aurora-estimate.md; measured in docs/measurements/aurora-2026-10.md)"
BEST_SHARE = 0.8


@dataclass(frozen=True)
class SkyInputs:
    """What the SWPC collectors had (``None`` for a product that could not be read)."""

    oval: Optional[OvationGrid]
    kp: Optional[list[KpInterval]]
    wind: Optional[SolarWind]
    messages: Optional[SpaceWeatherMessages]


async def _or_none(awaitable: Awaitable[T]) -> Optional[T]:
    try:
        return await awaitable
    except Exception:
        return None


def oval_bands(grid: OvationGrid) -> list[AuroraOvalFeature]:
    """The cells of the grid in each band ``[level, next level)``, joined into (multi)polygons, the antimeridian split."""
    features: list[AuroraOvalFeature] = []
    bounds = list(OVAL_LEVELS) + [256]
    for low, high in zip(bounds, bounds[1:], strict=False):
        boxes = []
        for lat, lon, value in grid.cells_at_least(low):
            if value >= high:
                continue
            south, north = max(-90.0, lat - 0.5), min(90.0, lat + 0.5)
            west, east = lon - 0.5, lon + 0.5
            if west < -180.0:  # the cell of lon -180 also covers 179.5 to 180
                boxes.append(box(-180.0, south, east, north))
                boxes.append(box(179.5, south, 180.0, north))
            else:
                boxes.append(box(west, south, min(180.0, east), north))
        if not boxes:
            continue
        shape = unary_union(boxes).simplify(OVAL_SIMPLIFY_DEG, preserve_topology=True)
        if shape.is_empty:
            continue
        geometry = mapping(shape)
        features.append(
            AuroraOvalFeature(
                geometry=_rounded(geometry),
                properties={"min": low, "max": None if high == 256 else high, "source": "NOAA SWPC OVATION"},
            )
        )
    return features


def _rounded(geometry: Any) -> Any:
    """The coordinates rounded to 0.01 degree (the grid is one degree)."""
    if isinstance(geometry, dict):
        return {key: _rounded(value) for key, value in geometry.items()}
    if isinstance(geometry, (list, tuple)):
        if geometry and all(isinstance(v, (int, float)) for v in geometry):
            return [round(float(v), 2) for v in geometry]
        return [_rounded(v) for v in geometry]
    return geometry


def ovation_nearby(grid: OvationGrid, lat: float, lon: float) -> OvationNearby:
    """The value of the place's cell and the strongest value within 1,000 km (SWPC's values)."""
    best: Optional[tuple[int, float, int, int]] = None
    for cell_lat, cell_lon, value in grid.cells_at_least(1):
        if abs(cell_lat - lat) > OVATION_REACH_KM / method.KM_PER_DEGREE + 1:
            continue
        distance = haversine_km(lat, lon, cell_lat, cell_lon)
        if distance > OVATION_REACH_KM:
            continue
        if best is None or value > best[0] or (value == best[0] and distance < best[1]):
            best = (value, distance, cell_lat, cell_lon)
    return OvationNearby(
        forecast_utc=grid.forecast_utc,
        value_here=grid.value_at(lat, lon),
        strongest_within_1000_km=None if best is None else best[0],
        strongest_distance_km=None if best is None else round(best[1], 0),
        strongest_lat=None if best is None else float(best[2]),
        strongest_lon=None if best is None else float(best[3]),
    )


def kp_for(intervals: list[KpInterval], when_utc: float) -> Optional[KpInterval]:
    """The interval that contains ``when_utc``."""
    for interval in intervals:
        if interval.start_utc <= when_utc < interval.start_utc + KP_INTERVAL_S:
            return interval
    return None


def _period(interval: KpInterval) -> KpPeriod:
    return KpPeriod(
        start_utc=interval.start_utc,
        end_utc=interval.start_utc + KP_INTERVAL_S,
        kp=interval.kp,
        kind=interval.kind,  # type: ignore[arg-type]
        noaa_scale=interval.noaa_scale,
    )


def _cloud_at(clouds: dict[int, Optional[float]], when_utc: int) -> Optional[float]:
    """The cloud cover of the forecast hour nearest to ``when_utc`` (within 30 minutes); ``None`` otherwise."""
    for delta in (0, -1800, 1800):
        if when_utc + delta in clouds:
            return clouds[when_utc + delta]
    nearest = min(clouds, key=lambda t: abs(t - when_utc), default=None)
    if nearest is not None and abs(nearest - when_utc) <= 1800:
        return clouds[nearest]
    return None


def estimate_night(
    lat: float,
    lon: float,
    now_utc: int,
    intervals: Optional[list[KpInterval]],
    clouds: Optional[list[tuple[int, Optional[float]]]],
) -> tuple[Optional[AuroraEstimate], Optional[str]]:
    """The estimate of the coming night at the place, hour by hour; ``(None, reason)`` when there is none to make."""
    if not intervals:
        return None, "SWPC's Kp could not be read"
    magnetic_lat = method.magnetic_latitude(lat, lon)
    cloud_table = {t: c for t, c in (clouds or [])}
    first_hour = now_utc - now_utc % HOUR_S
    night: list[AuroraHour] = []
    for step in range(LOOK_AHEAD_H + 1):
        hour = first_hour + step * HOUR_S
        middle = hour + HOUR_S / 2
        sun, moon_alt, fraction = method.sun_and_moon(lat, lon, middle)
        dark = method.darkness(sun)
        if dark <= 0.0:
            if night:
                break  # the night is over
            continue
        interval = kp_for(intervals, middle)
        cover = _cloud_at(cloud_table, hour) if cloud_table else None
        moon = method.moon_factor(moon_alt, fraction)
        clouds_factor = method.cloud_factor(cover)
        if interval is None:
            activity = distance = clear = chance = None
        else:
            distance = method.distance_to_edge_km(magnetic_lat, interval.kp)
            activity = method.activity(magnetic_lat, interval.kp)
            clear = activity * dark * moon
            chance = None if clouds_factor is None else clear * clouds_factor
        night.append(
            AuroraHour(
                time_utc=hour,
                kp=None if interval is None else interval.kp,
                kp_kind=None if interval is None else interval.kind,  # type: ignore[arg-type]
                distance_to_edge_km=None if distance is None else round(distance, 0),
                activity=None if activity is None else round(activity, 3),
                sun_altitude_deg=round(sun, 1),
                darkness=round(dark, 3),
                moon_altitude_deg=round(moon_alt, 1),
                moon_illuminated=round(fraction, 3),
                moon=round(moon, 3),
                cloud_cover_pct=cover,
                clouds=None if clouds_factor is None else round(clouds_factor, 3),
                chance_clear_sky=None if clear is None else round(clear, 3),
                chance=None if chance is None else round(chance, 3),
                level=None if chance is None else method.level_of(chance),  # type: ignore[arg-type]
                level_clear_sky=None if clear is None else method.level_of(clear),  # type: ignore[arg-type]
            )
        )
    if not night:
        return None, f"no hour of the next {LOOK_AHEAD_H} hours has the Sun 6 degrees below the horizon or lower"
    clouds_known = all(h.chance is not None for h in night if h.chance_clear_sky is not None)
    best = best_window(night, clouds_known)
    best_hour_level = best.level if best is not None else ("very_unlikely" if any(h.chance_clear_sky is not None for h in night) else None)
    return (
        AuroraEstimate(
            night_start_utc=night[0].time_utc,
            night_end_utc=night[-1].time_utc + HOUR_S,
            hours=night,
            best=best,
            level=best_hour_level,  # type: ignore[arg-type]
            clouds_known=clouds_known,
            method=METHOD,
        ),
        None,
    )


def best_window(hours: list[AuroraHour], with_clouds: bool) -> Optional[AuroraWindow]:
    """The run of hours around the best one whose chance is at least ``BEST_SHARE`` of it; ``None`` below 5 %."""
    values = [(h.chance if with_clouds else h.chance_clear_sky) for h in hours]
    scored = [(i, v) for i, v in enumerate(values) if v is not None]
    if not scored:
        return None
    best_index, best_value = max(scored, key=lambda item: item[1])
    if best_value < 0.05:
        return None
    floor = BEST_SHARE * best_value
    start = end = best_index
    while start > 0 and values[start - 1] is not None and values[start - 1] >= floor:  # type: ignore[operator]
        start -= 1
    while end < len(values) - 1 and values[end + 1] is not None and values[end + 1] >= floor:  # type: ignore[operator]
        end += 1
    return AuroraWindow(
        start_utc=hours[start].time_utc,
        end_utc=hours[end].time_utc + HOUR_S,
        chance=round(best_value, 3),
        level=method.level_of(best_value),  # type: ignore[arg-type]
        with_clouds=with_clouds,
    )


class AuroraService:
    """Reads the four SWPC collectors and, for a place, the clouds; builds the answers of the aurora routes."""

    def __init__(
        self,
        ovation: SWPCOvationCollector,
        kp_forecast: SWPCKpForecastCollector,
        solar_wind: SWPCSolarWindCollector,
        alerts: SWPCAlertsCollector,
        clouds: Callable[[float, float], Awaitable[tuple[Optional[list[tuple[int, Optional[float]]]], Optional[int], Optional[str]]]],
    ) -> None:
        self.ovation = ovation
        self.kp_forecast = kp_forecast
        self.solar_wind = solar_wind
        self.alerts = alerts
        self.clouds = clouds
        self._bands: Optional[tuple[tuple[int, int], list[AuroraOvalFeature]]] = None

    async def inputs(self) -> SkyInputs:
        oval, kp, wind, messages = await asyncio.gather(
            _or_none(self.ovation.fetch()),
            _or_none(self.kp_forecast.fetch()),
            _or_none(self.solar_wind.fetch()),
            _or_none(self.alerts.fetch()),
        )
        return SkyInputs(oval, kp, wind, messages)

    async def oval_geojson(self) -> AuroraOvalCollection:
        """The OVATION grid as bands, drawn once per forecast."""
        grid: Optional[OvationGrid] = await _or_none(self.ovation.fetch())
        if grid is None:
            return AuroraOvalCollection()
        key = (grid.observation_utc, grid.forecast_utc)
        if self._bands is None or self._bands[0] != key:
            self._bands = (key, await clock.run_blocking(oval_bands, grid))
        return AuroraOvalCollection(
            features=self._bands[1],
            observation_utc=grid.observation_utc,
            forecast_utc=grid.forecast_utc,
            levels=list(OVAL_LEVELS),
            attribution=credit_text(*SPACE_WEATHER),
        )

    async def aurora(self, lat: Optional[float] = None, lon: Optional[float] = None) -> AuroraResponse:
        now = int(clock.wall())
        sky = await self.inputs()
        unavailable = [
            collector.provider_id
            for collector, value in (
                (self.ovation, sky.oval),
                (self.kp_forecast, sky.kp),
                (self.solar_wind, sky.wind),
                (self.alerts, sky.messages),
            )
            if value is None
        ]
        response = AuroraResponse(generated_utc=now, unavailable=unavailable)
        if sky.oval is not None:
            response.oval = OvalSummary(observation_utc=sky.oval.observation_utc, forecast_utc=sky.oval.forecast_utc, max_value=sky.oval.max_value)
        if sky.kp is not None:
            response.kp = [_period(i) for i in sky.kp if i.start_utc + KP_INTERVAL_S > now - 24 * 3600]
            current = kp_for(sky.kp, now)
            response.kp_now = None if current is None else _period(current)
            ahead = [i for i in sky.kp if i.start_utc + KP_INTERVAL_S > now and i.start_utc < now + 24 * 3600]
            response.kp_max_next_24h = _period(max(ahead, key=lambda i: i.kp)) if ahead else None
        if sky.wind is not None:
            w = sky.wind
            response.solar_wind = SolarWindNow(
                measured_utc=w.measured_utc,
                propagated_utc=w.propagated_utc,
                speed_km_s=w.speed_km_s,
                density_cm3=w.density_cm3,
                bt_nt=w.bt_nt,
                bz_nt=w.bz_nt,
            )
        if sky.messages is not None:
            response.messages = [
                SpaceWeatherMessageOut(
                    product_id=m.product_id, code=m.code, serial=m.serial, issued_utc=m.issued_utc, headline=m.headline, message=m.message
                )
                for m in sky.messages.messages
                if m.issued_utc >= now - MESSAGES_WINDOW_S and "geomagnetic" in (m.headline or "").lower()
            ]
        credits: list[Optional[str]] = [credit_text(*SPACE_WEATHER)] if len(unavailable) < 4 else []
        if lat is not None and lon is not None:
            response.place, weather_credit = await self._place(lat, lon, now, sky)
            credits.append(weather_credit)
        response.attribution = merge_credits(*credits)
        return response

    async def _place(self, lat: float, lon: float, now: int, sky: SkyInputs) -> tuple[AuroraPlace, Optional[str]]:
        clouds, offset, weather_credit = await self.clouds(lat, lon)
        estimate, reason = await clock.run_blocking(estimate_night, lat, lon, now, sky.kp, clouds)
        place = AuroraPlace(
            lat=lat,
            lon=lon,
            magnetic_lat=round(method.magnetic_latitude(lat, lon), 2),
            utc_offset_s=offset,
            ovation=None if sky.oval is None else await clock.run_blocking(ovation_nearby, sky.oval, lat, lon),
            estimate=estimate,
            no_estimate_reason=reason,
        )
        used_clouds = estimate is not None and any(h.cloud_cover_pct is not None for h in estimate.hours)
        return place, (weather_credit if used_clouds else None)

