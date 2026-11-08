"""The tool that builds the catalogue of the places of the coast (``tools/build_places.py``), played on tiny synthetic inputs.

Nothing is downloaded: the sources are written here in the shape the real ones have (Sandre GeoJSON, the CSV of the bathing sites,
the answers of Overpass), and what the tool keeps, drops and merges is checked. The real file is checked in ``test_sea_places.py``.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import pytest

from tools import build_places as tool

COAST = [
    {"geometry": [{"lat": 45.0, "lon": 0.0}, {"lat": 45.0, "lon": 0.5}]}
]  # a coast running east-west for about 39 km


def node(element_id: int, lat: float, lon: float, **tags: str) -> dict[str, Any]:
    return {"type": "node", "id": element_id, "lat": lat, "lon": lon, "tags": tags}


def port(code: str, name: str, lat: float, lon: float, *uses: str) -> dict[str, Any]:
    props: dict[str, Any] = {"CdPort": code, "NomPort": name}
    for i in range(1, 7):
        props[f"MnActivitePortuaire_{i}"] = uses[i - 1] if i <= len(uses) else None
    return {"properties": props, "geometry": {"type": "Point", "coordinates": [lon, lat]}}


def bathing_row(
    index: int, name: str, lat: float, lon: float, water: str = "COTIERE", department: str = "33", quality: str = "Bon"
) -> dict[str, str]:
    return {
        "index": str(index), "code_geographique": "33009", "libelle_geographique": "Arcachon", "departement": department,
        "nom_site": name, "latitude": str(lat), "longitude": str(lon), "type_eau_norm": water, "qualite": quality,
    }  # fmt: skip


@pytest.fixture
def catalogue() -> list[dict[str, Any]]:
    ports = tool.read_ports(
        {
            "features": [
                port("1", "port du Chenal Vieux", 45.005, 0.10, "Plaisance", "Pêche"),
                port("2", "Port de commerce", 45.005, 0.20, "Commerce"),
                port("3", "port des collines", 45.3, 0.10, "Plaisance"),  # 33 km from the coast
                port("4", "port lointain", -21.3, 55.5, "Plaisance"),  # overseas
            ]
        }
    )
    bathing = tool.read_bathing(
        [
            bathing_row(1, "PLAGE DU MOULLEAU", 45.002, 0.30),
            bathing_row(2, "PLAN D'EAU", 45.4, 0.30, water="INTERIEURE"),
            bathing_row(3, "PLAGE TAHITI", -17.5, -149.5, department="987"),
            bathing_row(4, "ANSE SANS CLASSE", 45.001, 0.40, quality="Non classé"),
        ]
    )
    osm = {
        "marina": [
            node(
                10, 45.010, 0.105, leisure="marina", name="Marina du Chenal"
            ),  # 1.2 km from Sandre port 1: the same port
            node(11, 45.001, 0.35, leisure="marina", name="Marina des Pins"),
            node(12, 45.001, 0.45, leisure="marina"),  # no name
            node(13, 45.0012, 0.3505, leisure="marina", name="Marina des Pins bis"),  # 40 m from 11: one marina
            node(14, 45.001, 0.38, leisure="marina", name="Port de Valras-Plage"),  # a port named after a beach town
            node(15, 45.001, 0.39, leisure="marina", name="École de voile"),  # a school, a beach, a mooring: not ports
            node(16, 45.001, 0.40, leisure="marina", name="La plage des Pins"),
            node(17, 45.001, 0.41, leisure="marina", name="Mouillage de la Roche"),
        ],
        "slipway": [
            node(20, 45.001, 0.15, leisure="slipway"),
            node(
                21, 45.0011, 0.1501, leisure="slipway", name="Cale de la Pointe"
            ),  # 15 m from 20: one slipway, the named one
            node(22, 45.001, 0.25, leisure="slipway", access="private"),
            node(23, 45.2, 0.25, leisure="slipway"),  # 22 km from the coast
        ],
        "anchorage": [
            node(30, 44.99, 0.20, **{"seamark:type": "anchorage"}),
            node(31, 44.99, 0.30, **{"seamark:type": "mooring", "mooring": "private", "name": "Bouées privées"}),
            node(32, 44.99, 0.40, **{"seamark:type": "mooring", "mooring": "declaration", "name": "Zone de la Roche"}),
        ],
        "divespot": [
            node(40, 45.0, 0.05, **{"scuba_diving:divespot": "yes", "name": "L'Épave"}),
            node(41, 45.0, 0.06, **{"scuba_diving:divespot": "yes"}),
        ],
    }
    localities = tool.Grid()
    localities.add(45.01, 0.12, "Saint-Pierre")
    return tool.build(ports, bathing, osm, tool.coastline_grid(COAST), localities)


def by_ref(places: list[dict[str, Any]], source: str, ref: str) -> dict[str, Any] | None:
    return next((p for p in places if p["source"] == source and p["ref"] == ref), None)


def test_only_the_pleasure_ports_of_the_coast_of_metropolitan_france_are_kept(catalogue: list[dict[str, Any]]) -> None:
    ports = [p for p in catalogue if p["source"] == "sandre"]
    assert [p["name"] for p in ports] == [
        "Port du Chenal Vieux"
    ]  # the commerce port, the inland one and the overseas one are out
    assert ports[0]["uses"] == ["fishing", "leisure"] and ports[0]["near"] == "Saint-Pierre"


def test_an_openstreetmap_marina_next_to_a_sandre_port_is_that_port_and_an_unnamed_one_is_dropped(
    catalogue: list[dict[str, Any]],
) -> None:
    marinas = [p for p in catalogue if p["type"] == "port" and p["source"] == "osm"]
    # Not "Marina du Chenal" (the Sandre port), the unnamed one, "bis" (40 m away), the school, the beach and the mooring.
    assert [p["name"] for p in marinas] == ["Marina des Pins", "Port de Valras-Plage"]


def test_slipways_are_merged_and_the_closed_and_the_inland_ones_are_dropped(catalogue: list[dict[str, Any]]) -> None:
    slipways = [p for p in catalogue if p["type"] == "slipway"]
    assert [p["ref"] for p in slipways] == [
        "node/21"
    ]  # 20 and 21 are one (the named one wins); 22 is private; 23 is inland
    assert slipways[0]["name"] == "Cale de la Pointe"


def test_anchorages_keep_the_charted_areas_and_the_authorised_moorings_only(catalogue: list[dict[str, Any]]) -> None:
    anchorages = {p["ref"]: p for p in catalogue if p["type"] == "anchorage"}
    assert set(anchorages) == {"node/30", "node/32"}  # a private mooring is not listed
    assert (
        anchorages["node/30"]["name"] is None
    )  # an unnamed area is listed, and called after the nearest locality by its user


def test_dive_sites_must_have_a_name(catalogue: list[dict[str, Any]]) -> None:
    assert [p["name"] for p in catalogue if p["type"] == "dive"] == ["L'Épave"]


def test_bathing_sites_are_the_coastal_ones_of_metropolitan_france_with_a_tidy_name(
    catalogue: list[dict[str, Any]],
) -> None:
    sites = {p["name"]: p for p in catalogue if p["type"] == "bathing"}
    assert set(sites) == {"Plage du Moulleau", "Anse sans Classe"}
    assert sites["Plage du Moulleau"]["quality"] == "good" and sites["Plage du Moulleau"]["commune"] == "Arcachon"
    assert sites["Anse sans Classe"]["quality"] == "unclassified"


def test_ids_are_unique_stable_and_say_what_the_place_is(catalogue: list[dict[str, Any]]) -> None:
    ids = [p["id"] for p in catalogue]
    assert len(set(ids)) == len(ids)
    port_id = by_ref(catalogue, "sandre", "1")["id"]  # type: ignore[index]
    assert port_id.startswith("port-port-du-chenal-vieux-") and len(port_id.rsplit("-", 1)[1]) == 4
    unnamed = by_ref(catalogue, "osm", "node/30")["id"]  # type: ignore[index]
    assert unnamed.startswith("anchorage-")  # no locality within 6 km: the id still says the kind


def test_the_catalogue_is_ordered_by_kind_then_name(catalogue: list[dict[str, Any]]) -> None:
    kinds = [p["type"] for p in catalogue]
    assert kinds == sorted(kinds, key=tool.TYPES.index)


def test_the_file_is_the_same_bytes_every_time_and_holds_no_unknown_key(
    catalogue: list[dict[str, Any]], tmp_path: Path
) -> None:
    first, second = tmp_path / "a.json.gz", tmp_path / "b" / "c.json.gz"
    tool.write(first, catalogue, "2026-11-08T11:26:36Z")
    tool.write(second, catalogue, "2026-11-08T11:26:36Z")
    assert first.read_bytes() == second.read_bytes()
    data = json.loads(gzip.decompress(first.read_bytes()))
    assert (
        data["count"] == len(catalogue)
        and data["counts"]["dive"] == 1
        and data["osm_data_utc"] == "2026-11-08T11:26:36Z"
    )
    assert {k for p in data["places"] for k in p} <= {
        "id",
        "type",
        "name",
        "near",
        "lat",
        "lon",
        "commune",
        "uses",
        "quality",
        "source",
        "ref",
    }


def test_two_places_cannot_share_an_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool, "place_id", lambda item: "dive-a-0000")
    osm = {"marina": [], "slipway": [], "anchorage": [], "divespot": [
        node(40, 45.0, 0.05, **{"scuba_diving:divespot": "yes", "name": "A"}),
        node(41, 45.0, 0.20, **{"scuba_diving:divespot": "yes", "name": "B"}),
    ]}  # fmt: skip
    with pytest.raises(ValueError, match="two places with the id dive-a-0000"):
        tool.build([], [], osm, tool.coastline_grid(COAST), tool.Grid())


@pytest.mark.parametrize(
    ("raw", "tidy"),
    [
        ("PLAGE DU MOULLEAU", "Plage du Moulleau"),
        ("L'ÎLE D'YEU - PLAGE DES CONCHES", "L'Île d'Yeu - Plage des Conches"),
        ("Port de Bouc", "Port de Bouc"),  # already written with lower-case letters: left alone
        ("ST-MALO", "St-Malo"),
    ],
)
def test_tidy_name(raw: str, tidy: str) -> None:
    assert tool.tidy_name(raw) == tidy


def test_the_coast_is_found_within_2_km_and_not_beyond() -> None:
    coast = tool.coastline_grid(COAST)
    assert coast.nearest((45.015, 0.25), tool.COAST_KM) is not None  # 1.7 km north of the line
    assert coast.nearest((45.025, 0.25), tool.COAST_KM) is None  # 2.8 km
    assert coast.nearest((45.0, 0.2501), tool.COAST_KM) is not None  # on a long segment, between its two vertices
