"""The answer of ``GET /v1/rain/observed`` from the NASA GIBS GPM IMERG collector (``app/collectors/nasa_gibs_imerg.py``)."""

from __future__ import annotations

from typing import Optional

from app.attribution import credit_text
from app.collectors.nasa_gibs_imerg import (
    API_TILE_PATH,
    BOUNDS,
    INTERVAL_MINUTES,
    LICENCE,
    MAX_ZOOM,
    MIN_ZOOM,
    NOTE,
    RAIN_LEGEND,
    RESOLUTION_KM,
    SNOW_LEGEND,
    SOURCE_MODEL,
    TYPICAL_LATENCY_MINUTES,
    NasaGibsImergCollector,
)
from app.models.rain_observed import ObservedRainFrame, ObservedRainLegendClass, ObservedRainMap


async def rain_observed_map(collector: Optional[NasaGibsImergCollector]) -> ObservedRainMap:
    """Global observed satellite precipitation frames and tile template, or why none can be served."""
    base = {
        "source_model": SOURCE_MODEL,
        "interval_minutes": INTERVAL_MINUTES,
        "resolution_km": RESOLUTION_KM,
        "typical_latency_minutes": TYPICAL_LATENCY_MINUTES,
        "licence": LICENCE,
        "attribution": credit_text("nasa_gibs_imerg"),
        "note": NOTE,
    }
    if collector is None:
        return ObservedRainMap(status="disabled", **base)
    fetched = await collector.frames()
    if fetched is None:
        return ObservedRainMap(status="unavailable", **base)
    held = fetched.value
    return ObservedRainMap(
        status="ok",
        bounds=list(BOUNDS),
        min_zoom=MIN_ZOOM,
        max_zoom=MAX_ZOOM,
        latest_utc=held.latest.time_utc,
        latest_at=held.latest.time_at,
        stale=fetched.stale,
        tile_url_template=API_TILE_PATH,
        frames=[
            ObservedRainFrame(stamp=f.stamp, time_utc=f.time_utc, time_at=f.time_at)
            for f in held.frames
        ],
        legend=[
            ObservedRainLegendClass(min_mm_h=c.min_mm_h, color=c.color, alpha=c.alpha)
            for c in RAIN_LEGEND
        ],
        snow_legend=[
            ObservedRainLegendClass(min_mm_h=c.min_mm_h, color=c.color, alpha=c.alpha)
            for c in SNOW_LEGEND
        ],
        **base,
    )
