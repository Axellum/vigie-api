"""Pydantic models for Copernicus CAMS European pollen observations and forecast (via Open-Meteo Air Quality)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

PollenSpecies = Literal["alder", "birch", "grass", "mugwort", "olive", "ragweed"]

POLLEN_SPECIES: tuple[PollenSpecies, ...] = (
    "alder",
    "birch",
    "grass",
    "mugwort",
    "olive",
    "ragweed",
)

# Mapping from Vigie species key to the exact Open-Meteo Air Quality API variable name.
POLLEN_API_VARIABLES: dict[PollenSpecies, str] = {
    "alder": "alder_pollen",
    "birch": "birch_pollen",
    "grass": "grass_pollen",
    "mugwort": "mugwort_pollen",
    "olive": "olive_pollen",
    "ragweed": "ragweed_pollen",
}


class PollenSpeciesCounts(BaseModel):
    """Hourly pollen concentrations in grains/m3 from Copernicus CAMS Europe (``None`` when unknown or outside coverage)."""

    alder_grains_m3: Optional[float] = Field(default=None, ge=0.0, description="Alder pollen concentration (grains/m3)")
    birch_grains_m3: Optional[float] = Field(default=None, ge=0.0, description="Birch pollen concentration (grains/m3)")
    grass_grains_m3: Optional[float] = Field(default=None, ge=0.0, description="Grass pollen concentration (grains/m3)")
    mugwort_grains_m3: Optional[float] = Field(default=None, ge=0.0, description="Mugwort pollen concentration (grains/m3)")
    olive_grains_m3: Optional[float] = Field(default=None, ge=0.0, description="Olive pollen concentration (grains/m3)")
    ragweed_grains_m3: Optional[float] = Field(default=None, ge=0.0, description="Ragweed pollen concentration (grains/m3)")


class PollenDailyForecast(BaseModel):
    """Daily peak pollen forecast for one day (maximum of CAMS hourly values in grains/m3)."""

    date: str = Field(..., description="Calendar day from the provider, ISO 8601 (YYYY-MM-DD)")
    alder_max_grains_m3: Optional[float] = Field(default=None, ge=0.0, description="Daily peak alder pollen (grains/m3)")
    birch_max_grains_m3: Optional[float] = Field(default=None, ge=0.0, description="Daily peak birch pollen (grains/m3)")
    grass_max_grains_m3: Optional[float] = Field(default=None, ge=0.0, description="Daily peak grass pollen (grains/m3)")
    mugwort_max_grains_m3: Optional[float] = Field(default=None, ge=0.0, description="Daily peak mugwort pollen (grains/m3)")
    olive_max_grains_m3: Optional[float] = Field(default=None, ge=0.0, description="Daily peak olive pollen (grains/m3)")
    ragweed_max_grains_m3: Optional[float] = Field(default=None, ge=0.0, description="Daily peak ragweed pollen (grains/m3)")


class PollenStatus(BaseModel):
    """European pollen observations and up-to-4-day daily peak forecast (CAMS Europe through Open-Meteo Air Quality).

    Available only inside the CAMS European domain (~30 N..72 N, 25 W..45 E) during pollen seasons;
    ``None`` (JSON ``null``) outside Europe or when the provider supplies no pollen values.
    Informative only: never alters ``local_alert_level`` and carries no invented clinical risk threshold.
    """

    observed_time: Optional[str] = Field(
        default=None, description="Provider timestamp of the current pollen step (ISO 8601, GMT unless specified)"
    )
    current: Optional[PollenSpeciesCounts] = Field(
        default=None, description="Current hourly pollen concentrations (grains/m3); null when unknown"
    )
    daily: list[PollenDailyForecast] = Field(
        default_factory=list, description="Up to 4-day daily peak pollen forecast computed from hourly CAMS series"
    )
    dominant_species: Optional[PollenSpecies] = Field(
        default=None,
        description="Species with the highest non-zero current concentration; null when all current values are 0 or unknown",
    )
    derived: list[str] = Field(
        default_factory=list,
        description="Fields computed by Vigie from the CAMS hourly series (`daily`, `dominant_species`)",
    )
    stale: bool = Field(
        default=False,
        description="True when served from an expired cache entry (the provider could not or must not be asked)",
    )
    attribution: Optional[str] = Field(
        default=None,
        description="Credit the source asks for (Open-Meteo and CAMS ENSEMBLE data provider); null when there is no data",
    )


class PollensResponse(BaseModel):
    """Response returned by ``GET /v1/pollens`` for a GPS position."""

    version: str = Field(default="1.1")
    server_utc: int
    lat: float = Field(..., ge=-90.0, le=90.0, description="Requested WGS84 latitude")
    lon: float = Field(..., ge=-180.0, le=180.0, description="Requested WGS84 longitude")
    grid_lat: float = Field(..., description="Snapped CAMS/AQI grid latitude (0.1 deg)")
    grid_lon: float = Field(..., description="Snapped CAMS/AQI grid longitude (0.1 deg)")
    coverage: Literal["cams_europe", "outside_europe"] = Field(
        ..., description="Whether the grid cell lies inside the CAMS European Air Quality domain"
    )
    status: Literal["available", "outside_coverage", "unavailable"] = Field(
        ...,
        description="`available` when pollen data was returned, `outside_coverage` outside Europe (zero upstream calls), `unavailable` on outage/no data",
    )
    pollens: Optional[PollenStatus] = Field(default=None, description="Pollen observations and 4-day daily peak forecast; null when outside Europe or unavailable")
    stale: bool = Field(default=False, description="True when served from an expired cache entry")
    attribution: Optional[str] = Field(default=None, description="Credit for the pollen data; null when no data is returned")
