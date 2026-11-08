"""The catalogue of the places of the coast (``app/data/sea-places.json.gz``, ``app/services/sea_places.py``, ``GET /v1/sea/places``).

The file is the real one: its shape is checked here, and a few places everybody knows are looked for, so a rebuild that loses a
region or a kind of place fails. How the file is built is in ``test_build_places.py``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.attribution import CREDITS
from app.main import app
from app.services import sea_places

ARCACHON = (44.66, -1.17)
KINDS = {"port", "slipway", "anchorage", "dive", "bathing"}


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


# ------------------------------------------------------------------------------------------------------------------- the file


def test_the_file_holds_every_kind_of_place_and_the_counts_add_up() -> None:
    data = sea_places.place_file()
    places = data["places"]
    assert data["count"] == len(places) and sum(data["counts"].values()) == len(places)
    assert {p["type"] for p in places} == KINDS
    assert all(data["counts"][kind] >= 10 for kind in KINDS)  # a rebuild that loses a kind must not pass unnoticed
    assert data["counts"]["bathing"] > 1000 and data["counts"]["port"] > 300 and data["counts"]["slipway"] > 500
    assert data["osm_data_utc"].startswith("2026-")


def test_every_place_is_unique_in_metropolitan_france_and_comes_from_the_source_of_its_kind() -> None:
    places = sea_places.place_file()["places"]
    assert len({p["id"] for p in places}) == len(places)
    for p in places:
        assert 41.0 <= p["lat"] <= 51.3 and -5.6 <= p["lon"] <= 9.8
        assert p["id"].startswith(p["type"] + "-")
        assert p["source"] in {"port": {"sandre", "osm"}, "bathing": {"qsb"}}.get(p["type"], {"osm"})
        if p["type"] == "bathing":
            assert (
                p["quality"] in {"excellent", "good", "sufficient", "poor", "unclassified"}
                and p["commune"]
                and p["name"]
            )
        else:
            assert "quality" not in p and "commune" not in p
        if p["type"] == "port":
            assert p["name"]  # a port is listed by its name
            assert ("uses" in p) == (p["source"] == "sandre")
        else:
            assert "uses" not in p


def test_the_names_have_no_stray_spaces_and_the_bathing_sites_are_not_shouted() -> None:
    places = sea_places.place_file()["places"]
    for p in places:
        for text in (p["name"], p["near"], p.get("commune")):
            if text:
                assert text == text.strip() and "  " not in text
    # The source writes the names of the bathing sites in capitals and they are tidied; an acronym ("A.C.M") stays as it is.
    shouted = [
        p["name"] for p in places if p["type"] == "bathing" and len(p["name"]) >= 6 and p["name"] == p["name"].upper()
    ]
    assert len(shouted) < 20, shouted


@pytest.mark.parametrize(
    ("word", "kind"),
    [
        ("arcachon", "port"),
        ("arcachon", "bathing"),
        ("saint malo", "port"),
        ("la rochelle", "port"),
        ("porquerolles", "port"),
        ("calvi", "port"),
    ],
)
def test_places_everybody_knows_are_there(word: str, kind: str) -> None:
    assert sea_places.search([kind], None, 25, None, word, 5, 0).count >= 1  # type: ignore[list-item]


# ------------------------------------------------------------------------------------------------------------------ the search


def test_around_a_point_the_nearest_come_first_with_their_distance() -> None:
    answer = sea_places.search(None, ARCACHON, 10, None, None, 50, 0)
    distances = [p.distance_km for p in answer.places]
    assert answer.count > 20 and None not in distances and distances == sorted(distances)  # type: ignore[type-var]
    assert all(d <= 10 for d in distances if d is not None)  # type: ignore[misc]
    assert any(p.type == "port" and "Arcachon" in (p.name or "") for p in answer.places)


def test_without_a_point_there_is_no_distance_and_the_order_is_by_kind() -> None:
    answer = sea_places.search(None, None, 25, None, None, 500, 0)
    assert all(p.distance_km is None for p in answer.places)
    kinds = [p.type for p in answer.places]
    assert kinds == sorted(kinds, key=("port", "slipway", "anchorage", "dive", "bathing").index)


def test_the_kind_the_box_and_the_word_narrow_the_answer() -> None:
    ports = sea_places.search(["port"], None, 25, None, None, 1, 0)
    assert ports.by_type["port"] == ports.count and ports.by_type["bathing"] == 0 and ports.returned == 1
    boxed = sea_places.search(None, None, 25, (-1.3, 44.5, -1.0, 44.8), None, 500, 0)
    assert boxed.count > 0 and all(-1.3 <= p.lon <= -1.0 and 44.5 <= p.lat <= 44.8 for p in boxed.places)
    word = sea_places.search(None, None, 25, None, "ARCACHON", 500, 0)
    assert word.count > 0 and all(
        "arcachon" in (" ".join(filter(None, [p.name, p.near, p.commune]))).lower().replace("-", " ")
        for p in word.places
    )


def test_a_search_ignores_accents_capitals_hyphens_and_apostrophes() -> None:
    a = sea_places.search(None, None, 25, None, "Port-Joinville", 500, 0)
    b = sea_places.search(None, None, 25, None, "port joinville", 500, 0)
    assert a.count == b.count >= 1
    assert (
        sea_places.search(None, None, 25, None, "ILE D'YEU", 500, 0).count
        == sea_places.search(None, None, 25, None, "île d yeu", 500, 0).count
        > 0
    )


def test_pages_follow_one_another_without_a_gap() -> None:
    first = sea_places.search(["dive"], None, 25, None, None, 5, 0)
    second = sea_places.search(["dive"], None, 25, None, None, 5, 5)
    everything = sea_places.search(["dive"], None, 25, None, None, 500, 0)
    assert [p.id for p in first.places + second.places] == [p.id for p in everything.places[:10]]
    assert first.count == second.count == everything.count and first.limit == 5 and second.offset == 5


def test_the_credit_names_the_sources_of_the_places_returned() -> None:
    bathing = sea_places.search(["bathing"], None, 25, None, None, 3, 0)
    assert CREDITS["osm_places"].text in bathing.attribution and CREDITS["qsb_bathing"].text in bathing.attribution
    assert CREDITS["sandre_ports"].text not in bathing.attribution
    sandre = [p for p in sea_places.search(["port"], None, 25, None, None, 500, 0).places if p.source == "sandre"][:3]
    assert CREDITS["sandre_ports"].text in sea_places.find(sandre[0].id).attribution
    assert "OpenStreetMap" in sea_places.search(["slipway"], None, 25, None, None, 3, 0).attribution


def test_a_place_is_found_by_its_id() -> None:
    wanted = sea_places.search(["port"], None, 25, None, "arcachon", 1, 0).places[0]
    assert sea_places.find(wanted.id).place == wanted
    with pytest.raises(sea_places.UnknownPlace):
        sea_places.find("port-nowhere-0000")


@pytest.mark.parametrize("text", ["", "1,2,3", "a,b,c,d", "5,44,4,45", "-1,45,0,44", "-200,44,0,45"])
def test_a_bbox_that_is_not_a_box_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match="bbox"):
        sea_places.parse_bbox(text)


# ------------------------------------------------------------------------------------------------------------------ the routes


def test_the_route_answers_with_the_licence_the_note_and_a_day_of_cache(client: TestClient) -> None:
    response = client.get(
        "/v1/sea/places", params={"near_lat": ARCACHON[0], "near_lon": ARCACHON[1], "radius_km": 5, "limit": 5}
    )
    assert response.status_code == 200 and response.headers["cache-control"] == "public, max-age=86400"
    body = response.json()
    assert body["returned"] == 5 and body["count"] >= 5 and body["limit"] == 5 and body["offset"] == 0
    assert body["places"][0]["distance_km"] <= body["places"][1]["distance_km"]
    assert "ODbL" in body["licence"] and "not a forecast" in body["note"] and "distance_km" in body["derived"]
    assert body["built_utc"].startswith("2026-") and set(body["by_type"]) == KINDS


def test_the_kind_can_be_repeated(client: TestClient) -> None:
    body = client.get("/v1/sea/places", params=[("type", "anchorage"), ("type", "dive"), ("limit", "500")]).json()
    assert {p["type"] for p in body["places"]} == {"anchorage", "dive"} and body["by_type"]["bathing"] == 0


@pytest.mark.parametrize(
    "params",
    [
        {"near_lat": 44.6},
        {"near_lon": -1.1},
        {"near_lat": 44.6, "near_lon": -1.1, "radius_km": 201},
        {"near_lat": 44.6, "near_lon": -1.1, "radius_km": 0},
        {"near_lat": 91, "near_lon": 0},
        {"bbox": "1,2,3"},
        {"bbox": "5,44,4,45"},
        {"type": "beach"},
        {"limit": 0},
        {"limit": 501},
        {"offset": -1},
        {"q": "x" * 81},
    ],
)
def test_a_wrong_filter_is_a_422(client: TestClient, params: dict[str, str | int | float]) -> None:
    assert client.get("/v1/sea/places", params=params).status_code == 422


def test_one_place_by_id_and_an_unknown_id(client: TestClient) -> None:
    listed = client.get("/v1/sea/places", params={"type": "dive", "limit": 1}).json()["places"][0]
    response = client.get(f"/v1/sea/places/{listed['id']}")
    assert response.status_code == 200 and response.json()["place"] == listed
    assert "attribution" in response.json() and response.headers["cache-control"] == "public, max-age=86400"
    assert client.get("/v1/sea/places/nowhere").status_code == 404


def test_the_routes_are_in_the_api_description(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/v1/sea/places" in paths and "/v1/sea/places/{place_id}" in paths
    assert paths["/v1/sea/places"]["get"]["tags"] == ["Sea"]
