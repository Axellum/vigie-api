"""The forecast of a hazard as GeoJSON: what ``GET /v1/geojson/hazards/{id}/forecast`` answers (``app/models/forecast.py``).

Two sources have a forecast to draw: the tropical cyclones of the National Hurricane Center (cone, track, forecast positions, coastal
watches and warnings) and the tsunami messages of the U.S. warning centres (epicentre, estimated arrival times). The geometries were
made when the source's bundle was read (``app/storm_geo.py``); here they are only put in features.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from app.attribution import credit_text
from app.collectors.nhc import KNOT_KMH, Storm
from app.collectors.tsunami import CENTRES, THREAT_PRODUCTS, TsunamiEvent
from app.models.forecast import ForecastFeature, HazardForecast
from app.models.schemas import HazardAlert

NO_FORECAST_REASON = (
    "This hazard has no forecast: the API relays one for the tropical cyclones of the National Hurricane Center "
    "(cone, track, forecast positions) and the arrival times of the tsunami messages of the U.S. warning centres."
)


def empty(hazard: HazardAlert, reason: str = NO_FORECAST_REASON) -> HazardForecast:
    """An empty forecast with its reason: the hazard exists, the source gave nothing to draw."""
    return HazardForecast(hazard_id=hazard.id, title=hazard.title, reason=reason, generated_utc=int(time.time()))


def _bbox(features: list[ForecastFeature]) -> Optional[list[float]]:
    lons: list[float] = []
    lats: list[float] = []

    def walk(value: Any) -> None:
        if isinstance(value, (list, tuple)):
            if len(value) >= 2 and isinstance(value[0], (int, float)):
                lons.append(float(value[0]))
                lats.append(float(value[1]))
            else:
                for item in value:
                    walk(item)

    for feature in features:
        if feature.geometry is not None:
            walk(feature.geometry.get("coordinates"))
    return [min(lons), min(lats), max(lons), max(lats)] if lons else None


def storm_forecast(hazard: HazardAlert, storm: Storm) -> HazardForecast:
    """The cone, the track, the forecast positions and the watches and warnings of a storm's latest advisory."""
    forecast = storm.forecast
    if forecast is None:
        return empty(
            hazard,
            "The forecast of this storm could not be read from the National Hurricane Center this time "
            "(its advisory bundle was not available or not readable): the current position is the hazard itself.",
        )
    common = {"storm_id": storm.id, "storm_name": storm.name, "advisory": forecast.advisory}
    features: list[ForecastFeature] = []
    for index, cone in enumerate(forecast.cones):
        features.append(ForecastFeature(id=f"{hazard.id}:cone{'' if index == 0 else f':{index}'}", geometry=cone, properties={"kind": "cone", **common}))
    for index, track in enumerate(forecast.tracks):
        features.append(ForecastFeature(id=f"{hazard.id}:track{'' if index == 0 else f':{index}'}", geometry=track, properties={"kind": "track", **common}))
    for point in forecast.points:
        features.append(
            ForecastFeature(
                id=f"{hazard.id}:pos:{point.tau_h if point.tau_h is not None else len(features)}",
                geometry=point.geometry,
                properties={
                    "kind": "position",
                    "tau_h": point.tau_h,
                    "valid_utc": point.valid_utc,
                    "wind_kt": point.wind_kt,
                    "wind_kmh": None if point.wind_kt is None else round(point.wind_kt * KNOT_KMH, 1),
                    "gust_kt": point.gust_kt,
                    "pressure_hpa": point.pressure_hpa,
                    "stage": point.stage,
                    "category": point.category,
                    **common,
                },
            )
        )
    for index, warning in enumerate(forecast.watches_warnings):
        features.append(
            ForecastFeature(
                id=f"{hazard.id}:ww:{index}",
                geometry=warning.geometry,
                properties={"kind": "watch_warning", "code": warning.code, "label": warning.label, **common},
            )
        )
    if not features:
        return empty(hazard, "The advisory bundle of this storm holds no geometry to draw.")
    return HazardForecast(
        hazard_id=hazard.id,
        title=hazard.title,
        kind="tropical_cyclone",
        source="NHC",
        issued_utc=forecast.issued_utc,
        advisory=forecast.advisory,
        advisory_url=storm.advisory_url,
        attribution=credit_text("noaa_nhc"),
        generated_utc=int(time.time()),
        bbox=_bbox(features),
        features=features,
    )


def tsunami_forecast(hazard: HazardAlert, event: TsunamiEvent) -> HazardForecast:
    """The epicentre and the estimated arrival times of a tsunami event, from its latest messages."""
    strongest = event.strongest
    origin = event.origin
    alerts = [
        {
            "event": alert.info.event,
            "level_original": alert.level_original,
            "level": alert.level.value,
            "areas": "; ".join(area.description for area in alert.info.areas if area.description) or None,
            "message": alert.message.identifier,
            "product": alert.message.bulletin.wmo_id,
            "international_threat_message": alert.message.bulletin.wmo_id in THREAT_PRODUCTS,
        }
        for alert in event.alerts
    ]
    features = [
        ForecastFeature(
            id=f"{hazard.id}:epicentre",
            geometry={"type": "Point", "coordinates": [round(origin.lon, 3), round(origin.lat, 3)]},
            properties={
                "kind": "epicentre",
                "magnitude": origin.magnitude,
                "magnitude_type": origin.magnitude_type,
                "depth_km": origin.depth_km,
                "origin_utc": origin.time_utc,
                "location": origin.location,
                "alerts": alerts,
            },
        )
    ]
    for index, eta in enumerate(sorted(event.etas, key=lambda e: e.eta_utc)):
        features.append(
            ForecastFeature(
                id=f"{hazard.id}:eta:{index}",
                geometry=None if eta.lat is None or eta.lon is None else {"type": "Point", "coordinates": [round(eta.lon, 3), round(eta.lat, 3)]},
                properties={"kind": "eta", "place": eta.place, "region": eta.region, "eta_utc": eta.eta_utc, "read_from": eta.source},
            )
        )
    return HazardForecast(
        hazard_id=hazard.id,
        title=hazard.title,
        kind="tsunami",
        source=CENTRES[event.twc],
        issued_utc=strongest.message.sent_utc,
        advisory=strongest.message.identifier,
        advisory_url=strongest.info.web,
        attribution=credit_text("noaa_tsunami"),
        generated_utc=int(time.time()),
        bbox=_bbox(features),
        features=features,
    )


def has_storm_forecast(storm: Storm) -> bool:
    """A storm has a forecast to fetch when its bundle was read and holds a cone, a track or a position."""
    forecast = storm.forecast
    return forecast is not None and bool(forecast.cones or forecast.tracks or forecast.points)


def has_tsunami_forecast(event: TsunamiEvent) -> bool:
    """A tsunami event has a forecast when a message gave the arrival time at a place."""
    return bool(event.etas)
