"""The forecast wind for a map (``GET /v1/wind/field``): the grids of the hour, as images (see ``app/collectors/meteofrance_wind_field.py``)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class WindFieldLevel(BaseModel):
    """One grid of the hour, made into a PNG image: red is the east component of the wind, green the north one, blue says where the
    model has a value."""

    id: Literal["world", "europe", "france"] = Field(description="`world` (ARPEGE 0.25, every 2nd node), `europe` (ARPEGE 0.1), `france` (AROME 0.025)")
    model: Literal["ARPEGE", "AROME"]
    run_utc: int = Field(description="time of the model's run, UTC (the date of the data, to be named with the source)")
    run_at: str = Field(description="`run_utc` as an ISO 8601 date-time")
    valid_utc: int = Field(description="the hour the wind is forecast for, UTC")
    valid_at: str = Field(description="`valid_utc` as an ISO 8601 date-time")
    bbox: list[float] = Field(description="`[west, south, east, north]` of the grid's nodes, which are the centres of the image's pixels")
    width: int = Field(description="columns of the image, from west to east")
    height: int = Field(description="rows of the image, from north to south")
    step_deg: float = Field(description="degrees between two nodes, in latitude and in longitude")
    whole_world: bool = Field(description="the columns wrap around the globe: the one after the last is the first")
    scale_ms: float = Field(description="`value_ms = (byte - 127.5) / 127.5 * scale_ms`, for red (east) and green (north)")
    min_zoom: float = Field(description="the map zoom from which this grid is worth loading (the finer grid wins where it has a value)")
    image: str = Field(description="path of the PNG on this API (immutable: a new hour or run has a new path)")
    size_bytes: int = Field(description="size of the PNG")


class WindFieldMap(BaseModel):
    status: Literal["ok", "not_configured", "unavailable"] = Field(
        description="`not_configured`: no Météo-France key on this instance (or `VIGIE_WIND_FIELD=0`); `unavailable`: the portal could not be read and nothing is held"
    )
    valid_utc: Optional[int] = Field(None, description="the hour asked: the whole hour nearest to now, UTC")
    valid_at: Optional[str] = None
    stale: bool = Field(False, description="the portal failed: the grids of an earlier answer are served")
    levels: list[WindFieldLevel] = Field(default_factory=list, description="from the widest to the finest; a grid that failed is left out")
    note: str
    licence: str
    attribution: Optional[str] = None
