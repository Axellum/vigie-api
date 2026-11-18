"""The radar mosaic of Météo-France over metropolitan France: what ``GET /v1/radar/france`` says (the tiles are relayed by ``main.py``)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.attribution import credit_text
from app.collectors.meteofrance_radar import TILE_PATH, MeteoFranceRadarCollector
from app.models.radar import RadarFrame, RadarFrance, RadarLegendClass

LICENCE = "Licence Ouverte 2.0 (Météo-France, dataset 'Package Radar' of data.gouv.fr; see SOURCES.md)"
NOTE = (
    "The rain the radars of Météo-France measured, over metropolitan France (the areas the radars do not see are transparent): the "
    "rain accumulated over the 5 minutes before each image, shown as intensity classes (`legend`). An image is published about two "
    "minutes after its date and drawn by a gateway of this project; the tiles are not a forecast. **Not for safety.**"
)


async def radar_france(collector: Optional[MeteoFranceRadarCollector]) -> RadarFrance:
    """The images of the radar and how to ask for their tiles."""
    base = {"note": NOTE, "licence": LICENCE, "attribution": credit_text("meteofrance_radar")}
    if collector is None:
        return RadarFrance(status="not_configured", frames=[], **base)
    frames = await collector.frames()
    if frames is None:
        return RadarFrance(status="unavailable", frames=[], **base)
    return RadarFrance(
        status="ok",
        frames=[
            RadarFrame(epoch=e, time=datetime.fromtimestamp(e, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")) for e in frames.epochs
        ],
        latest_utc=frames.epochs[-1],
        tile_url_template=TILE_PATH,
        bounds=list(frames.bounds),
        min_zoom=frames.min_zoom,
        max_zoom=frames.max_zoom,
        legend=[RadarLegendClass(min_mm_h=c.min_mm_h, color=c.color, alpha=c.alpha) for c in frames.legend],
        **base,
    )
