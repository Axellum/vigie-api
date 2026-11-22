"""Global observed satellite precipitation map layer (``GET /v1/rain/observed``): NASA GIBS GPM IMERG 30-minute tiles (ADR 0043)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ObservedRainFrame(BaseModel):
    """One 30-minute observed precipitation frame published by NASA GIBS IMERG."""

    stamp: str = Field(description="compact UTC timestamp (`YYYYMMDDHHMM`) that addresses the tiles")
    time_utc: int = Field(description="start of the 30-minute observation period, UTC epoch seconds")
    time_at: str = Field(description="`time_utc` as an ISO 8601 date-time (UTC)")


class ObservedRainLegendClass(BaseModel):
    """One intensity tick of the NASA GIBS ``GPM_Precipitation_Rate`` colour scale."""

    min_mm_h: float = Field(description="precipitation rate at which this tick starts (mm/h)")
    color: str = Field(description="`#rrggbb` colour from NASA GIBS `GPM_Precipitation_Rate.xml` (v1.3)")
    alpha: float = Field(description="opacity of the colour in the tiles, 0 to 1")


class ObservedRainMap(BaseModel):
    """Metadata, active observed frames and tile template for ``GET /v1/rain/observed``."""

    status: Literal["ok", "disabled", "unavailable"] = Field(
        description="`ok`: observed frames are listed; `disabled`: turned off with `VIGIE_OBSERVED_RAIN=0`; "
        "`unavailable`: NASA GIBS could not be read and no cached frame list is held"
    )
    source_model: str = Field("nasa-gibs-imerg-30min", description="upstream satellite precipitation product identifier")
    projection: Literal["epsg3857"] = Field(
        "epsg3857",
        description="Web Mercator (`EPSG:3857`, GoogleMapsCompatible_Level6) 256 px tiles",
    )
    bounds: Optional[list[float]] = Field(
        None,
        description="`[west, south, east, north]` in degrees of the Web Mercator tile grid",
    )
    min_zoom: Optional[int] = Field(None, description="minimum native tile zoom level (`0`)")
    max_zoom: Optional[int] = Field(None, description="maximum native tile zoom level (`6`; clients may overzoom)")
    interval_minutes: int = Field(30, description="interval between consecutive observed frames in minutes")
    resolution_km: int = Field(10, description="approximate native horizontal resolution of GPM IMERG (0.1° ≈ 10 km)")
    typical_latency_minutes: int = Field(
        300,
        description="typical delay behind real time in minutes (~4 to 6 hours for IMERG Early Run via GIBS)",
    )
    latest_utc: Optional[int] = Field(None, description="`time_utc` of the newest available frame")
    latest_at: Optional[str] = Field(None, description="`latest_utc` as an ISO 8601 date-time (UTC)")
    stale: bool = Field(False, description="upstream refresh failed: an earlier frame list is served")
    tile_url_template: Optional[str] = Field(
        None,
        description="path, relative to the root of this API, of a 256 px Web Mercator XYZ tile "
        "(`{stamp}` one of `frames`, `{z}` in 0..6, `{x}`, `{y}`); transparent below 0.1 mm/h or outside coverage; immutable",
    )
    frames: list[ObservedRainFrame] = Field(
        default_factory=list,
        description="available observed 30-minute frames, oldest first (up to the last 6 hours)",
    )
    legend: list[ObservedRainLegendClass] = Field(
        default_factory=list,
        description="NASA GIBS IMERG rain-rate scale ticks (mm/h, green to dark red), weakest first",
    )
    snow_legend: list[ObservedRainLegendClass] = Field(
        default_factory=list,
        description="NASA GIBS IMERG snow-rate scale ticks (mm/h, cyan to dark purple), weakest first",
    )
    licence: str
    attribution: str
    note: str
