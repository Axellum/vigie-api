"""The radar mosaics of Météo-France (metropolitan France, Antilles, Réunion, New Caledonia): what ``GET /v1/radar/{area}`` says (the tiles are relayed by ``main.py``)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.attribution import credit_text
from app.collectors.meteofrance_radar import FRANCE, NOWCAST_TILE_PATH, MeteoFranceRadarCollector, tile_path
from app.models.radar import RadarFrame, RadarFrance, RadarLegendClass, RadarNowcastFrame, RadarNowcastFrance

LICENCE = "Licence Ouverte 2.0 (Météo-France, dataset 'Package Radar' of data.gouv.fr; see SOURCES.md)"
NOTE = (
    "The rain the radars of Météo-France measured, over the area named (the places the radars do not see are transparent): the "
    "rain accumulated over the 5 minutes before each image, shown as intensity classes (`legend`). An image is published about two "
    "minutes after its date and drawn by a gateway of this project; the tiles are not a forecast. **Not for safety.**"
)
NOWCAST_LICENCE = "Not verified (free of charge on the Météo-France API portal; catalogue entry names no licence; see SOURCES.md)"
NOWCAST_NOTE = (
    "Short-term rain nowcast (+5 to +60 min) from Météo-France PIAF, 5-minute steps drawn as tiles by a vigie-meteo gateway. "
    "A forecast, not a radar measure. Not for safety."
)


def _iso_utc(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def radar_france(collector: Optional[MeteoFranceRadarCollector], area: str = FRANCE) -> RadarFrance:
    """The images of the radar of an area and how to ask for their tiles."""
    base = {"area": area, "note": NOTE, "licence": LICENCE, "attribution": credit_text("meteofrance_radar")}
    if collector is None:
        return RadarFrance(status="not_configured", frames=[], **base)
    frames = await collector.frames(area)
    if frames is None:
        return RadarFrance(status="unavailable", frames=[], **base)
    return RadarFrance(
        status="ok",
        frames=[RadarFrame(epoch=e, time=_iso_utc(e)) for e in frames.epochs],
        latest_utc=frames.epochs[-1],
        tile_url_template=tile_path(area),
        bounds=list(frames.bounds),
        min_zoom=frames.min_zoom,
        max_zoom=frames.max_zoom,
        legend=[RadarLegendClass(min_mm_h=c.min_mm_h, color=c.color, alpha=c.alpha) for c in frames.legend],
        **base,
    )


async def radar_nowcast_france(collector: Optional[MeteoFranceRadarCollector]) -> RadarNowcastFrance:
    """The PIAF +1h forecast rain frames over France and how to ask for their tiles."""
    base = {
        "area": FRANCE,
        "note": NOWCAST_NOTE,
        "licence": NOWCAST_LICENCE,
        "attribution": credit_text("meteofrance_piaf_tiles"),
    }
    if collector is None:
        return RadarNowcastFrance(status="not_configured", frames=[], **base)
    nowcast = await collector.nowcast_frames()
    if nowcast is None:
        return RadarNowcastFrance(status="unavailable", frames=[], **base)
    return RadarNowcastFrance(
        status="ok",
        run_utc=nowcast.run_utc,
        run_at=_iso_utc(nowcast.run_utc),
        frames=[
            RadarNowcastFrame(epoch=f.epoch, time=_iso_utc(f.epoch), lead_minutes=f.lead_minutes)
            for f in nowcast.frames
        ],
        interval_minutes=max(1, nowcast.step_seconds // 60),
        tile_url_template=f"{NOWCAST_TILE_PATH}?run={nowcast.run_utc}",  # the next run draws the same times again
        bounds=list(nowcast.bounds),
        min_zoom=nowcast.min_zoom,
        max_zoom=nowcast.max_zoom,
        legend=[RadarLegendClass(min_mm_h=c.min_mm_h, color=c.color, alpha=c.alpha) for c in nowcast.legend],
        **base,
    )
