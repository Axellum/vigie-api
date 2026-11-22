"""The answer of ``GET /v1/rain/field`` from the collector (``app/collectors/meteofrance_rain_field.py``)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.attribution import credit_text
from app.collectors.meteofrance_rain_field import (
    LICENCE,
    NOTE,
    RAIN_LEGEND,
    MeteoFranceRainFieldCollector,
    RainFrameImage,
    RainLevelFrames,
    nearest_hour,
)
from app.models.rain_field import RainFieldFrame, RainFieldLegendClass, RainFieldLevel, RainFieldMap


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _frame(level_id: str, item: RainFrameImage) -> RainFieldFrame:
    return RainFieldFrame(
        stamp=item.stamp,
        valid_utc=int(item.valid.timestamp()),
        valid_at=_iso(item.valid),
        lead_hours=item.lead_hours,
        url=f"/v1/rain/field/{level_id}/{item.stamp}.png",
        size_bytes=len(item.png),
    )


def _level(bundle: RainLevelFrames) -> RainFieldLevel:
    return RainFieldLevel(
        id=bundle.level.id,  # type: ignore[arg-type]
        model=bundle.level.model,  # type: ignore[arg-type]
        bounds=list(bundle.bounds),
        width=bundle.width,
        height=bundle.height,
        min_zoom=bundle.level.min_zoom,
        run_utc=int(bundle.run.timestamp()),
        run_at=_iso(bundle.run),
        frames=[_frame(bundle.level.id, frame) for frame in bundle.frames],
    )


async def rain_field_map(collector: Optional[MeteoFranceRainFieldCollector], now_unix: float) -> RainFieldMap:
    """The forecast rain overlays across world, Europe and France, or why none can be served."""
    base = nearest_hour(now_unix)
    common = {"note": NOTE, "licence": LICENCE}
    if collector is None:
        return RainFieldMap(status="not_configured", **common)
    fetched = await collector.field()
    if fetched is None:
        return RainFieldMap(status="unavailable", base_utc=int(base.timestamp()), base_at=_iso(base), **common)
    held = fetched.value
    return RainFieldMap(
        status="ok",
        base_utc=int(held.base.timestamp()),
        base_at=_iso(held.base),
        stale=fetched.stale,
        levels=[_level(bundle) for bundle in held.levels],
        legend=[RainFieldLegendClass(min_mm_h=c.min_mm_h, color=c.color, alpha=c.alpha) for c in RAIN_LEGEND],
        attribution=credit_text("meteofrance_rain_field"),
        **common,
    )
