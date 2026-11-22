"""The answer of ``GET /v1/wind/field`` from the images the collector holds (``app/collectors/meteofrance_wind_field.py``)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.attribution import credit_text
from app.collectors.meteofrance_wind_field import NOTE, LevelImage, MeteoFranceWindFieldCollector, nearest_hour
from app.models.wind_field import WindFieldLevel, WindFieldMap

LICENCE = "Licence Ouverte 2.0 (Etalab): name Météo-France and the run, do not alter the data"


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _level(image: LevelImage) -> WindFieldLevel:
    return WindFieldLevel(
        id=image.level.id,  # type: ignore[arg-type]
        model=image.level.model,  # type: ignore[arg-type]
        run_utc=int(image.run.timestamp()),
        run_at=_iso(image.run),
        valid_utc=int(image.valid.timestamp()),
        valid_at=_iso(image.valid),
        bbox=list(image.bbox),
        width=image.width,
        height=image.height,
        step_deg=image.step_lat,
        whole_world=image.whole_world,
        scale_ms=image.scale_ms,
        min_zoom=image.level.min_zoom,
        image=f"/v1/wind/field/{image.level.id}/{image.stamp}.png",
        size_bytes=len(image.png),
    )


async def wind_field_map(collector: Optional[MeteoFranceWindFieldCollector], now_unix: float) -> WindFieldMap:
    """The grids of the hour, or the status that says why there are none."""
    valid = nearest_hour(now_unix)
    common = {"note": NOTE, "licence": LICENCE}
    if collector is None:
        return WindFieldMap(status="not_configured", **common)
    fetched = await collector.field()
    if fetched is None:
        return WindFieldMap(status="unavailable", valid_utc=int(valid.timestamp()), valid_at=_iso(valid), **common)
    held = fetched.value
    return WindFieldMap(
        status="ok",
        valid_utc=int(held.valid.timestamp()),
        valid_at=_iso(held.valid),
        stale=fetched.stale,
        levels=[_level(image) for image in held.images],
        attribution=credit_text("meteofrance_wind_field"),
        **common,
    )
