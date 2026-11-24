"""The rain measured by the radars of Météo-France over metropolitan France and the overseas areas (``GET /v1/radar/{area}``)."""

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
    area: str = Field(
        "france",
        description="the radar mosaic: `france` (metropolitan), `antilles` (Guadeloupe, Martinique, Saint-Barthélemy, Saint-Martin's south), "
        "`reunion` (La Réunion and Mauritius) or `new-caledonia`",
    )
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


class RadarNowcastFrame(BaseModel):
    """One 5-minute forecast frame of the Météo-France PIAF rain nowcast."""

    epoch: int = Field(description="end of the 5 minutes of forecast rain, UTC seconds: it addresses the tiles")
    time: str = Field(description="`epoch` as an ISO 8601 date-time (UTC)")
    lead_minutes: int = Field(description="forecast lead time in minutes after `run_utc` (e.g. 5 to 60)")


class RadarNowcastFrance(BaseModel):
    """Météo-France PIAF +1h rain nowcast Web Mercator tiles relayed from a vigie-meteo gateway."""

    area: str = Field("france", description="metropolitan France and Corsica (Météo-France PIAF coverage)")
    status: Literal["ok", "not_configured", "unavailable"] = Field(
        description="`ok`: forecast frames are listed; `not_configured`: no gateway on this instance; `unavailable`: the gateway has no PIAF run"
    )
    source_model: str = Field("meteofrance-piaf", description="upstream nowcast model identifier")
    run_utc: Optional[int] = Field(None, description="PIAF run timestamp, UTC epoch seconds")
    run_at: Optional[str] = Field(None, description="`run_utc` as an ISO 8601 date-time (UTC)")
    frames: list[RadarNowcastFrame] = Field(description="forecast frames, earliest first (+5 to +60 minutes)")
    interval_minutes: int = 5
    tile_url_template: Optional[str] = Field(
        None,
        description="path, relative to the root of this API, of a 256 px Web Mercator tile (`{epoch}` one of `frames`, `{z}`, `{x}`, `{y}`), "
        "with the run filled in (`?run=`): the next run draws the same times again, so a tile is immutable under the address of its run; "
        "transparent where no rain is forecast or outside coverage",
    )
    bounds: Optional[list[float]] = Field(None, description="west, south, east, north (degrees) of the area the tiles can cover")
    min_zoom: Optional[int] = None
    max_zoom: Optional[int] = None
    legend: list[RadarLegendClass] = Field(default_factory=list, description="the intensity classes, weakest first")
    note: str
    licence: str
    attribution: str
