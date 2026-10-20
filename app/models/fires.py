"""The models of the fire routes (``GET /v1/fires``, ``GET /v1/fires/grid``): RFC 7946 FeatureCollections of NASA FIRMS detections.

The routes build their answer as a plain dictionary (up to thousands of features: validating each one through a model would cost more than
the work itself) and these models describe it for the OpenAPI document and for the tests, which validate every answer against them.

A detection is the source's own record: position, acquisition time, satellite, instrument, confidence, fire radiative power, brightness
temperatures and pixel size, as the FIRMS CSV gives them. What Vigie adds is named: the acquisition time in epoch seconds (the CSV has a
date and a ``HHMM`` time), the words of the confidence (``l`` / ``n`` / ``h``) and of ``daynight``, and the cell summaries of the grid.
A value FIRMS did not give is ``null``.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.models.schemas import GeoJSONPointGeometry


class FireProperties(BaseModel):
    """One detection (one VIIRS pixel flagged as an active fire)."""

    acq_utc: int = Field(..., description="Acquisition time, UTC epoch seconds (FIRMS gives a date and a HHMM time)")
    satellite: Optional[str] = Field(default=None, description="FIRMS satellite code: `N20` (NOAA-20), `N21` (NOAA-21)")
    instrument: Optional[str] = Field(default=None, description="`VIIRS`")
    confidence: Optional[Literal["low", "nominal", "high"]] = Field(
        default=None,
        description="FIRMS confidence class of the VIIRS pixel (`l`, `n`, `h`). Low-confidence daytime pixels are mostly sun glint; FIRMS advises caution",
    )
    frp_mw: Optional[float] = Field(default=None, description="Fire radiative power of the pixel (megawatts)")
    bright_ti4_k: Optional[float] = Field(default=None, description="Brightness temperature of the VIIRS I-4 channel (kelvin)")
    bright_ti5_k: Optional[float] = Field(default=None, description="Brightness temperature of the VIIRS I-5 channel (kelvin)")
    scan_km: Optional[float] = Field(default=None, description="Pixel size across the scan (kilometres)")
    track_km: Optional[float] = Field(default=None, description="Pixel size along the track (kilometres)")
    daynight: Optional[Literal["day", "night"]] = Field(default=None, description="`D` / `N` of FIRMS")
    version: Optional[str] = Field(default=None, description="FIRMS processing version, e.g. `2.0NRT`; `URT` = ultra real-time (United States and Canada)")


class FireFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    id: str = Field(..., description="Stable across refreshes: satellite, position (5 decimals) and acquisition time")
    geometry: GeoJSONPointGeometry
    properties: FireProperties


class FireSourceStatus(BaseModel):
    source: str = Field(..., description="FIRMS product, e.g. `VIIRS_NOAA20_NRT`")
    read_utc: int = Field(..., description="When the API last read it (UTC epoch seconds)")
    ok: bool = Field(..., description="False when the last attempt to read it failed (the previous reading is used)")


class FiresMetadata(BaseModel):
    standard: Literal["IETF RFC 7946 GeoJSON"] = "IETF RFC 7946 GeoJSON"
    generated_utc: int
    bbox: list[float] = Field(..., description="The box that was asked, as `[west, south, east, north]` (west greater than east: it crosses the antimeridian)")
    window_hours: float = Field(..., description="Detections acquired in the last `window_hours` hours (counted back from `generated_utc`, the start rounded down to 5 minutes)")
    since_utc: int
    min_confidence: Literal["low", "nominal", "high"]
    min_frp_mw: float
    limit: int
    count: int = Field(..., description="Features in this answer")
    total_matching: int = Field(..., description="Detections in the box and the window that pass the filters")
    truncated: bool = Field(..., description="True when `total_matching` is more than `limit`: the strongest (highest FRP) are kept")
    order: str = "frp_mw descending, then most recent first"
    data_read_utc: int = Field(..., description="When the API last read FIRMS")
    stale: bool = Field(..., description="True when the last attempt to read FIRMS failed: these are the detections of the previous reading")
    sources: list[FireSourceStatus]
    skipped_rows: int = Field(..., ge=0, description="Rows of the FIRMS files that had no readable position or time (not in the answer)")
    attribution: str
    disclaimer: str


class FiresCollection(BaseModel):
    """``GET /v1/fires``."""

    type: Literal["FeatureCollection"] = "FeatureCollection"
    metadata: FiresMetadata
    features: list[FireFeature]


class FireCellProperties(BaseModel):
    count: int = Field(..., ge=1, description="Detections in the cell")
    max_frp_mw: Optional[float] = Field(default=None, description="Highest fire radiative power in the cell (megawatts); null when FIRMS gave none")
    latest_utc: int = Field(..., description="Acquisition time of the most recent detection in the cell (UTC epoch seconds)")


class FireCellFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    id: str = Field(..., description="`<cell_deg>_<row>_<column>`: the cell, counted from -90 and -180 degrees")
    geometry: GeoJSONPointGeometry = Field(..., description="The centre of the cell; the cell spans `cell_deg` around it")
    properties: FireCellProperties


class FiresGridMetadata(BaseModel):
    standard: Literal["IETF RFC 7946 GeoJSON"] = "IETF RFC 7946 GeoJSON"
    generated_utc: int
    cell_deg: float = Field(..., description="Side of a cell, degrees. Cells are aligned on multiples of it from -90 / -180")
    bbox: list[float]
    window_hours: float
    since_utc: int
    min_confidence: Literal["low", "nominal", "high"]
    min_frp_mw: float
    limit: int
    count: int = Field(..., description="Cells in this answer")
    total_cells: int = Field(..., description="Cells that hold at least one detection")
    total_detections: int = Field(..., description="Detections in the box and the window that pass the filters (all cells)")
    truncated: bool = Field(..., description="True when `total_cells` is more than `limit`: the cells with the most detections are kept")
    order: str = "count descending, then max_frp_mw descending"
    data_read_utc: int
    stale: bool
    sources: list[FireSourceStatus]
    skipped_rows: int = Field(..., ge=0)
    attribution: str
    disclaimer: str


class FiresGrid(BaseModel):
    """``GET /v1/fires/grid``: detections counted per cell."""

    type: Literal["FeatureCollection"] = "FeatureCollection"
    metadata: FiresGridMetadata
    features: list[FireCellFeature]


def description_of(model: type[BaseModel]) -> dict[str, Any]:
    """The OpenAPI response entry of a fire route (the route returns a prebuilt dictionary, which FastAPI does not validate)."""
    return {"model": model, "description": (model.__doc__ or "").strip()}
