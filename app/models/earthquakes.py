"""Models of the earthquakes near a position (``GET /v1/earthquakes``).

The earthquakes the two seismic sources reported (USGS and EMSC, merged when they report the same event), within a radius of a
position and over the last hours. Nothing here is weather: the route costs no call to a weather provider. It is a layer of its
own, not an official warning: a magnitude says how strong an earthquake was, not what it did where you are.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.models.watch import WatchedSource


class EarthquakesQuery(BaseModel):
    """What was asked, as the API understood it."""

    lat: float
    lon: float
    radius_km: float
    min_magnitude: float
    hours: float


class NearbyEarthquake(BaseModel):
    """One earthquake, as its sources reported it, with its distance to the position (computed by Vigie)."""

    id: str = Field(..., description="Identifier of the event (the one of the first-hand source when two report it)")
    title: str = Field(..., description="Title as the source gives it (magnitude and region)")
    magnitude: float = Field(..., description="Magnitude, the highest of the sources that report the event")
    magnitude_type: Optional[str] = Field(default=None, description="Type of magnitude as the source names it (mww, mb, ml...)")
    time_utc: int = Field(..., description="Time of the event, UTC epoch seconds")
    lat: float = Field(..., description="Latitude of the epicentre")
    lon: float = Field(..., description="Longitude of the epicentre")
    depth_km: Optional[float] = Field(default=None, description="Depth of the hypocentre (km); null when the source gives none")
    tsunami_warning: bool = Field(default=False, description="The tsunami flag of USGS (oceanic event; not a warning of a tsunami centre)")
    sources: list[str] = Field(default_factory=list, description="The sources that report the event (USGS, EMSC)")
    distance_km: float = Field(..., description="Great-circle distance from the position to the epicentre (km), computed by Vigie")
    bearing_deg: Optional[float] = Field(default=None, description="Initial bearing from the position to the epicentre, computed by Vigie")
    bearing_cardinal: Optional[str] = Field(default=None, description="The same as a cardinal direction (N, NE, E...)")
    listed_now: bool = Field(
        ...,
        description="True when a source lists the event in its feed now; false when it comes from the history the instance kept "
        "(the feeds keep a short window: USGS the last day, EMSC its last 40 events)",
    )
    attribution: Optional[str] = Field(default=None, description="Credit of the sources that report the event")


class EarthquakesResponse(BaseModel):
    """The earthquakes near a position, strongest first."""

    generated_utc: int
    query: EarthquakesQuery
    feeds_min_magnitude: float = Field(
        ..., description="The smallest magnitude the feeds this instance reads list: a smaller `min_magnitude` finds nothing more"
    )
    sources: list[WatchedSource] = Field(
        ..., description="The seismic sources, whether they answered at this reading, and since when they have been read without a gap"
    )
    count: int = Field(..., ge=0, description="Earthquakes that match, before `limit`")
    earthquakes: list[NearbyEarthquake] = Field(default_factory=list, description="The strongest first, then the most recent")
    derived: list[str] = Field(
        default_factory=lambda: ["distance_km", "bearing_deg", "bearing_cardinal", "listed_now"],
        description="The fields computed by Vigie: everything else is a source's report",
    )
    attribution: Optional[str] = Field(
        default=None, description="Credit of the seismic sources that answered and of the sources of the earthquakes listed"
    )
