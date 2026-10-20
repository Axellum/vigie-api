"""Real answers, recorded on 2026-10-20 (``tests/fixtures/places/README.md``), for the tests of the places near a hazard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.collectors.disasters import GDACSCollector
from app.collectors.flood_air import USGSVolcanoCollector
from app.collectors.seismic import USGSCollector
from app.models.schemas import HazardAlert

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "places"

# The real earthquakes of the recording (ids of the USGS feed).
PANAMA_M77 = "us6000u18k"  # M 7.7, 12 km WSW of Pitaloza Arriba, Panama, depth 12.6 km: on the Azuero Fault Zone
PANAMA_M66 = "us6000u19w"  # M 6.6, 12 km W of Rio Grande, Panama, depth 24 km
TEZPUR = "us6000tzth"  # M 4.6, 29 km SSE of Tezpur, India, depth 10 km
COLOMBIA_DEEP = "us6000u25i"  # M 4.8, 5 km NW of Cepita, Colombia, depth 162 km (a mapped trace lies 4 km from its epicentre)
NICARAGUA_DEEP = "us6000u0uq"  # M 4.6, 18 km NNE of Posoltega, Nicaragua, depth 206 km
EASTER_ISLAND = "us6000u1gs"  # M 5.3, southeast of Easter Island, open ocean
FIJI_DATE_LINE = "us6000tzsc"  # M 5.0, south of the Fiji Islands, at 179.863 east


def read_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def usgs_earthquake(event_id: str) -> HazardAlert:
    """One of the recorded earthquakes, through the real parser of the USGS collector."""
    feed = read_json("usgs_earthquakes_2026-10-20.geojson")
    features = [f for f in feed["features"] if f["id"] == event_id]
    assert features, event_id
    [alert] = USGSCollector.parse_geojson({"features": features})
    return alert


def gem_excerpt() -> list[dict[str, Any]]:
    """The traces of the GEM file (unmodified: properties and every vertex) within 30 km of the shallow earthquakes above."""
    return read_json("gem_raw_excerpt_2026-10-20.geojson")["features"]


def usgs_volcanoes() -> list[HazardAlert]:
    """The volcanoes in alert of the USGS (Great Sitkin, Shishaldin, Kilauea, Ahyi Seamount), through the real parser."""
    return USGSVolcanoCollector.parse_volcanoes(read_json("usgs_volcano_elevated_2026-10-20.json"))


def gdacs_volcanoes() -> list[HazardAlert]:
    """The two GDACS eruptions of the recording (Kanlaon, Taal), through the real parser.

    GDACS had stopped following both (``iscurrent`` ``"false"``) when they were recorded: the flag is set back to ``"true"``, as the
    GDACS fixtures of the storms do, so that the parser keeps them.
    """
    payload = read_json("gdacs_volcano_2026-10-20.json")
    for feature in payload["features"]:
        feature["properties"]["iscurrent"] = "true"
    return GDACSCollector.parse_geojson(payload)


def volcano(alerts: list[HazardAlert], name: str) -> HazardAlert:
    [found] = [a for a in alerts if a.place_name == name]
    return found
