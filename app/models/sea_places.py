"""The places of the French coast (``GET /v1/sea/places``): ports, slipways, anchorages, dive sites and bathing sites.

Not to be confused with ``app/models/places.py`` (the places an event is near, and the points a user watches).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

PlaceType = Literal["port", "slipway", "anchorage", "dive", "bathing"]


class Place(BaseModel):
    id: str = Field(description="stable id: the kind, the name and four hexadecimal characters of the source reference")
    type: PlaceType = Field(
        description="`port` (a harbour with pleasure boats), `slipway` (a public boat ramp), `anchorage` (an anchorage area or an authorised "
        "mooring zone), `dive` (a dive site), `bathing` (a monitored bathing site)"
    )
    name: Optional[str] = Field(
        None, description="the name the source gives; `null` when it has none (a slipway, an anchorage)"
    )
    near: Optional[str] = Field(
        None,
        description="the nearest named locality of OpenStreetMap, within 6 km (worked out by Vigie); what an unnamed place is called after",
    )
    commune: Optional[str] = Field(None, description="the commune, for a bathing site (the source gives it)")
    lat: float
    lon: float
    distance_km: Optional[float] = Field(
        None, description="from the point asked with `near_lat` and `near_lon`; only then"
    )
    uses: Optional[list[str]] = Field(
        None,
        description="a port: what it is used for (`leisure`, `fishing`, `passengers`, `trade`, `oil`, `military`), from the Sandre",
    )
    quality: Optional[Literal["excellent", "good", "sufficient", "poor", "unclassified"]] = Field(
        None,
        description="a bathing site: the sanitary class of its water over the four years 2021 to 2024 (a rolling four-year class), not the water of today. "
        "It does not say whether it is safe to swim now.",
    )
    source: Literal["sandre", "osm", "qsb"] = Field(
        description="`sandre` (Sandre, ports), `osm` (OpenStreetMap), `qsb` (sanitary quality of the bathing sites)"
    )
    ref: str = Field(
        description="the reference in the source: the Sandre port code, the OpenStreetMap element (`node/1`), the bathing site"
    )


class PlaceAnswer(BaseModel):
    place: Place
    built_utc: str = Field(description="the date of the OpenStreetMap data the file was built from (UTC)")
    note: str
    derived: list[str]
    licence: str
    attribution: str


class PlaceList(BaseModel):
    count: int = Field(description="how many places match (before `limit` and `offset`)")
    returned: int
    limit: int
    offset: int
    by_type: dict[str, int] = Field(description="how many places of each kind match")
    places: list[Place]
    built_utc: str = Field(description="the date of the OpenStreetMap data the file was built from (UTC)")
    note: str
    derived: list[str] = Field(description="what Vigie works out and the source does not give")
    licence: str
    attribution: str
