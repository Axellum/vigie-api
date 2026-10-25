"""``GET /v1/earthquakes``: the earthquakes near a position, from the feeds and the history, without asking a weather provider.

The earthquakes are real ones of the USGS feed (``tests/fixtures/places``): Panama M 7.7 and M 6.6, Tezpur (India) M 4.6, their
event times moved to "now" where a test needs it.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from app.attribution import credit_text
from app.collectors.base import CircuitState
from app.main import aggregator
from app.models.earthquakes import EarthquakesQuery
from app.models.schemas import HazardAlert
from app.services.earthquakes import FEEDS_MIN_MAGNITUDE, MAX_RADIUS_KM, build_earthquakes
from tests import places_samples as real
from tests.conftest import World

PANAMA = {"lat": 7.62, "lon": -80.80}  # 5 km from the epicentre of the M 7.7
HOUR = 3600


@pytest.fixture(autouse=True)
def _every_hazard_collector_starts_healthy() -> None:
    """The collectors are shared by every test: one that another test let fail would not count as having answered here."""
    for collector in aggregator.hazard_collectors():
        collector.consecutive_failures = 0
        collector.circuit_state = CircuitState.CLOSED


def quake(event_id: str, age_s: int, **update: object) -> HazardAlert:
    """A real earthquake of the recording, ``age_s`` seconds old."""
    return real.usgs_earthquake(event_id).model_copy(update={"timestamp_utc": int(time.time()) - age_s, **update})


def listed(world: World, monkeypatch: pytest.MonkeyPatch, usgs: list[HazardAlert], emsc: list[HazardAlert] | None = None) -> None:
    monkeypatch.setattr(world.aggregator.usgs, "fetch", AsyncMock(return_value=usgs))
    monkeypatch.setattr(world.aggregator.emsc, "fetch", AsyncMock(return_value=emsc or []))


def test_the_earthquakes_within_the_radius_strongest_first(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    listed(world, monkeypatch, [quake(real.PANAMA_M66, 2 * HOUR), quake(real.PANAMA_M77, HOUR), quake(real.TEZPUR, HOUR)])
    body = world.client.get("/v1/earthquakes", params={**PANAMA, "radius_km": 200}).json()
    assert [q["id"] for q in body["earthquakes"]] == ["usgs_us6000u18k", "usgs_us6000u19w"]  # Tezpur is in India
    strongest = body["earthquakes"][0]
    assert strongest["magnitude"] == 7.7 and strongest["magnitude_type"] == "mww"
    assert strongest["title"] == "M 7.7 - 12 km WSW of Pitaloza Arriba, Panama"  # the source's words
    assert strongest["distance_km"] < 10 and strongest["bearing_cardinal"] and strongest["listed_now"] is True
    assert strongest["sources"] == ["USGS"] and strongest["attribution"] == credit_text("usgs")
    assert body["count"] == 2 and body["feeds_min_magnitude"] == FEEDS_MIN_MAGNITUDE == 4.5
    assert body["query"] == {**PANAMA, "radius_km": 200.0, "min_magnitude": 4.5, "hours": 24.0}
    assert set(body["derived"]) == {"distance_km", "bearing_deg", "bearing_cardinal", "listed_now"}
    assert {s["provider_id"]: s["answered"] for s in body["sources"]} == {"usgs_eq": True, "emsc_seismic": True}
    assert credit_text("usgs") in body["attribution"] and credit_text("emsc") in body["attribution"]


def test_the_minimum_magnitude_the_window_and_the_limit(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    listed(world, monkeypatch, [quake(real.PANAMA_M77, 30 * HOUR), quake(real.PANAMA_M66, HOUR)])
    ids = lambda **params: [q["id"] for q in world.client.get("/v1/earthquakes", params={**PANAMA, **params}).json()["earthquakes"]]  # noqa: E731
    assert ids() == ["usgs_us6000u19w"]  # the M 7.7 is 30 hours old: out of the default day
    assert ids(hours=48) == ["usgs_us6000u18k", "usgs_us6000u19w"]
    assert ids(hours=48, min_magnitude=7) == ["usgs_us6000u18k"]
    assert ids(hours=48, limit=1) == ["usgs_us6000u18k"]
    body = world.client.get("/v1/earthquakes", params={**PANAMA, "hours": 48, "limit": 1}).json()
    assert body["count"] == 2 and len(body["earthquakes"]) == 1


def test_two_sources_reporting_one_earthquake_are_one(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    usgs = quake(real.PANAMA_M77, HOUR)
    emsc = usgs.model_copy(update={"id": "emsc_20261020_0001", "source": "EMSC", "sources": ["EMSC"], "value_primary": 7.6, "value_unit": "mw"})
    listed(world, monkeypatch, [usgs], [emsc])
    [one] = world.client.get("/v1/earthquakes", params=PANAMA).json()["earthquakes"]
    assert one["id"] == "usgs_us6000u18k" and one["sources"] == ["USGS", "EMSC"] and one["magnitude"] == 7.7
    assert credit_text("usgs") in one["attribution"] and credit_text("emsc") in one["attribution"]


def test_an_earthquake_the_feeds_no_longer_list_comes_from_the_history(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    old = quake(real.PANAMA_M77, 3 * 24 * HOUR)
    world.aggregator.storage.record_hazards([old])
    listed(world, monkeypatch, [quake(real.PANAMA_M66, HOUR)])
    body = world.client.get("/v1/earthquakes", params={**PANAMA, "hours": 96}).json()
    assert [(q["id"], q["listed_now"]) for q in body["earthquakes"]] == [("usgs_us6000u18k", False), ("usgs_us6000u19w", True)]
    assert [q["id"] for q in world.client.get("/v1/earthquakes", params=PANAMA).json()["earthquakes"]] == ["usgs_us6000u19w"]


def test_the_history_never_doubles_an_earthquake_a_feed_lists(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    usgs = quake(real.PANAMA_M77, HOUR)
    first_seen_by_emsc = usgs.model_copy(update={"id": "emsc_20261020_0001", "source": "EMSC", "sources": ["EMSC"]})
    world.aggregator.storage.record_hazards([first_seen_by_emsc])  # EMSC was read first, alone
    listed(world, monkeypatch, [usgs])
    assert [q["id"] for q in world.client.get("/v1/earthquakes", params=PANAMA).json()["earthquakes"]] == ["usgs_us6000u18k"]


def test_a_source_that_does_not_answer_is_said_and_not_credited(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(world.aggregator.usgs, "fetch", AsyncMock(side_effect=RuntimeError("down")))
    monkeypatch.setattr(world.aggregator.emsc, "fetch", AsyncMock(return_value=[]))
    body = world.client.get("/v1/earthquakes", params=PANAMA).json()
    assert body["earthquakes"] == [] and body["count"] == 0
    assert {s["provider_id"]: s["answered"] for s in body["sources"]} == {"usgs_eq": False, "emsc_seismic": True}
    assert body["attribution"] == credit_text("emsc")


def test_no_weather_provider_is_asked(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    listed(world, monkeypatch, [quake(real.PANAMA_M77, HOUR)])
    world.client.get("/v1/earthquakes", params=PANAMA)
    world.aggregator.open_meteo.fetch_point.assert_not_awaited()
    world.aggregator.met_norway.fetch_point.assert_not_awaited()
    world.aggregator.air_quality.fetch_air_quality.assert_not_awaited()


@pytest.mark.parametrize(
    "params",
    [{}, {"lat": 7.6}, {**PANAMA, "radius_km": MAX_RADIUS_KM + 1}, {**PANAMA, "radius_km": 0}, {**PANAMA, "hours": 169}, {**PANAMA, "limit": 0}],
)
def test_what_is_refused(world: World, params: dict[str, float]) -> None:
    assert world.client.get("/v1/earthquakes", params=params).status_code == 422


def test_a_simulated_earthquake_is_not_listed() -> None:
    simulated = real.usgs_earthquake(real.PANAMA_M77).model_copy(update={"id": "sim_quake", "timestamp_utc": 1_000})
    query = EarthquakesQuery(lat=7.62, lon=-80.8, radius_km=100, min_magnitude=4.5, hours=24)
    assert build_earthquakes(query, [simulated], [], [], 2_000, 20, None).earthquakes == []
