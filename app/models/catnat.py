"""Pydantic models for GASPAR historical natural disaster decrees (CatNat)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

CatNatHazardType = Literal[
    "floods",
    "drought",
    "landslides",
    "storms_cyclones",
    "hail_snow",
    "clay_shrink_swell",
    "avalanche",
    "other",
]

CatNatStatus = Literal["available", "confirmed_none", "outside_coverage"]


class CatNatDecree(BaseModel):
    """One natural disaster recognition decree (arrete CatNat) for a commune."""

    category: CatNatHazardType
    type_label: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    decree_date: Optional[str] = None


class CatNatTypeCount(BaseModel):
    """Decree count grouped by CatNat hazard category."""

    category: CatNatHazardType
    type_label: str
    count: int


class CatNatYearCount(BaseModel):
    """Decree count grouped by calendar year of event start (falling back to decree year)."""

    year: int
    count: int


class CatNatCommuneMatch(BaseModel):
    """French commune matched by INSEE code or nearest centroid."""

    insee_code: str
    name: str
    department: str
    centroid_lat: float
    centroid_lon: float
    centroid_distance_km: Optional[float] = None


class CatNatHistoryResponse(BaseModel):
    """Historical CatNat summary and paginated decrees for a French commune or position."""

    status: CatNatStatus
    lat: Optional[float] = None
    lon: Optional[float] = None
    country: Optional[str] = None
    commune: Optional[CatNatCommuneMatch] = None
    dataset_updated: str
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    total_decrees: Optional[int] = None
    filtered_decrees: Optional[int] = None
    first_decree_date: Optional[str] = None
    last_decree_date: Optional[str] = None
    by_type: list[CatNatTypeCount] = Field(default_factory=list)
    by_year: list[CatNatYearCount] = Field(default_factory=list)
    limit: int
    offset: int
    returned: int
    decrees: list[CatNatDecree] = Field(default_factory=list)
    derived: list[str] = Field(default_factory=list)
    note: str
    attribution: str
