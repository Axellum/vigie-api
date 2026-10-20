"""The forecast of a hazard as an RFC 7946 FeatureCollection: ``GET /v1/geojson/hazards/{id}/forecast``.

A hazard of ``/v1/geojson/hazards`` is a point (the current position of a storm, the epicentre of an earthquake). When
the source also gives where it goes, ``has_forecast`` is true on that hazard and this answer holds the geometries:

- a tropical cyclone of the NHC: ``cone`` (Polygon, or MultiPolygon across the date line: the cone of uncertainty), ``track``
  (LineString, or MultiLineString across the date line: the forecast track), ``position`` (Point, the forecast positions: ``tau_h``, ``valid_utc``, ``wind_kt``, ``gust_kt``, ``pressure_hpa``) and
  ``watch_warning`` (LineString, a coastal stretch under a watch or a warning: ``code``, ``label``);
- a tsunami of the U.S. warning centres: ``epicentre`` (Point) and ``eta`` (Point with ``eta_utc``, the estimated arrival time of
  the first wave at a place; a place the message gives no coordinates for has a ``null`` geometry).

Every feature carries ``properties.kind``. The collection says who issued the forecast (``source``, ``issued_utc``, ``advisory``,
``advisory_url``) and the credit (``attribution``). Nothing is drawn that the source did not give: with no forecast the
collection is empty and ``reason`` says why.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class ForecastFeature(BaseModel):
    """A feature of a forecast. ``properties.kind`` tells what it is; the geometry is ``null`` for a place without coordinates."""

    type: Literal["Feature"] = "Feature"
    id: str
    geometry: Optional[dict[str, Any]] = None
    properties: dict[str, Any]


class HazardForecast(BaseModel):
    """The forecast of one hazard (RFC 7946 FeatureCollection, with foreign members for who issued it)."""

    type: Literal["FeatureCollection"] = "FeatureCollection"
    hazard_id: str = Field(..., description="The `id` of the hazard in /v1/geojson/hazards")
    title: Optional[str] = Field(default=None, description="Title of the hazard")
    kind: Optional[Literal["tropical_cyclone", "tsunami"]] = Field(
        default=None, description="What kind of forecast this is; null when the hazard has none"
    )
    source: Optional[str] = Field(default=None, description="Who issued it: NHC, PTWC or NTWC")
    issued_utc: Optional[int] = Field(default=None, description="Time of issue of the forecast (UTC epoch seconds)")
    advisory: Optional[str] = Field(
        default=None, description="Its number as the issuer writes it (NHC advisory `13A`), or the identifier of the tsunami message"
    )
    advisory_url: Optional[str] = Field(default=None, description="Link to the issuer's own advisory or bulletin")
    attribution: Optional[str] = Field(default=None, description="The credit to show with this forecast")
    reason: Optional[str] = Field(default=None, description="Set when the collection is empty: why there is no forecast")
    generated_utc: int = Field(..., description="Server time")
    bbox: Optional[list[float]] = Field(default=None, description="[min_lon, min_lat, max_lon, max_lat] of the geometries")
    features: list[ForecastFeature] = Field(default_factory=list)
