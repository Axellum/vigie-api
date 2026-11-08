"""The nearest wave buoy of each surf spot."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services import buoys


def test_every_spot_has_its_nearest_buoy_and_the_counts_add_up() -> None:
    answer = buoys.spot_buoys()
    assert answer.count == len(answer.spots) == 51
    for row in answer.spots:
        assert 0 <= row.distance_km < 500
        if row.real_time_buoy is not None:
            assert row.real_time_distance_km is not None and row.real_time_distance_km >= row.distance_km
        assert row.comparable == (row.real_time_distance_km is not None and row.real_time_distance_km <= buoys.MAX_COMPARABLE_KM)
    assert answer.within_30_km == sum(r.distance_km <= 30 for r in answer.spots)
    assert answer.real_time_within_30_km <= answer.within_30_km <= answer.within_50_km


def test_a_station_is_never_called_real_time_without_a_reading() -> None:
    known = [b for b in buoys.stations() if b.real_time]
    assert known and all(b.real_time is True for b in known)
    assert any(b.real_time is None for b in buoys.stations())  # unknown is not false


def test_the_route() -> None:
    body = TestClient(app).get("/v1/surf/buoys").json()
    assert body["count"] == 51 and "Candhis" in body["attribution"] and "distance_km" in body["derived"]
