"""``/v1/watched-points`` (the points the user watches) and ``near_places`` in the routes that carry hazards."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.attribution import CREDITS
from app.models.places import MAX_WATCHED_RADIUS_KM, WatchedPointUpsertRequest
from app.services.places import PlaceSettings, default_faults
from app.services.storage_wal import SQLiteWALStorage, WatchedPointsLimitError
from tests import places_samples as real
from tests.conftest import World


def add(world: World, **fields: Any) -> dict[str, Any]:
    body = {"name": "Etna", "kind": "volcano", "lat": 37.751, "lon": 14.994, "radius_km": 30.0, **fields}
    response = world.client.post("/v1/watched-points", json=body)
    assert response.status_code == 200, response.text
    return response.json()


# ============================================================================
# 1. The routes
# ============================================================================


def test_a_new_instance_watches_nothing(world: World) -> None:
    assert world.client.get("/v1/watched-points").json() == []


def test_a_point_is_added_listed_updated_and_deleted(world: World) -> None:
    created = add(world)
    assert created == {"id": "p1", "name": "Etna", "kind": "volcano", "lat": 37.751, "lon": 14.994, "radius_km": 30.0}
    second = add(world, name="Stromboli", lat=38.789, lon=15.213, radius_km=20.0)
    assert second["id"] == "p2"
    assert [p["name"] for p in world.client.get("/v1/watched-points").json()] == ["Etna", "Stromboli"]

    updated = add(world, id="p1", name="Mount Etna", radius_km=45.54)
    assert (updated["id"], updated["name"], updated["radius_km"]) == ("p1", "Mount Etna", 45.5)
    assert len(world.client.get("/v1/watched-points").json()) == 2  # an update is not a new point
    assert world.client.get("/v1/health").json()["watched_points_count"] == 2

    assert world.client.delete("/v1/watched-points/p1").json() == {"status": "deleted", "point_id": "p1"}
    assert [p["id"] for p in world.client.get("/v1/watched-points").json()] == ["p2"]
    assert world.client.delete("/v1/watched-points/p1").status_code == 404


def test_the_defaults_are_a_place_of_50_km(world: World) -> None:
    created = world.client.post("/v1/watched-points", json={"name": "Home", "lat": 10.0, "lon": 20.0}).json()
    assert (created["kind"], created["radius_km"]) == ("place", 50.0)


def test_a_new_id_never_takes_one_that_exists(world: World) -> None:
    add(world, id="p2", name="Taken")
    assert add(world, name="Next")["id"] == "p3"  # the new id is "p" and the count of points plus one, and then the next free one
    ids = [p["id"] for p in world.client.get("/v1/watched-points").json()]
    assert len(set(ids)) == len(ids)


@pytest.mark.parametrize(
    "fields",
    [
        {"name": ""},
        {"name": "x" * 65},
        {"lat": 90.5},
        {"lat": -91.0},
        {"lon": 180.5},
        {"lon": -181.0},
        {"radius_km": 0.0},
        {"radius_km": -5.0},
        {"radius_km": MAX_WATCHED_RADIUS_KM + 1},
        {"kind": "home"},
        {"id": "bad id!"},
        {"id": "x" * 33},
        {"lat": "north"},
    ],
)
def test_nonsense_is_refused_with_422(world: World, fields: dict[str, Any]) -> None:
    body = {"name": "Point", "kind": "place", "lat": 10.0, "lon": 20.0, "radius_km": 50.0, **fields}
    assert world.client.post("/v1/watched-points", json=body).status_code == 422
    assert world.client.get("/v1/watched-points").json() == []


@pytest.mark.parametrize("raw", ['{"name": "P", "lat": NaN, "lon": 20}', '{"name": "P", "lat": 10, "lon": Infinity}', '{"name": "P", "lat": 10, "lon": 20, "radius_km": NaN}'])
def test_not_a_number_is_refused(world: World, raw: str) -> None:
    response = world.client.post("/v1/watched-points", content=raw, headers={"Content-Type": "application/json"})
    assert response.status_code == 422


def test_a_refusal_for_a_number_json_cannot_carry_is_a_422_not_a_500(world: World) -> None:
    """Python's JSON parser accepts NaN; the error that quotes it had to be encoded, and could not (every route with a number)."""
    response = world.client.post(
        "/v1/zones", content='{"name": "Z", "lat": NaN, "lon": 2, "utc_offset_s": 0}', headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422 and response.json()["detail"][0]["input"] == "nan"


def test_a_name_is_trimmed_and_the_position_rounded(world: World) -> None:
    created = world.client.post(
        "/v1/watched-points", json={"name": "  Etna  ", "lat": 37.7510049, "lon": 14.9940049, "radius_km": 30.04}
    ).json()
    assert (created["name"], created["lat"], created["lon"], created["radius_km"]) == ("Etna", 37.751, 14.994, 30.0)


def test_an_instance_keeps_a_limited_number_of_points(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(world.aggregator.places, "settings", PlaceSettings(watched_points_max=3))
    for n in range(3):
        add(world, name=f"Point {n}")
    refused = world.client.post("/v1/watched-points", json={"name": "One too many", "lat": 1.0, "lon": 2.0})
    assert refused.status_code == 409 and "3 watched points at most" in refused.json()["detail"]
    assert add(world, id="p1", name="Renamed")["name"] == "Renamed"  # updating one that exists is not adding
    assert len(world.client.get("/v1/watched-points").json()) == 3
    world.client.delete("/v1/watched-points/p2")
    assert add(world, name="Now there is room")["name"] == "Now there is room"


def test_the_points_survive_a_restart(tmp_path: Path) -> None:
    database = tmp_path / "restart.db"
    first = SQLiteWALStorage(db_path=database)
    first.upsert_watched_point(WatchedPointUpsertRequest(name="Etna", kind="volcano", lat=37.751, lon=14.994, radius_km=30.0), 50)
    reopened = SQLiteWALStorage(db_path=database)
    [point] = reopened.list_watched_points()
    assert (point.id, point.name, point.kind, point.radius_km) == ("p1", "Etna", "volcano", 30.0)


def test_the_storage_refuses_a_new_point_over_the_limit(tmp_path: Path) -> None:
    storage = SQLiteWALStorage(db_path=tmp_path / "limit.db")
    request = WatchedPointUpsertRequest(name="P", lat=1.0, lon=2.0)
    storage.upsert_watched_point(request, 1)
    with pytest.raises(WatchedPointsLimitError):
        storage.upsert_watched_point(request, 1)


def test_a_database_made_before_this_feature_gets_the_new_tables_and_keeps_its_data(tmp_path: Path) -> None:
    """An instance upgraded in place: the three old tables as they were, none of the new ones."""
    import sqlite3

    database = tmp_path / "before.db"
    with sqlite3.connect(database) as old:
        old.executescript(
            """
            CREATE TABLE monitored_zones (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL,
                utc_offset_s INTEGER NOT NULL DEFAULT 0, alert_radius_km REAL NOT NULL DEFAULT 500.0,
                is_marine INTEGER NOT NULL DEFAULT 0, created_utc INTEGER NOT NULL
            );
            CREATE TABLE hazards_history (
                id TEXT PRIMARY KEY, category TEXT NOT NULL, severity TEXT NOT NULL, severity_rank INTEGER NOT NULL,
                timestamp_utc INTEGER NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL, value_primary REAL,
                source TEXT NOT NULL, payload_json TEXT NOT NULL, updated_utc INTEGER NOT NULL
            );
            CREATE TABLE snapshot_cache (
                device_type TEXT PRIMARY KEY, server_utc INTEGER NOT NULL, payload_json TEXT NOT NULL
            );
            INSERT INTO monitored_zones VALUES ('z1', 'Somewhere', 10.0, 20.0, 3600, 300.0, 0, 1700000000);
            """
        )
    storage = SQLiteWALStorage(db_path=database)
    assert [zone.id for zone in storage.list_zones()] == ["z1"]
    assert storage.list_watched_points() == []
    assert storage.load_source_runs() == {}
    point = storage.upsert_watched_point(WatchedPointUpsertRequest(name="Etna", kind="volcano", lat=37.751, lon=14.994), 50)
    assert point.id == "p1"
    assert [p.name for p in SQLiteWALStorage(db_path=database).list_watched_points()] == ["Etna"]


# ============================================================================
# 2. near_places in the routes that carry hazards
# ============================================================================


@pytest.fixture()
def panama(world: World, monkeypatch: pytest.MonkeyPatch) -> World:
    """The application with the real M 7.7 earthquake of Panama as the only hazard."""
    quake = real.usgs_earthquake(real.PANAMA_M77)
    monkeypatch.setattr(world.aggregator.usgs, "fetch", AsyncMock(return_value=[quake]))
    return world


def test_the_geojson_carries_the_places_and_says_they_are_derived(panama: World) -> None:
    add(panama, name="Camp", kind="place", lat=7.62, lon=-80.80, radius_km=50.0)
    collection = panama.client.get("/v1/geojson/hazards").json()
    [feature] = collection["features"]
    properties = feature["properties"]
    assert [(p["name"], p["kind"]) for p in properties["near_places"]] == [("Azuero Fault Zone", "fault"), ("Camp", "place")]
    assert properties["near_places"][0]["distance_km"] == pytest.approx(4.3, abs=0.1) and properties["near_places"][0]["sources"] == ["GEM"]
    assert properties["derived"] == ["near_places"]
    assert CREDITS["gem_faults"].text in properties["attribution"] and CREDITS["usgs"].text in properties["attribution"]
    assert CREDITS["gem_faults"].text in collection["metadata"]["attribution"]
    assert properties["severity"] == "RED"  # M 7.7: the magnitude decides it, as it does without any place


def test_the_severity_is_the_same_with_and_without_a_place(panama: World) -> None:
    without = panama.client.get("/v1/geojson/hazards").json()["features"][0]["properties"]["severity"]
    add(panama, name="Camp", kind="place", lat=7.62, lon=-80.80, radius_km=50.0)
    assert panama.client.get("/v1/geojson/hazards").json()["features"][0]["properties"]["severity"] == without


def test_the_json_routes_carry_the_places_too(panama: World) -> None:
    add(panama, name="Camp", kind="place", lat=7.62, lon=-80.80, radius_km=50.0)
    snapshot = panama.client.get("/v1/snapshot", params={"compact": "false"}).json()
    assert [p["name"] for p in snapshot["hazards"][0]["near_places"]] == ["Azuero Fault Zone", "Camp"]
    assert snapshot["hazards"][0]["derived"] == ["near_places"]

    point = panama.client.get("/v1/point", params={"lat": 7.6, "lon": -80.8, "radius_km": 200, "compact": "false"}).json()
    assert [p["name"] for p in point["nearby_hazards"][0]["near_places"]] == ["Azuero Fault Zone", "Camp"]

    history = panama.client.get("/v1/history").json()  # the collection recorded it: the history keeps what was said
    assert [p["name"] for p in history["items"][0]["near_places"]] == ["Azuero Fault Zone", "Camp"]


def test_the_compact_profile_keeps_to_its_16_kib_and_carries_no_places(panama: World) -> None:
    """The compact snapshot and point (the default of both) are held to 16 KiB with the credit of every item: a place and the
    credit of its source on each of 15 hazards would not fit, so they are not in it. The field is null there: not worked out."""
    add(panama, name="Camp", kind="place", lat=7.62, lon=-80.80, radius_km=50.0)
    for hazard in (
        panama.client.get("/v1/snapshot").json()["hazards"][0],
        panama.client.get("/v1/point", params={"lat": 7.6, "lon": -80.8, "radius_km": 200}).json()["nearby_hazards"][0],
    ):
        assert hazard["near_places"] is None and hazard["derived"] == []
        assert CREDITS["gem_faults"].text not in hazard["attribution"]  # the credit goes with the places it is for
    assert panama.client.get("/v1/geojson/hazards").json()["features"][0]["properties"]["near_places"]  # the other routes have them


def test_a_hazard_far_from_everything_says_nothing_is_near(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(world.aggregator.usgs, "fetch", AsyncMock(return_value=[real.usgs_earthquake(real.EASTER_ISLAND)]))
    [feature] = world.client.get("/v1/geojson/hazards").json()["features"]
    assert feature["properties"]["near_places"] == [] and feature["properties"]["derived"] == ["near_places"]
    assert "Active faults" not in (feature["properties"]["attribution"] or "")


def test_when_the_faults_cannot_be_read_the_hazard_still_comes_with_its_places_unknown(panama: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(panama.aggregator.places, "_faults", lambda: None)
    [feature] = panama.client.get("/v1/geojson/hazards").json()["features"]
    assert feature["properties"]["near_places"] is None and feature["properties"]["derived"] == []
    assert panama.client.get("/v1/health").json()["active_faults_loaded"] is None


def test_the_places_failing_never_stops_the_hazards(panama: World, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_: Any, **__: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(panama.aggregator.places, "annotate", broken)
    [feature] = panama.client.get("/v1/geojson/hazards").json()["features"]
    assert feature["properties"]["title"].startswith("M 7.7") and feature["properties"]["near_places"] is None


def test_the_health_says_how_many_faults_are_loaded(world: World) -> None:
    health = world.client.get("/v1/health").json()
    faults = default_faults()
    assert faults is not None and health["active_faults_loaded"] == len(faults) > 5000 and health["watched_points_count"] == 0


def test_a_volcano_in_alert_is_told_to_the_earthquakes_near_it(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    sitkin = real.volcano(real.usgs_volcanoes(), "Great Sitkin")
    quake = real.usgs_earthquake(real.PANAMA_M77).model_copy(
        update={"id": "usgs_near_sitkin", "lat": sitkin.lat + 0.09, "lon": sitkin.lon, "depth_km": 6.0}
    )  # a stated position, 10 km north of the real volcano: no recorded earthquake happened there
    monkeypatch.setattr(world.aggregator.usgs, "fetch", AsyncMock(return_value=[quake]))
    monkeypatch.setattr(world.aggregator.volcano, "fetch", AsyncMock(return_value=real.usgs_volcanoes()))
    features = {f["id"]: f for f in world.client.get("/v1/geojson/hazards").json()["features"]}
    near = features["usgs_near_sitkin"]["properties"]["near_places"]
    assert [(p["name"], p["kind"], p["sources"]) for p in near] == [("Great Sitkin", "volcano", ["USGS_VOLCANO"])]
    assert near[0]["distance_km"] == pytest.approx(10.0, abs=0.1)
    assert all(f["properties"]["category"] != "VOLCANO" or f["properties"]["near_places"] is not None for f in features.values())
