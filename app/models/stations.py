"""The coastal weather stations near the surf spots (``GET /v1/surf/stations``), their latest 6-minute observation
(``GET /v1/surf/spots/{id}/station``) and the wind of every station of a box (``GET /v1/stations/wind``)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class CoastalStation(BaseModel):
    id: str = Field(description="Météo-France 8-digit station code (`NUM_POSTE` / `geo_id_insee`)")
    name: str
    commune: str
    lat: float
    lon: float
    alt_m: int
    kind: int = Field(
        description="Météo-France station type (`TYPE_POSTE_ACTUEL` in `POSTES_MF.csv`: 0 synoptic, 1 teletransmitted automatic, 2 other automatic)"
    )


class SpotStation(BaseModel):
    spot_id: str
    spot_name: str
    station: CoastalStation = Field(description="the nearest open real-time Météo-France coastal station")
    distance_km: float
    comparable: bool = Field(description="`distance_km <= max_comparable_km` rule: the nearest station is close enough to compare with the spot")


class StationMeasure(BaseModel):
    """The latest 6-minute observation of a coastal station, converted to the same units as the surf forecast."""

    measured_utc: int = Field(description="validity timestamp of the 6-minute observation, UTC")
    measured_at: str = Field(description="`measured_utc` as an ISO 8601 date-time")
    age_minutes: int = Field(description="minutes since the observation, never below 0")
    wind_speed_kmh: float = Field(description="10-minute mean wind speed at 10 m (`ff` converted from m/s)")
    wind_gust_kmh: Optional[float] = Field(None, description="10-minute maximum wind gust at 10 m (`fxi10` or `fxy` converted from m/s)")
    wind_from_deg: Optional[int] = Field(None, description="10-minute wind direction at 10 m (`dd`), degrees the wind blows from (0..359)")
    wind_relative: Optional[Literal["offshore", "cross-shore", "onshore"]] = Field(
        None, description="Ours: how the station's wind direction relates to the spot's `facing_deg`"
    )
    air_temp_c: Optional[float] = Field(None, description="air temperature at 2 m (`t` converted from Kelvin)")
    humidity_pct: Optional[int] = Field(None, description="relative humidity at 2 m (`u`)")
    pressure_hpa: Optional[float] = Field(None, description="sea-level pressure (`pmer` converted from Pa)")
    precip_6m_mm: Optional[float] = Field(None, description="precipitation over the last 6 minutes (`rr_per`)")


class StationWindComparison(BaseModel):
    """How the coastal station's current wind observation compares with the spot's hourly wind forecast (`compare=true`)."""

    status: Literal["ok", "unavailable", "no_matching_hour"] = Field(
        description="`ok`: the forecast hour within 30 minutes of `measured_utc` was compared; `unavailable`: the wind forecast gave no answer; "
        "`no_matching_hour`: the forecast does not cover `measured_utc`"
    )
    wind_model: str
    forecast_time_utc: Optional[int] = Field(None, description="The forecast hour (UTC) nearest to `measure.measured_utc` (within 30 minutes)")
    model_wind_speed_kmh: Optional[float] = Field(None, description="10 m wind speed (`wind_speed_10m`) of the forecast at `forecast_time_utc`")
    speed_diff_kmh: Optional[float] = Field(
        None, description="Ours: `measure.wind_speed_kmh - model_wind_speed_kmh` (positive when the station measures stronger wind than forecast)"
    )
    model_wind_gust_kmh: Optional[float] = Field(None, description="10 m wind gust (`wind_gusts_10m`) of the forecast at `forecast_time_utc`")
    gust_diff_kmh: Optional[float] = Field(None, description="Ours: `measure.wind_gust_kmh - model_wind_gust_kmh`")
    model_wind_from_deg: Optional[int] = Field(None, description="10 m wind direction (`wind_direction_10m`) of the forecast at `forecast_time_utc`")
    model_wind_relative: Optional[Literal["offshore", "cross-shore", "onshore"]] = Field(
        None, description="Ours: how `model_wind_from_deg` relates to the spot's `facing_deg`"
    )
    direction_diff_deg: Optional[int] = Field(
        None, description="Ours: angular difference (0 to 180 degrees) between `measure.wind_from_deg` and `model_wind_from_deg`"
    )
    regime_match: Optional[bool] = Field(
        None, description="Ours: true when `measure.wind_relative` and `model_wind_relative` agree (`offshore`, `cross-shore` or `onshore`)"
    )
    stale: bool = False


class SpotStationReading(BaseModel):
    """What the nearest coastal weather station with a current 6-minute wind observation measures, beside a surf spot."""

    spot_id: str
    spot_name: str
    status: Literal["ok", "not_configured", "unavailable", "no_current_reading"] = Field(
        description="`ok`: an observation; `not_configured`: this instance has no Météo-France observation key; `unavailable`: `DPPaquetObs` "
        "gave nothing and nothing is cached; `no_current_reading`: no coastal station has a wind observation within `max_age_hours`"
    )
    station: Optional[CoastalStation] = Field(
        None, description="the nearest coastal station whose latest wind observation is at most `max_age_hours` old"
    )
    distance_km: Optional[float] = None
    comparable: bool = Field(False, description="the station is within `max_comparable_km` of the spot: a setting of ours, not a fact")
    measure: Optional[StationMeasure] = None
    comparison: Optional[StationWindComparison] = Field(
        None, description="Comparison of `measure` with the hourly wind forecast of the spot at the nearest hour, when `compare=true` and `status` is `ok`"
    )
    stale: bool = Field(False, description="the observation comes from a cache that could not be refreshed")
    max_age_hours: float
    max_comparable_km: float
    note: str
    derived: list[str]
    licence: str
    attribution: str


class SpotStations(BaseModel):
    count: int
    max_comparable_km: float
    within_15_km: int
    within_25_km: int
    spots: list[SpotStation]
    note: str
    derived: list[str]
    licence: str
    attribution: str


class WindStation(BaseModel):
    """One Météo-France ground station and the wind its anemometer measured last (a 6-minute observation)."""

    id: str = Field(description="Météo-France 8-digit station code (`geo_id_insee`)")
    name: Optional[str] = Field(None, description="usual name in `POSTES_MF.csv`; null for a station the bundled list does not name")
    alt_m: Optional[int] = Field(None, description="altitude of the station in `POSTES_MF.csv`: a summit station measures the wind of the crest")
    lat: float = Field(description="as the 6-minute package gives it")
    lon: float = Field(description="as the 6-minute package gives it")
    measured_utc: int = Field(description="validity timestamp of the observation, UTC")
    age_minutes: int = Field(description="minutes since the observation, never below 0")
    wind_speed_kmh: float = Field(description="10-minute mean wind speed at 10 m (`ff` converted from m/s)")
    wind_gust_kmh: Optional[float] = Field(None, description="10-minute maximum wind gust at 10 m (`raf10`, else `fxi10` or `fxy`, from m/s)")
    wind_from_deg: Optional[int] = Field(None, description="degrees the wind blows from (0..359); null when calm or not measured")


class StationWindMap(BaseModel):
    """The wind measured now by every Météo-France ground station of a box, strongest first."""

    status: Literal["ok", "not_configured", "unavailable"] = Field(
        description="`ok`: the list (it may be empty: no station of the box measured the wind within `max_age_hours`); `not_configured`: this "
        "instance has no Météo-France observation key; `unavailable`: `DPPaquetObs` gave nothing and nothing is cached"
    )
    bbox: list[float] = Field(description="the box asked, `west,south,east,north`")
    count: int
    truncated: bool = Field(False, description="more stations than `limit` measured the wind in the box: the weakest were left out")
    measured_utc: Optional[int] = Field(None, description="the newest observation of the list, UTC")
    stale: bool = Field(False, description="the observations come from a cache that could not be refreshed")
    max_age_hours: float
    stations: list[WindStation] = Field(description="strongest mean wind first")
    note: str
    derived: list[str]
    licence: str
    attribution: str
