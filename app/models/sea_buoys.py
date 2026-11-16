"""The offshore buoys and their latest record (``GET /v1/sea/buoys``)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class SeaBuoy(BaseModel):
    """The latest record of one offshore buoy, as its network gave it. A value the buoy did not give is `null`, never a stand-in."""

    id: str = Field(description="WMO identifier of the buoy (Météo-France `geo_id_wmo`)")
    name: str = Field(description="name of the buoy, from the network (`BOUEE_GASCOGNE` is given as `Gascogne`)")
    source: Literal["meteofrance"] = Field("meteofrance", description="buoy network")
    lat: float
    lon: float
    distance_km: Optional[float] = Field(None, description="from `near_lat`, `near_lon` when they are given (calculated by Vigie)")
    measured_utc: int = Field(description="date of the record, UTC (records are hourly)")
    measured_at: str = Field(description="`measured_utc` as an ISO 8601 date-time")
    age_minutes: int = Field(description="minutes since the record, never below 0")
    wave_height_m: Optional[float] = Field(
        None, description="significant wave height (`haut_vag`); which one (H1/3 or Hm0) Météo-France does not state"
    )
    wave_period_s: Optional[float] = Field(None, description="mean wave period (`per_moy_vag`)")
    wave_from_deg: Optional[int] = Field(
        None, description="wave direction (`dir_vag`), degrees from true North; the convention is not stated (WMO practice: coming from)"
    )
    sea_temp_c: Optional[float] = Field(None, description="sea temperature measured by the buoy")
    air_temp_c: Optional[float] = None
    wind_speed_kmh: Optional[float] = Field(None, description="mean wind at the buoy")
    wind_gust_kmh: Optional[float] = Field(None, description="gust at the buoy (`rafper`)")
    wind_from_deg: Optional[int] = Field(None, description="where the wind comes from, degrees; null in a calm")
    pressure_hpa: Optional[float] = Field(None, description="sea-level pressure")


class SeaBuoys(BaseModel):
    status: Literal["ok", "not_configured", "unavailable"] = Field(
        description="`ok`: the buoys were read (the list can still be empty); `not_configured`: no Météo-France key on this instance; "
        "`unavailable`: Météo-France gave nothing and nothing is cached"
    )
    count: int
    buoys: list[SeaBuoy] = Field(description="nearest first with `near_lat`, `near_lon`; else from north to south")
    stale: bool = Field(False, description="the records come from a cache that could not be refreshed")
    max_age_hours: float = Field(description="a buoy whose latest record is older than this is left out")
    note: str
    derived: list[str]
    licence: str
    attribution: str
