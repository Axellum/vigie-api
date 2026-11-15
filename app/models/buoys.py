"""The wave buoys near the surf spots (``GET /v1/surf/buoys``) and their latest measure (``GET /v1/surf/spots/{id}/buoy``)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Buoy(BaseModel):
    id: str = Field(description="CANDHIS campaign code or NOAA NDBC station identifier")
    name: str
    lat: float
    lon: float
    source: Literal["candhis", "ndbc"] = Field("candhis", description="buoy network (`candhis` or `ndbc`)")
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
    """The latest record of a buoy, as its network (CANDHIS or NOAA NDBC) gave it. A value the buoy did not give is `null`, never a stand-in."""

    measured_utc: int = Field(description="date of the record, UTC (CANDHIS 'Heure (TU)'; NDBC `latest_obs.txt` UTC)")
    measured_at: str = Field(description="`measured_utc` as an ISO 8601 date-time")
    age_minutes: int = Field(description="minutes since the record, never below 0 (a buoy has been dated ahead of the server's clock)")
    height_m: float = Field(description="significant wave height: H1/3 (CANDHIS / NDBC `WVHT`) or Hm0 according to the buoy (`height_kind`)")
    height_kind: str = Field(description="`H1/3` or `Hm0`: two ways to measure the significant height, close but not the same")
    hmax_m: Optional[float] = Field(None, description="the highest wave of the record")
    period_s: Optional[float] = Field(None, description="`TH1/3`, `T02` (CANDHIS) or `APD` (NDBC average period) according to the buoy (`period_kind`); not the peak period")
    period_kind: Optional[str] = None
    peak_period_s: Optional[float] = Field(None, description="the peak / dominant period (CANDHIS non-directional buoys or NDBC `DPD`)")
    direction_deg: Optional[float] = Field(None, description="direction at the peak (CANDHIS directional buoys or NDBC `MWD`); degrees clockwise from true North, coming from")
    spread_deg: Optional[float] = Field(None, description="spreading at the peak (`Etal. au pic`)")
    sea_temp_c: Optional[float] = Field(None, description="sea temperature measured by the buoy")


class BuoyForecastComparison(BaseModel):
    """How the buoy's current record compares with the MFWAM wave forecast of the spot at the nearest hour (`compare=true`)."""

    status: Literal["ok", "unavailable", "no_matching_hour"] = Field(
        description="`ok`: the model hour within 30 minutes of `measured_utc` was compared; `unavailable`: the wave model gave no answer; "
        "`no_matching_hour`: the forecast does not cover `measured_utc`"
    )
    swell_model: str
    swell_cell: Optional[dict[str, float]] = Field(None, description="The grid point of the wave model that answered")
    forecast_time_utc: Optional[int] = Field(None, description="The forecast hour (UTC) nearest to `measure.measured_utc` (within 30 minutes)")
    model_wave_height_m: Optional[float] = Field(None, description="Significant wave height (`wave_height`) of the model at `forecast_time_utc`")
    height_diff_m: Optional[float] = Field(None, description="Ours: `measure.height_m - model_wave_height_m` (positive when the sea is higher than modelled)")
    height_ratio: Optional[float] = Field(None, description="Ours: `measure.height_m / model_wave_height_m` when `model_wave_height_m > 0`")
    model_wave_period_s: Optional[float] = None
    model_swell_height_m: Optional[float] = None
    model_swell_period_s: Optional[float] = None
    model_swell_from_deg: Optional[int] = None
    direction_diff_deg: Optional[int] = Field(
        None, description="Ours: angular difference (0 to 180 degrees) between `measure.direction_deg` and `model_swell_from_deg`"
    )
    stale: bool = False


class SpotBuoyReading(BaseModel):
    """What the nearest buoy with a current record measures, beside a surf spot."""

    spot_id: str
    spot_name: str
    status: Literal["ok", "not_configured", "unavailable", "no_current_reading"] = Field(
        description="`ok`: a record; `not_configured`: neither CANDHIS nor NDBC is enabled on this instance; `unavailable`: active buoy "
        "networks gave nothing and nothing is cached; `no_current_reading`: no located buoy has a record of the last `max_age_hours` hours"
    )
    buoy: Optional[Buoy] = Field(None, description="the nearest located buoy whose latest record is at most `max_age_hours` old")
    distance_km: Optional[float] = None
    comparable: bool = Field(False, description="the buoy is within `max_comparable_km` of the spot: a setting of ours, not a fact")
    measure: Optional[BuoyMeasure] = None
    comparison: Optional[BuoyForecastComparison] = Field(
        None, description="Comparison of `measure` with the MFWAM forecast of the spot at the nearest hour, when `compare=true` and `status` is `ok`"
    )
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
