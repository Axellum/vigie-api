"""The nearest wave buoy of each surf spot."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.collectors.ndbc import NdbcCollector
from app.main import app
from app.services import buoys, surf
from tests.test_candhis import HOSSEGOR, candhis_host, collector_with
from tests.test_ndbc import BRETON_SPOT, ndbc_host
from tests.test_ndbc import NOW as NDBC_NOW


def test_every_spot_has_its_nearest_buoy_and_the_counts_add_up() -> None:
    answer = buoys.spot_buoys()
    assert answer.count == len(answer.spots) == surf.spots().count == 171
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
    assert body["count"] == 171 and "Candhis" in body["attribution"] and "distance_km" in body["derived"]


def test_spot_reading_combines_candhis_and_ndbc_cleanly() -> None:
    # Failing CANDHIS + working NDBC still yields the nearest NDBC buoy, flagged complete=False.
    failing_candhis = collector_with(candhis_host([], fail=(0, 1, 2)))
    working_ndbc = NdbcCollector(http_client=ndbc_host())
    reading = asyncio.run(buoys.spot_reading(failing_candhis, BRETON_SPOT, NDBC_NOW, ndbc=working_ndbc))
    assert reading.status == "ok" and reading.complete is False
    assert reading.buoy is not None and reading.buoy.source == "ndbc" and reading.buoy.id == "62107"

    # Working CANDHIS + failing NDBC yields the nearest CANDHIS buoy with complete=False and CANDHIS attribution.
    candhis_now = int(datetime(2026, 11, 9, 19, 8, tzinfo=timezone.utc).timestamp())
    working_candhis = collector_with(candhis_host([]))
    failing_ndbc = NdbcCollector(http_client=ndbc_host(fail_obs=True))
    c_reading = asyncio.run(buoys.spot_reading(working_candhis, HOSSEGOR, candhis_now, ndbc=failing_ndbc))
    assert c_reading.status == "ok" and c_reading.complete is False
    assert c_reading.buoy is not None and c_reading.buoy.source == "candhis" and c_reading.buoy.id == "06402"
    assert "Candhis" in c_reading.attribution

    # Both failing -> unavailable.
    unavail = asyncio.run(buoys.spot_reading(failing_candhis, BRETON_SPOT, NDBC_NOW, ndbc=failing_ndbc))
    assert unavail.status == "unavailable" and unavail.measure is None
