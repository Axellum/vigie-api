"""The forecast rain for a map (``GET /v1/rain/field``): AROME and ARPEGE 1-hour accumulation overlays (ADR 0041)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class RainFieldFrame(BaseModel):
    """One forecast step of a grid: 1-hour accumulated precipitation ending at ``valid_utc``."""

    stamp: str = Field(description="image identifier (`valid`-`run`, `YYYYMMDDHH-YYYYMMDDHH`)")
    valid_utc: int = Field(description="end of the 1-hour accumulation period, UTC epoch seconds")
    valid_at: str = Field(description="`valid_utc` as an ISO 8601 date-time (UTC)")
    lead_hours: int = Field(description="forecast lead time in hours from `base_utc` (e.g. 1, 2, 3, 6, 12, 24)")
    url: str = Field(description="path of the RGBA PNG overlay on this API (immutable)")
    size_bytes: int = Field(description="size of the PNG in bytes")


class RainFieldLevel(BaseModel):
    """One geographical grid (world, Europe or France) with its forecast lead frames."""

    id: Literal["world", "europe", "france"] = Field(
        description="`world` (ARPEGE 0.25), `europe` (ARPEGE 0.1), `france` (AROME 0.025)"
    )
    model: Literal["ARPEGE", "AROME"]
    bounds: list[float] = Field(
        description="`[west, south, east, north]` in degrees; image rows are linearly spaced in Web Mercator Y"
    )
    width: int = Field(description="image width in pixels, west to east")
    height: int = Field(description="image height in pixels, north to south")
    min_zoom: float = Field(description="map zoom from which this grid should be preferred over coarser grids")
    run_utc: int = Field(description="time of the model's run, UTC epoch seconds (the date of the data)")
    run_at: str = Field(description="`run_utc` as an ISO 8601 date-time (UTC)")
    frames: list[RainFieldFrame] = Field(description="available forecast frames, earliest lead first")


class RainFieldLegendClass(BaseModel):
    """One precipitation intensity class of the RGBA overlays: from ``min_mm_h`` to the next class."""

    min_mm_h: float = Field(description="1-hour precipitation rate at which the class starts (mm/h)")
    color: str = Field(description="`#rrggbb` colour of the class in the PNG overlays")
    alpha: float = Field(description="opacity of the class in the PNG overlays, 0 to 1")


class RainFieldMap(BaseModel):
    status: Literal["ok", "not_configured", "unavailable"] = Field(
        description="`not_configured`: no Météo-France key on this instance (or `VIGIE_RAIN_FIELD=0`); "
        "`unavailable`: the portal could not be read and nothing is held"
    )
    base_utc: Optional[int] = Field(None, description="reference UTC hour (`lead_hours` is measured from this hour)")
    base_at: Optional[str] = Field(None, description="`base_utc` as an ISO 8601 date-time (UTC)")
    stale: bool = Field(False, description="the portal failed: an earlier answer is served")
    projection: Literal["mercator"] = Field(
        "mercator",
        description="image rows are linearly spaced in Web Mercator Y over `bounds` (ready for a MapLibre `image` source)",
    )
    levels: list[RainFieldLevel] = Field(
        default_factory=list, description="from widest (`world`) to finest (`france`); a grid that failed is left out"
    )
    legend: list[RainFieldLegendClass] = Field(
        default_factory=list, description="intensity classes, weakest first (transparent below 0.1 mm/h and outside grid)"
    )
    licence: str
    attribution: Optional[str] = None
    note: str
