"""The wave buoys near the surf spots (``GET /v1/surf/buoys``)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class Buoy(BaseModel):
    id: str = Field(description="CANDHIS campaign code")
    name: str
    lat: float
    lon: float
    real_time: Optional[bool] = Field(None, description="true when CANDHIS marked it real time when read; null: unknown")


class SpotBuoy(BaseModel):
    spot_id: str
    spot_name: str
    buoy: Buoy = Field(description="the nearest station of the CANDHIS file, whether or not it still measures")
    distance_km: float
    real_time_buoy: Optional[Buoy] = Field(None, description="the nearest one known to be real time; null when none is")
    real_time_distance_km: Optional[float] = None
    comparable: bool = Field(description=">= `max_comparable_km` rule: the nearest real-time buoy is close enough to compare with the spot")


class SpotBuoys(BaseModel):
    count: int
    max_comparable_km: float
    within_30_km: int
    within_50_km: int
    real_time_within_30_km: int
    real_time_within_50_km: int
    spots: list[SpotBuoy]
    note: str
    derived: list[str]
    licence: str
    attribution: str
