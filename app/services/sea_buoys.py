"""The offshore buoys of Météo-France and their latest record (``GET /v1/sea/buoys``).

The surf spots keep their nearest coastal buoy (``app/services/buoys.py``, CANDHIS and NOAA NDBC): the buoys of Météo-France float
30 to 100 km off the coast, and none of them is nearer to a surf spot than a real-time CANDHIS buoy (counted on 2026-11-16). They
say what the open sea does (the Gulf of Lion, the Ligurian Sea, around Corsica, the Bay of Biscay), so they are listed on their own.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.attribution import credit_text
from app.collectors.base import CollectorUnavailableError
from app.collectors.meteofrance_buoys import BuoyRecord, MeteoFranceBuoysCollector
from app.models.sea_buoys import SeaBuoy, SeaBuoys
from app.services.geodesy import haversine_km

# A record older than this is not "the sea now"; one dated further ahead of our clock is not believed. Settings of ours: the
# buoys give one record an hour, so three hours is two missed transmissions.
MAX_AGE_S = 3 * 3600
MAX_AHEAD_S = 30 * 60
LICENCE = "Licence Ouverte 2.0 (not stated on the API's own page: see SOURCES.md)"
NOTE = (
    "The moored buoys of Météo-France, out at sea (30 to 100 km off the coast): what the open sea does, not what a beach gets. Only "
    "buoys with a record of the last `max_age_hours` hours are listed. The distance and `age_minutes` are calculated by Vigie; the "
    "values are Météo-France's, converted to °C, km/h and hPa. **Not for safety.**"
)


def _buoy(record: BuoyRecord, now_unix: float, near: Optional[tuple[float, float]]) -> SeaBuoy:
    return SeaBuoy(
        id=record.id,
        name=record.name,
        lat=record.lat,
        lon=record.lon,
        distance_km=None if near is None else round(haversine_km(near[0], near[1], record.lat, record.lon), 1),
        measured_utc=record.measured_utc,
        measured_at=datetime.fromtimestamp(record.measured_utc, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        age_minutes=max(0, int((now_unix - record.measured_utc) // 60)),
        wave_height_m=record.wave_height_m,
        wave_period_s=record.wave_period_s,
        wave_from_deg=record.wave_from_deg,
        sea_temp_c=record.sea_temp_c,
        air_temp_c=record.air_temp_c,
        wind_speed_kmh=record.wind_speed_kmh,
        wind_gust_kmh=record.wind_gust_kmh,
        wind_from_deg=record.wind_from_deg,
        pressure_hpa=record.pressure_hpa,
    )


async def sea_buoys(
    collector: Optional[MeteoFranceBuoysCollector],
    now_unix: float,
    near: Optional[tuple[float, float]] = None,
    radius_km: Optional[float] = None,
    limit: int = 50,
) -> SeaBuoys:
    """The current offshore buoys, nearest first around ``near`` (within ``radius_km`` when given). Never raises for the source."""

    def answer(status: str, buoys: list[SeaBuoy], stale: bool = False) -> SeaBuoys:
        return SeaBuoys(
            status=status,  # type: ignore[arg-type]
            count=len(buoys),
            buoys=buoys,
            stale=stale,
            max_age_hours=MAX_AGE_S / 3600,
            note=NOTE,
            derived=["distance_km", "age_minutes", "measured_at", "max_age_hours"],
            licence=LICENCE,
            attribution=credit_text("meteofrance_buoys"),
        )

    if collector is None:
        return answer("not_configured", [])
    try:
        latest = await collector.fetch_latest()
    except CollectorUnavailableError:
        return answer("unavailable", [])
    rows = [
        _buoy(r, now_unix, near) for r in latest.records.values() if -MAX_AHEAD_S <= now_unix - r.measured_utc <= MAX_AGE_S
    ]
    if near is not None:
        rows = [b for b in rows if radius_km is None or (b.distance_km or 0.0) <= radius_km]
        rows.sort(key=lambda b: (b.distance_km or 0.0, b.id))
    else:
        rows.sort(key=lambda b: (-b.lat, b.id))
    return answer("ok", rows[:limit], latest.stale)
