"""The wave buoys near the surf spots (``GET /v1/surf/buoys``) and their latest measure (``GET /v1/surf/spots/{id}/buoy``)."""

from __future__ import annotations

from typing import Literal, Optional

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


class BuoyMeasure(BaseModel):
    """The latest record of a buoy, as CANDHIS gave it. A value the buoy did not give is `null`, never a stand-in."""

    measured_utc: int = Field(description="date of the record, UTC (the CANDHIS site heads that column 'Heure (TU)'; the guide does not say)")
    measured_at: str = Field(description="`measured_utc` as an ISO 8601 date-time")
    age_minutes: int = Field(description="minutes since the record, never below 0 (a buoy has been dated ahead of the server's clock)")
    height_m: float = Field(description="significant wave height: H1/3 or Hm0 according to the buoy (`height_kind`)")
    height_kind: str = Field(description="`H1/3` or `Hm0`: two ways to measure the significant height, close but not the same")
    hmax_m: Optional[float] = Field(None, description="the highest wave of the record")
    period_s: Optional[float] = Field(None, description="`TH1/3` or `T02` according to the buoy (`period_kind`); not the peak period")
    period_kind: Optional[str] = None
    peak_period_s: Optional[float] = Field(None, description="the peak period; only the non-directional buoys give it")
    direction_deg: Optional[float] = Field(None, description="direction at the peak; only the directional buoys give it. The convention is not "
                                           "stated by the guide; the values read on 2026-11-09 are those of a south-westerly swell, so: the direction it comes from")
    spread_deg: Optional[float] = Field(None, description="spreading at the peak (`Etal. au pic`)")
    sea_temp_c: Optional[float] = Field(None, description="sea temperature measured by the buoy")


class SpotBuoyReading(BaseModel):
    """What the nearest buoy with a current record measures, beside a surf spot."""

    spot_id: str
    spot_name: str
    status: Literal["ok", "not_configured", "unavailable", "no_current_reading"] = Field(
        description="`ok`: a record; `not_configured`: this instance has no CANDHIS key; `unavailable`: CANDHIS gave nothing and nothing is "
        "cached; `no_current_reading`: no located buoy has a record of the last `max_age_hours` hours"
    )
    buoy: Optional[Buoy] = Field(None, description="the nearest located buoy whose latest record is at most `max_age_hours` old")
    distance_km: Optional[float] = None
    comparable: bool = Field(False, description="the buoy is within `max_comparable_km` of the spot: a setting of ours, not a fact")
    measure: Optional[BuoyMeasure] = None
    stale: bool = Field(False, description="the record comes from a cache that could not be refreshed")
    complete: bool = Field(
        True,
        description="false when one of the three kinds of buoy could not be read and nothing is cached for it: `buoy` is then the nearest "
        "among the buoys that were read, not necessarily the nearest of all",
    )
    max_age_hours: float
    max_comparable_km: float
    note: str
    derived: list[str]
    licence: str
    attribution: str


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
