"""The nearest wave buoy of each surf spot, and how far it is.

The stations are ``app/data/candhis-stations.json`` (CANDHIS, Cerema, Licence Ouverte 2.0; ``tools/build_buoys.py``). The
distance is calculated by Vigie (``derived``). A buoy is `comparable` with a spot only when it is real time **and** at most
``MAX_COMPARABLE_KM`` away: that radius is a setting of ours, not a fact. How exposed the spot is compared with the buoy (a
sheltered spot behind a headland) is **not** judged: only the distance is.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.attribution import credit_text
from app.models.buoys import Buoy, SpotBuoy, SpotBuoys
from app.services import surf
from app.services.geodesy import haversine_km

STATIONS_FILE = Path(__file__).resolve().parents[1] / "data" / "candhis-stations.json"
MAX_COMPARABLE_KM = 30.0
NOTE = (
    "The distance is calculated by Vigie. The nearest station of the CANDHIS file may no longer measure: `real_time` is true only for "
    "stations CANDHIS marked real time when the list was read, null for the others. `comparable` rests on the distance alone, not on "
    "how sheltered the spot is."
)


@lru_cache(maxsize=1)
def stations() -> list[Buoy]:
    data: dict[str, Any] = json.loads(STATIONS_FILE.read_text(encoding="utf-8"))
    return [Buoy(**s) for s in data["stations"]]


def _nearest(lat: float, lon: float, pool: list[Buoy]) -> tuple[Buoy, float] | None:
    best = min(((haversine_km(lat, lon, b.lat, b.lon), b) for b in pool), key=lambda t: t[0], default=None)
    return None if best is None else (best[1], best[0])


def spot_buoys() -> SpotBuoys:
    all_stations = stations()
    live = [b for b in all_stations if b.real_time]
    rows: list[SpotBuoy] = []
    for spot in surf.spots().spots:
        near = _nearest(spot.lat, spot.lon, all_stations)
        near_live = _nearest(spot.lat, spot.lon, live)
        assert near is not None
        rows.append(
            SpotBuoy(
                spot_id=spot.id,
                spot_name=spot.name,
                buoy=near[0],
                distance_km=near[1],
                real_time_buoy=near_live[0] if near_live else None,
                real_time_distance_km=near_live[1] if near_live else None,
                comparable=bool(near_live and near_live[1] <= MAX_COMPARABLE_KM),
            )
        )
    return SpotBuoys(
        count=len(rows),
        max_comparable_km=MAX_COMPARABLE_KM,
        within_30_km=sum(r.distance_km <= 30 for r in rows),
        within_50_km=sum(r.distance_km <= 50 for r in rows),
        real_time_within_30_km=sum((r.real_time_distance_km or 1e9) <= 30 for r in rows),
        real_time_within_50_km=sum((r.real_time_distance_km or 1e9) <= 50 for r in rows),
        spots=rows,
        note=NOTE,
        derived=["distance_km", "real_time_distance_km", "comparable", "max_comparable_km", "within_30_km", "within_50_km"],
        licence="Licence Ouverte 2.0",
        attribution=credit_text("candhis_stations"),
    )
