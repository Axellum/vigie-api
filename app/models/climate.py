"""Pydantic models of the climate normals and records of Météo-France (``GET /v1/climate/normals``)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

ClimateNormalsStatus = Literal["available", "outside_coverage"]


class ClimateStation(BaseModel):
    """The station whose sheet answers, as the sheet describes it, with the distance calculated by Vigie."""

    id: str = Field(description="Météo-France station number (8 digits)")
    name: str
    lat: float
    lon: float
    altitude_m: int
    distance_km: float = Field(description="Distance from the asked position to the station (calculated by Vigie)")
    sheet_edited: Optional[str] = Field(default=None, description="Date the sheet was edited by Météo-France (ISO)")
    records_until: Optional[str] = Field(
        default=None,
        description="Last day the records of the station cover (ISO); a station that stopped measuring keeps its old records",
    )
    active: bool = Field(description="True when the records run to the latest edition of the sheets (the station still measures)")


class ClimateNormal(BaseModel):
    """One monthly value of the sheet and the period it was computed over."""

    value: Optional[float] = Field(default=None, description="Null when the sheet marks it missing")
    period: Optional[str] = Field(default=None, description="Years the value was computed over, as the sheet says (e.g. `1991-2020`)")


class ClimateRecord(BaseModel):
    """One record of the sheet: the value, the day it happened, and the span of years the records are kept for."""

    value: Optional[float] = None
    date: Optional[str] = Field(default=None, description="Day of the record (ISO); the year alone for the record of the whole year")
    since: Optional[str] = Field(default=None, description="First day of the records of this measure (ISO)")
    until: Optional[str] = Field(default=None, description="Last day of the records of this measure (ISO)")


class ClimateNormals(BaseModel):
    """The normals of the month (means and average counts of days)."""

    tmax_mean_c: ClimateNormal
    tmin_mean_c: ClimateNormal
    tmean_c: ClimateNormal
    rain_mm: ClimateNormal = Field(description="Average rain of the whole month")
    rain_days: ClimateNormal = Field(description="Average number of days with at least 1 mm of rain")
    frost_days: ClimateNormal = Field(description="Average number of days with a minimum at or below 0 °C")
    hot_days: ClimateNormal = Field(description="Average number of days with a maximum at or above 30 °C")
    sunshine_h: ClimateNormal = Field(description="Average hours of sunshine (few stations measure it)")


class ClimateRecords(BaseModel):
    """The records of the month, or of the whole year."""

    tmax_c: ClimateRecord
    tmin_c: ClimateRecord
    rain_day_mm: ClimateRecord = Field(description="Most rain in one day")
    gust_ms: ClimateRecord = Field(description="Strongest gust (m/s; few stations measure it)")


class ClimateComparison(BaseModel):
    """A value given by the caller set against the normal and the record of the month (calculated by Vigie)."""

    value: float
    normal: Optional[float] = Field(default=None, description="The normal of the month it is compared with; null when unknown")
    difference: Optional[float] = Field(default=None, description="`value` minus `normal`; null when the normal is unknown")
    record: Optional[float] = Field(default=None, description="The record of the month; null when unknown")
    record_date: Optional[str] = None
    beats_record: Optional[bool] = Field(
        default=None,
        description="True when `value` goes past the record of the month (above for a maximum or rain, below for a minimum); "
        "null when there is no record, or when the station no longer measures (its records stopped)",
    )


class ClimateComparisons(BaseModel):
    tmax_c: Optional[ClimateComparison] = None
    tmin_c: Optional[ClimateComparison] = None
    rain_mm: Optional[ClimateComparison] = Field(
        default=None, description="Rain of one day: only set against the record of the month (a monthly mean is not a daily normal)"
    )


class ClimateNormalsResponse(BaseModel):
    """The normals and records of the nearest station that has a climate sheet, for one month."""

    status: ClimateNormalsStatus
    lat: float
    lon: float
    month: int = Field(description="Month of the normals and records (1-12)")
    station: Optional[ClimateStation] = None
    nearest_distance_km: Optional[float] = Field(
        default=None, description="Distance to the nearest sheet when it is farther than `max_distance_km` (`outside_coverage`)"
    )
    dataset_updated: Optional[str] = Field(default=None, description="Latest edition of the sheets in the bundle (ISO)")
    normals: Optional[ClimateNormals] = None
    records: Optional[ClimateRecords] = None
    records_all_year: Optional[ClimateRecords] = Field(default=None, description="The records of the whole year (the year alone as date)")
    comparison: Optional[ClimateComparisons] = Field(
        default=None, description="Present when the caller gave `tmax`, `tmin` or `rain_mm`"
    )
    derived: list[str] = Field(default_factory=list, description="Fields calculated by Vigie, not read from the sheet")
    note: str
    attribution: str
