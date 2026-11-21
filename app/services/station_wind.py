"""The wind measured now by every Météo-France ground station of a box (``GET /v1/stations/wind``), for a map.

The observations are the 6-minute package ``DPPaquetObs`` the coastal stations already read (``app/collectors/meteofrance_obs.py``):
one request every 6 minutes serves every box, so this route asks the provider nothing more. The package gives each station's
position; its name and altitude come from ``app/data/meteofrance-station-names.json`` (``tools/build_station_names.py``). Only
stations with a wind reading at most ``MAX_AGE_S`` old are listed, strongest mean wind first, so that a map which cannot show them
all keeps the strongest.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from app.attribution import credit_text
from app.collectors.base import CollectorUnavailableError
from app.collectors.meteofrance_obs import MeteoFranceObsCollector
from app.models.stations import StationWindMap, WindStation
from app.services.fires import SCALE, Box
from app.services.stations import MAX_AGE_S, MAX_AHEAD_S

NAMES_FILE = Path(__file__).resolve().parents[1] / "data" / "meteofrance-station-names.json"
DEFAULT_LIMIT = 3000
NOTE = (
    "Every Météo-France ground station of the box whose latest 6-minute wind observation is at most `max_age_hours` old, strongest "
    "mean wind first. The wind is measured at 10 m where the station stands: a summit (`alt_m`) or a sheltered valley can differ "
    "much from the places around. `age_minutes` is calculated by Vigie. Not for safety."
)


@lru_cache(maxsize=1)
def names() -> dict[str, tuple[Optional[str], Optional[int]]]:
    data: dict[str, Any] = json.loads(NAMES_FILE.read_text(encoding="utf-8"))
    return {sid: (row[0] or None, row[1]) for sid, row in data["stations"].items()}


def _inside(box: Box, lat: float, lon: float) -> bool:
    if not box.south <= lat <= box.north:
        return False
    value = round(lon * SCALE)
    return any(low <= value <= high for low, high in box.lon_ranges)


async def wind_map(collector: Optional[MeteoFranceObsCollector], box: Box, now_unix: float, limit: int = DEFAULT_LIMIT) -> StationWindMap:
    """The latest wind of the stations of ``box``, strongest first, at most ``limit`` of them."""

    def answer(status: str, **more: Any) -> StationWindMap:
        fields: dict[str, Any] = {"count": 0, "stations": [], **more}
        return StationWindMap(
            status=status,  # type: ignore[arg-type]
            bbox=box.as_list(),
            max_age_hours=MAX_AGE_S / 3600,
            note=NOTE,
            derived=["age_minutes", "measured_utc", "truncated"],
            licence="Licence Ouverte 2.0",
            attribution=credit_text("meteofrance_obs", "meteofrance_station_names"),
            **fields,
        )

    if collector is None:
        return answer("not_configured")
    try:
        latest = await collector.fetch_latest()
    except CollectorUnavailableError:
        return answer("unavailable")
    known = names()
    rows: list[WindStation] = []
    for sid, reading in latest.readings.items():
        if reading.lat is None or reading.lon is None or not _inside(box, reading.lat, reading.lon):
            continue
        if not -MAX_AHEAD_S <= now_unix - reading.measured_utc <= MAX_AGE_S:
            continue
        name, alt = known.get(sid, (None, None))
        rows.append(
            WindStation(
                id=sid,
                name=name,
                alt_m=alt,
                lat=reading.lat,
                lon=reading.lon,
                measured_utc=reading.measured_utc,
                age_minutes=max(0, int((now_unix - reading.measured_utc) // 60)),
                wind_speed_kmh=reading.wind_speed_kmh,
                wind_gust_kmh=reading.wind_gust_kmh,
                wind_from_deg=reading.wind_from_deg,
            )
        )
    rows.sort(key=lambda r: (-r.wind_speed_kmh, r.id))
    kept = rows[:limit]
    return answer(
        "ok",
        count=len(kept),
        truncated=len(rows) > limit,
        measured_utc=max((r.measured_utc for r in kept), default=None),
        stale=latest.stale,
        stations=kept,
    )
