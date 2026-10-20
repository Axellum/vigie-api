"""Models of the places an event is near (``near_places``) and of the points a user watches (``/v1/watched-points``).

Both are **computed by Vigie**, not reported by a source: a hazard says "6 km from the Azuero Fault Zone (active fault)", a watched
point says "nothing within 50 km in the last 7 days". They never change the severity of a hazard, and the field that holds
``near_places`` is named in the ``derived`` list of the hazard (``docs/decisions/0008-sensitive-points.md``).

**The two never meet.** ``near_places`` is in the outputs every client shares (the hazards, the history, the stream, the snapshot), so
it holds reference places only: the named active faults and the volcanoes that the hazard sources name. A watched point is personal
(a home, for example) and anyone who can write one could put any text in front of every client: it is listed by
``/v1/watched-points`` and ``/v1/watched-points/status`` and nowhere else.

This module imports nothing from ``schemas.py`` (``HazardAlert`` uses ``NearPlace``): the models that need the hazard types are
in ``watch.py``.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# What a place near a hazard is: an active fault (from the bundled GEM file) or a volcano (named by a hazard source).
NearPlaceKind = Literal["fault", "volcano"]

# What a point the user watches is: a fault, a volcano, a place of their own (``place``: a town, a site) or anything else (``custom``).
PlaceKind = Literal["fault", "volcano", "place", "custom"]

# The longest radius a watched point may have (km), and the default one.
MAX_WATCHED_RADIUS_KM = 500.0
DEFAULT_WATCHED_RADIUS_KM = 50.0


class NearPlace(BaseModel):
    """A named place close to an event, with the distance. Computed by Vigie; the radii are settings, not sourced facts."""

    name: str = Field(..., description="The place's name: the database's own for a fault, the source's for a volcano")
    kind: NearPlaceKind = Field(..., description="fault (GEM active fault trace) or volcano (named by a hazard source)")
    distance_km: float = Field(
        ...,
        ge=0.0,
        description="Great-circle distance in km from the event's position to the place (to the nearest point of a fault's trace), "
        "rounded to 0.1 km. For a fault it is the distance to the mapped surface trace: depth is not used, and it does not say "
        "that the event happened on that fault",
    )
    sources: list[str] = Field(
        default_factory=list,
        description="Where the place comes from: GEM for a fault, the agencies that report the volcano",
    )


class WatchedPoint(BaseModel):
    """A point the user watches (``GET /v1/watched-points``): events within ``radius_km`` of it are listed in its status."""

    id: str = Field(..., description="Identifier (generated when the point is added without one)")
    name: str = Field(..., min_length=1, max_length=64, description="Name of the point, shown by `/v1/watched-points` and its status only")
    kind: PlaceKind = Field(default="place", description="place, custom, volcano or fault: what the point is (shown after the name)")
    lat: float = Field(..., ge=-90.0, le=90.0, allow_inf_nan=False, description="WGS84 latitude")
    lon: float = Field(..., ge=-180.0, le=180.0, allow_inf_nan=False, description="WGS84 longitude")
    radius_km: float = Field(
        default=DEFAULT_WATCHED_RADIUS_KM,
        gt=0.0,
        le=MAX_WATCHED_RADIUS_KM,
        allow_inf_nan=False,
        description="Watch radius (km): an event closer than this is near the point",
    )


class WatchedPointUpsertRequest(BaseModel):
    """Request to add or update a watched point (``POST /v1/watched-points``)."""

    id: Optional[str] = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_-]{1,32}$",
        description="Identifier of the point to update (letters, digits, - and _); generated when absent",
    )
    name: str = Field(..., min_length=1, max_length=64, description="Name of the point")
    kind: PlaceKind = Field(default="place", description="place, custom, volcano or fault")
    lat: float = Field(..., ge=-90.0, le=90.0, allow_inf_nan=False, description="WGS84 latitude")
    lon: float = Field(..., ge=-180.0, le=180.0, allow_inf_nan=False, description="WGS84 longitude")
    radius_km: float = Field(
        default=DEFAULT_WATCHED_RADIUS_KM,
        gt=0.0,
        le=MAX_WATCHED_RADIUS_KM,
        allow_inf_nan=False,
        description=f"Watch radius (km), at most {MAX_WATCHED_RADIUS_KM:g}",
    )
