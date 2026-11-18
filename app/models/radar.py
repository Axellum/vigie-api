"""The rain measured by the radar of Météo-France over metropolitan France (``GET /v1/radar/france``)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class RadarFrame(BaseModel):
    """One image of the radar mosaic."""

    epoch: int = Field(description="end of the 5 minutes of rain the image shows, UTC seconds: it addresses the tiles")
    time: str = Field(description="`epoch` as an ISO 8601 date-time (UTC)")


class RadarLegendClass(BaseModel):
    """An intensity class of the tiles: from `min_mm_h` to the start of the next class."""

    min_mm_h: float = Field(description="rain rate at which the class starts (mm/h)")
    color: str = Field(description="`#rrggbb` colour of the class in the tiles")
    alpha: float = Field(description="own opacity of the colour in the tiles, 0 to 1")


class RadarFrance(BaseModel):
    status: Literal["ok", "not_configured", "unavailable"] = Field(
        description="`ok`: the images are listed; `not_configured`: no radar gateway on this instance; `unavailable`: the gateway has no image to give"
    )
    frames: list[RadarFrame] = Field(description="the images kept, oldest first (the last three hours, one every 5 minutes)")
    latest_utc: Optional[int] = Field(None, description="`epoch` of the newest image")
    interval_minutes: int = 5
    tile_url_template: Optional[str] = Field(
        None,
        description="path, relative to the root of this API, of a 256 px Web Mercator tile (`{epoch}` one of `frames`, `{z}`, `{x}`, `{y}`); "
        "transparent where it does not rain or the radars do not see; immutable",
    )
    bounds: Optional[list[float]] = Field(None, description="west, south, east, north (degrees) of the area the tiles can cover")
    min_zoom: Optional[int] = None
    max_zoom: Optional[int] = None
    legend: list[RadarLegendClass] = Field(default_factory=list, description="the intensity classes, weakest first")
    note: str
    licence: str
    attribution: str
