"""Models of the sky routes: the aurora (``GET /v1/sky/aurora``, ``GET /v1/sky/aurora.geojson``).

Two kinds of values, never mixed: what NOAA's Space Weather Prediction Center (SWPC) publishes, as it publishes it (the OVATION
grid, the Kp intervals, the solar wind, the messages), and the **estimate** of Vigie for a place (ADR 0012), a method of this
project measured on past nights, listed in ``derived`` and not an official forecast.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

AuroraLevel = Literal["likely", "possible", "unlikely", "very_unlikely"]


class OvalSummary(BaseModel):
    """The OVATION forecast SWPC published last (the map is ``/v1/sky/aurora.geojson``)."""

    observation_utc: int = Field(..., description="When the solar wind that drives the forecast was observed (UTC epoch seconds)")
    forecast_utc: int = Field(..., description="The time the forecast is for (UTC epoch seconds), 30 to 90 minutes after the observation")
    max_value: int = Field(..., description="The highest value of the grid (SWPC: an intensity; a viewing probability in % can be derived linearly)")


class KpPeriod(BaseModel):
    """One 3-hour interval of SWPC's Kp file."""

    start_utc: int
    end_utc: int
    kp: float = Field(..., ge=0.0, le=9.0, description="Planetary Kp index")
    kind: Literal["observed", "estimated", "predicted"] = Field(..., description="As SWPC marks the interval")
    noaa_scale: Optional[str] = Field(default=None, description="The NOAA G level SWPC wrote for the interval (G1 to G5), null when none")


class SolarWindNow(BaseModel):
    """The latest solar wind row of SWPC's last hour, measured at L1 (about 1.5 million km sunward)."""

    measured_utc: int = Field(..., description="Time of the measurement at L1 (UTC epoch seconds)")
    propagated_utc: Optional[int] = Field(
        default=None,
        description="The propagated time SWPC's file gives for the row (`propagated_time_tag`), presumably its arrival near the Earth: "
        "SWPC does not document it on the pages read (not verified)",
    )
    speed_km_s: Optional[float] = None
    density_cm3: Optional[float] = None
    bt_nt: Optional[float] = Field(default=None, description="Total interplanetary magnetic field (nT)")
    bz_nt: Optional[float] = Field(default=None, description="North-south component (nT); southward (negative) favours aurora")


class SpaceWeatherMessageOut(BaseModel):
    """A geomagnetic message of SWPC (watch, warning, alert, summary), its text untouched."""

    product_id: str
    code: Optional[str] = Field(default=None, description="The 'Space Weather Message Code' of the message")
    serial: Optional[str] = None
    issued_utc: int
    headline: Optional[str] = Field(default=None, description="The line that names the message (WATCH:, WARNING:, ALERT:, SUMMARY:...)")
    message: str = Field(..., description="The message as SWPC wrote it (line ends normalized)")


class OvationNearby(BaseModel):
    """What SWPC's OVATION grid says around the place (SWPC's values; the search radius is SWPC's 1,000 km)."""

    forecast_utc: int
    value_here: int = Field(..., description="The value of the grid cell of the place")
    strongest_within_1000_km: Optional[int] = Field(default=None, description="The highest value within 1,000 km, null when the grid is 0 there")
    strongest_distance_km: Optional[float] = Field(default=None, description="How far that cell is (km, computed by Vigie)")
    strongest_lat: Optional[float] = None
    strongest_lon: Optional[float] = None


class AuroraHour(BaseModel):
    """One hour of the coming night at the place: the factors of the estimate and the estimate (ADR 0012)."""

    time_utc: int = Field(..., description="Start of the hour (UTC epoch seconds)")
    kp: Optional[float] = Field(default=None, description="SWPC's Kp of the 3-hour interval of that hour")
    kp_kind: Optional[Literal["observed", "estimated", "predicted"]] = None
    distance_to_edge_km: Optional[float] = Field(
        default=None, description="How far the place is on the equator side of the aurora's edge for that Kp (km; 0 or less: under it)"
    )
    activity: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    sun_altitude_deg: float
    darkness: float = Field(..., ge=0.0, le=1.0)
    moon_altitude_deg: float
    moon_illuminated: float = Field(..., ge=0.0, le=1.0, description="Illuminated fraction of the Moon")
    moon: float = Field(..., ge=0.0, le=1.0)
    cloud_cover_pct: Optional[float] = Field(default=None, description="Forecast cloud cover of that hour; null when unknown")
    clouds: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    chance_clear_sky: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="The estimate without the clouds")
    chance: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="The estimate with the clouds; null when they are unknown")
    level: Optional[AuroraLevel] = Field(default=None, description="The word for `chance` (ours); null when the clouds are unknown")
    level_clear_sky: Optional[AuroraLevel] = None


class AuroraWindow(BaseModel):
    """The best hours of the night: the run of hours around the best one whose chance is at least 80 % of it."""

    start_utc: int
    end_utc: int
    chance: float = Field(..., ge=0.0, le=1.0)
    level: AuroraLevel
    with_clouds: bool = Field(..., description="False when the window was chosen on the clear-sky chance (the clouds are unknown)")


class AuroraEstimate(BaseModel):
    """The estimate of Vigie for the coming night at the place (ADR 0012): ours, not SWPC's, not an official forecast."""

    night_start_utc: Optional[int] = Field(default=None, description="First hour of the coming night with the Sun below -6 degrees")
    night_end_utc: Optional[int] = Field(default=None, description="End of the last such hour")
    hours: list[AuroraHour] = Field(default_factory=list)
    best: Optional[AuroraWindow] = None
    level: Optional[AuroraLevel] = Field(default=None, description="The level of the best hour (with the clouds when they are known)")
    clouds_known: bool = Field(..., description="Whether every hour of the night has a cloud forecast")
    method: str = Field(..., description="Where the method and its measure are written")


class AuroraPlace(BaseModel):
    """The place asked and what the API worked out for it."""

    lat: float
    lon: float
    magnetic_lat: float = Field(..., description="Modified apex latitude of IGRF-14 (computed by Vigie)")
    utc_offset_s: Optional[int] = Field(default=None, description="Local offset from UTC given by the weather forecast; null when unknown")
    ovation: Optional[OvationNearby] = None
    estimate: Optional[AuroraEstimate] = None
    no_estimate_reason: Optional[str] = Field(default=None, description="Why `estimate` is null")


class AuroraResponse(BaseModel):
    """SWPC's aurora data, and for a place the estimate of Vigie."""

    generated_utc: int
    oval: Optional[OvalSummary] = None
    kp: list[KpPeriod] = Field(default_factory=list, description="SWPC's Kp intervals from 24 hours ago to the end of its forecast")
    kp_now: Optional[KpPeriod] = Field(default=None, description="The interval of now")
    kp_max_next_24h: Optional[KpPeriod] = Field(default=None, description="The highest Kp of the next 24 hours")
    solar_wind: Optional[SolarWindNow] = None
    messages: list[SpaceWeatherMessageOut] = Field(
        default_factory=list, description="SWPC's geomagnetic messages of the last 72 hours, newest first"
    )
    place: Optional[AuroraPlace] = None
    unavailable: list[str] = Field(default_factory=list, description="The SWPC products that could not be read (no copy cached)")
    derived: list[str] = Field(
        default_factory=lambda: [
            "place.magnetic_lat",
            "place.ovation.strongest_distance_km",
            "place.estimate",
            "kp_now",
            "kp_max_next_24h",
        ],
        description="The fields computed by Vigie: everything else is SWPC's",
    )
    attribution: Optional[str] = Field(default=None, description="Credit of SWPC (and of the weather source when clouds were used)")


class AuroraOvalFeature(BaseModel):
    """One band of the OVATION grid: the cells whose value is in ``[min, max)``."""

    type: Literal["Feature"] = "Feature"
    geometry: dict[str, Any]
    properties: dict[str, Any]


class AuroraOvalCollection(BaseModel):
    """The OVATION grid of SWPC drawn as bands of its values (RFC 7946). The levels of the bands are ours; the values are SWPC's."""

    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[AuroraOvalFeature] = Field(default_factory=list)
    observation_utc: Optional[int] = None
    forecast_utc: Optional[int] = None
    levels: list[int] = Field(default_factory=list, description="The lower bound of each band (ours)")
    attribution: Optional[str] = None
