"""The fused hazards are worked out once per reading of the sources, not once per request.

Fusing, annotating and recording every hazard of the world was about 80 % of the time of a ``GET /v1/point`` (profiled on
2026-11-17). ``collect_hazards_with_status`` now serves its last result again while every input is the same object as last time:
the lists the sources hand back (their caches), the simulated alerts, the watched zones, the recent fires, the place settings and
the storage. These tests fix when the result is reused and when it is worked out again.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.collectors.base import CircuitState
from app.models.schemas import HazardAlert, HazardCategory, SeverityLevel
from app.services import aggregator as aggregator_module
from app.services.aggregator import HAZARD_RECORD_REFRESH_S, AggregatorService
from tests.helpers import SimClock, make_service


def quake(hazard_id: str, lat: float = 8.15, lon: float = 126.30) -> HazardAlert:
    return HazardAlert(
        id=hazard_id,
        category=HazardCategory.EARTHQUAKE,
        severity=SeverityLevel.ORANGE,
        title=hazard_id,
        source="USGS",
        sources=["USGS"],
        timestamp_utc=1792334000,
        lat=lat,
        lon=lon,
        value_primary=6.1,
        value_unit="Mw",
    )


class FakeSource:
    """A hazard source that hands back the very same list until it is given a new one, as a cached collector does."""

    def __init__(self, provider_id: str, hazards: list[HazardAlert]) -> None:
        self.provider_id = provider_id
        self.hazards = hazards
        self.consecutive_failures = 0
        self.circuit_state = CircuitState.CLOSED

    async def fetch(self) -> list[HazardAlert]:
        return self.hazards


class Counted:
    """Counts the fusions and the writes to the history."""

    def __init__(self, service: AggregatorService, monkeypatch: pytest.MonkeyPatch) -> None:
        self.fusions = 0
        self.records = 0
        real_fuse = aggregator_module.deduplicate_hazards
        real_record = service.storage.record_hazards

        def fuse(hazards: Any) -> Any:
            self.fusions += 1
            return real_fuse(hazards)

        def record(hazards: Any) -> int:
            self.records += 1
            return real_record(hazards)

        monkeypatch.setattr(aggregator_module, "deduplicate_hazards", fuse)
        monkeypatch.setattr(service.storage, "record_hazards", record)


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sim_clock: SimClock) -> tuple[AggregatorService, FakeSource, Counted]:
    service = make_service(tmp_path / "vigie.db")
    source = FakeSource("usgs", [quake("us1"), quake("us2", lat=35.0, lon=139.0)])
    monkeypatch.setattr(service, "hazard_collectors", lambda: [source])
    return service, source, Counted(service, monkeypatch)


@pytest.mark.asyncio
async def test_the_same_source_lists_are_fused_once(world: tuple[AggregatorService, FakeSource, Counted]) -> None:
    service, _, counted = world
    first, ok, total = await service.collect_hazards_with_status()
    again = [await service.collect_hazards_with_status() for _ in range(5)]
    assert counted.fusions == 1 and counted.records == 1
    assert (ok, total) == (1, 1)
    assert all([h.id for h in hazards] == [h.id for h in first] for hazards, _, _ in again)
    assert {h.id for h in first} == {"us1", "us2"}


@pytest.mark.asyncio
async def test_a_caller_cannot_change_what_the_next_one_gets(world: tuple[AggregatorService, FakeSource, Counted]) -> None:
    service, _, _ = world
    first, _, _ = await service.collect_hazards_with_status()
    first.clear()
    second, _, _ = await service.collect_hazards_with_status()
    assert {h.id for h in second} == {"us1", "us2"}


@pytest.mark.asyncio
async def test_a_new_reading_of_a_source_is_fused_again(world: tuple[AggregatorService, FakeSource, Counted]) -> None:
    service, source, counted = world
    await service.collect_hazards_with_status()
    source.hazards = [quake("us3")]  # the cache of the source was refreshed: a new list
    hazards, _, _ = await service.collect_hazards_with_status()
    assert counted.fusions == 2 and counted.records == 2
    assert [h.id for h in hazards] == ["us3"]


@pytest.mark.asyncio
async def test_new_watched_zones_are_taken_into_account(world: tuple[AggregatorService, FakeSource, Counted]) -> None:
    service, _, counted = world
    await service.collect_hazards_with_status()
    service.monitored_zones = [{"id": "z1", "name": "Zone", "lat": 8.0, "lon": 126.0, "utc_offset_s": 0}]
    hazards, _, _ = await service.collect_hazards_with_status()
    assert counted.fusions == 2
    assert all(h.nearest_zone_id == "z1" for h in hazards)


@pytest.mark.asyncio
async def test_the_simulated_alerts_are_taken_into_account(world: tuple[AggregatorService, FakeSource, Counted]) -> None:
    service, _, counted = world
    await service.collect_hazards_with_status()
    service.simulation_active = True
    service.simulated_hazards = [quake("sim_1", lat=0.0, lon=0.0)]
    hazards, _, _ = await service.collect_hazards_with_status()
    assert counted.fusions == 2 and "sim_1" in {h.id for h in hazards}
    service.simulation_active = False
    hazards, _, _ = await service.collect_hazards_with_status()
    assert counted.fusions == 3 and "sim_1" not in {h.id for h in hazards}


@pytest.mark.asyncio
async def test_the_history_still_sees_the_hazards_now_and_then(
    world: tuple[AggregatorService, FakeSource, Counted], sim_clock: SimClock
) -> None:
    service, _, counted = world
    await service.collect_hazards_with_status()
    sim_clock.advance(HAZARD_RECORD_REFRESH_S - 1)
    await service.collect_hazards_with_status()
    assert counted.records == 1
    sim_clock.advance(1)
    await service.collect_hazards_with_status()
    assert counted.fusions == 1 and counted.records == 2  # "last seen active" refreshed without working anything out again


@pytest.mark.asyncio
async def test_a_source_that_fails_does_not_stop_the_reuse(world: tuple[AggregatorService, FakeSource, Counted], monkeypatch: pytest.MonkeyPatch) -> None:
    service, source, counted = world

    class Down(FakeSource):
        async def fetch(self) -> list[HazardAlert]:
            raise RuntimeError("down, nothing cached")

    down = Down("emsc", [])
    monkeypatch.setattr(service, "hazard_collectors", lambda: [source, down])
    _, ok, total = await service.collect_hazards_with_status()
    _, ok_again, _ = await service.collect_hazards_with_status()
    assert counted.fusions == 1
    assert (ok, total) == (1, 2) and ok_again == 1
