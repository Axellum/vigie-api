"""``POST`` and ``DELETE`` of ``/v1/watched-points`` are opt-in: they have no authentication and a watched point is personal.

The instance owner enables them with ``VIGIE_ENABLE_WATCHED_POINTS_WRITE=1``; without it they answer 403 and change nothing, and the
reads (``GET /v1/watched-points`` and its status) still work. Same pattern as ``POST /v1/simulate`` (``test_simulation_optin.py``).
"""

from __future__ import annotations

import pytest

from tests.conftest import World

BODY = {"name": "Etna", "kind": "volcano", "lat": 37.751, "lon": 14.994, "radius_km": 30.0}


def test_writing_is_off_by_default(world: World) -> None:
    response = world.client.post("/v1/watched-points", json=BODY)
    assert response.status_code == 403
    assert "VIGIE_ENABLE_WATCHED_POINTS_WRITE" in response.json()["detail"]
    deletion = world.client.delete("/v1/watched-points/p1")
    assert deletion.status_code == 403 and "VIGIE_ENABLE_WATCHED_POINTS_WRITE" in deletion.json()["detail"]


def test_a_refused_write_changes_nothing(world: World) -> None:
    world.client.post("/v1/watched-points", json=BODY)
    assert world.client.get("/v1/watched-points").json() == []
    assert world.aggregator.storage.list_watched_points() == []
    assert world.client.get("/v1/health").json()["watched_points_count"] == 0


def test_the_gate_answers_before_the_body_is_read(world: World) -> None:
    """A write that is off says 403, whatever it carries: it does not tell what a valid body would be, nor accept a bad one."""
    assert world.client.post("/v1/watched-points", json={"name": "x", "lat": 95, "lon": 0}).status_code == 403
    assert world.client.post("/v1/watched-points", json={}).status_code == 403


def test_the_reads_work_while_writing_is_off(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIGIE_ENABLE_WATCHED_POINTS_WRITE", "1")
    assert world.client.post("/v1/watched-points", json=BODY).status_code == 200
    monkeypatch.delenv("VIGIE_ENABLE_WATCHED_POINTS_WRITE")

    listed = world.client.get("/v1/watched-points")
    assert listed.status_code == 200 and [p["name"] for p in listed.json()] == ["Etna"]
    status = world.client.get("/v1/watched-points/status")
    assert status.status_code == 200 and [p["point"]["name"] for p in status.json()["points"]] == ["Etna"]
    assert world.client.delete("/v1/watched-points/p1").status_code == 403
    assert [p["id"] for p in world.client.get("/v1/watched-points").json()] == ["p1"]  # still there


def test_health_says_whether_writing_is_enabled(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    assert world.client.get("/v1/health").json()["watched_points_write_enabled"] is False
    monkeypatch.setenv("VIGIE_ENABLE_WATCHED_POINTS_WRITE", "1")
    assert world.client.get("/v1/health").json()["watched_points_write_enabled"] is True


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " 1 "])
def test_writing_works_when_enabled(world: World, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("VIGIE_ENABLE_WATCHED_POINTS_WRITE", value)
    created = world.client.post("/v1/watched-points", json=BODY)
    assert created.status_code == 200 and created.json()["id"] == "p1"
    assert world.client.delete("/v1/watched-points/p1").status_code == 200  # the same switch for the deletion
    assert world.client.delete("/v1/watched-points/p1").status_code == 404  # on: the route answers about the point, not the gate


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "2", "enabled"])
def test_writing_stays_off_for_any_other_value(world: World, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("VIGIE_ENABLE_WATCHED_POINTS_WRITE", value)
    assert world.client.post("/v1/watched-points", json=BODY).status_code == 403


def test_the_switch_is_read_at_each_request(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    assert world.client.post("/v1/watched-points", json=BODY).status_code == 403
    monkeypatch.setenv("VIGIE_ENABLE_WATCHED_POINTS_WRITE", "1")
    assert world.client.post("/v1/watched-points", json=BODY).status_code == 200
    monkeypatch.setenv("VIGIE_ENABLE_WATCHED_POINTS_WRITE", "0")
    assert world.client.post("/v1/watched-points", json=BODY).status_code == 403


def test_the_zones_are_not_behind_this_switch(world: World) -> None:
    """Only the new route is opt-in: ``/v1/zones`` keeps working as it did (its own exposure is documented, not changed here)."""
    assert world.client.post("/v1/zones", json={"name": "Somewhere", "lat": 1.0, "lon": 2.0, "utc_offset_s": 0, "alert_radius_km": 100.0, "is_marine": False}).status_code == 200
