"""The impact score and the families of phenomena of ``GET /v1/events`` (ADR 0012). Nothing reaches the network."""

from __future__ import annotations

from typing import Any, get_args
from unittest.mock import AsyncMock

import pytest

from app.models.events import FamilyId
from app.models.schemas import HazardAlert, HazardCategory, SeverityLevel
from app.models.vigilance import VigilancePhenomenon
from app.services.events import (
    FAMILIES,
    WORLD_MIN_IMPACT,
    group_hazards,
    group_warnings,
    impact_of_hazards,
    impact_of_warnings,
    sorted_events,
)
from tests import samples as hazard_samples
from tests import vigilance_samples as samples
from tests.conftest import World
from tests.test_events import recorded  # noqa: F401 - the fixture of the route tests

RED, ORANGE, YELLOW, GREEN = SeverityLevel.RED, SeverityLevel.ORANGE, SeverityLevel.YELLOW, SeverityLevel.GREEN


def hazard(category: HazardCategory, severity: SeverityLevel, hazard_id: str = "h1", **update: Any) -> HazardAlert:
    return hazard_samples.earthquake(hazard_id, severity, tsunami=False).model_copy(update={"category": category, **update})


# ---------------------------------------------------------------------------------------------- families


def test_every_phenomenon_and_every_category_has_one_family() -> None:
    phenomena = [p for family in FAMILIES for p in family.phenomena]
    categories = [c for family in FAMILIES for c in family.hazard_categories]
    assert sorted(phenomena) == sorted(p.value for p in VigilancePhenomenon)
    assert sorted(categories) == sorted(c.value for c in HazardCategory)
    assert [family.id for family in FAMILIES] == list(get_args(FamilyId))
    assert len(FAMILIES) == 15  # the fourteen of the plan, and "other"


def test_the_family_of_a_warning_follows_its_phenomenon() -> None:
    def family_of(**kwargs: Any) -> str:
        [event] = group_warnings([samples.warning("A", **kwargs)], lambda v: None, lambda c: c)
        return event.family

    assert family_of(phenomenon=VigilancePhenomenon.RAIN) == family_of(phenomenon=VigilancePhenomenon.FLOOD) == "rain-flood"
    assert family_of(phenomenon=VigilancePhenomenon.AVALANCHE) == "snow-ice"
    assert family_of(phenomenon=None) == "other"
    assert family_of(source="tsunami", country="US", phenomenon=VigilancePhenomenon.COASTAL) == "tsunami"


def test_the_families_route(world: World) -> None:
    answer = world.client.get("/v1/families").json()
    assert [family["id"] for family in answer["families"]] == list(get_args(FamilyId))
    assert answer["world_min_impact"] == WORLD_MIN_IMPACT == 60
    air = next(family for family in answer["families"] if family["id"] == "air")
    assert air["phenomena"] == [] and air["hazard_categories"] == [] and "empty" in air["note"]


# ---------------------------------------------------------------------------------------------- the score


def test_the_table_of_the_official_warnings() -> None:
    assert [impact_of_warnings(level, 1)[0] for level in (RED, ORANGE, YELLOW, GREEN, None)] == [90, 60, 20, 0, 10]
    assert impact_of_warnings(YELLOW, 2) == (21, ["official yellow warning", "2 areas"])
    assert impact_of_warnings(ORANGE, 100_000)[0] == 69 < impact_of_warnings(RED, 1)[0]  # breadth never lifts a level over the next
    assert impact_of_warnings(GREEN, 500)[0] == 0  # green stays at nothing
    assert [impact_of_warnings(level, 1, tsunami=True)[0] for level in (RED, ORANGE, YELLOW, GREEN)] == [100, 80, 60, 30]


@pytest.mark.parametrize(
    ("category", "severity", "expected"),
    [
        (HazardCategory.EARTHQUAKE, RED, 85),
        (HazardCategory.EARTHQUAKE, ORANGE, 60),  # magnitude 6 or more, a PAGER orange, or the tsunami flag
        (HazardCategory.EARTHQUAKE, YELLOW, 30),
        (HazardCategory.EARTHQUAKE, GREEN, 5),
        (HazardCategory.HURRICANE, GREEN, 60),  # every tropical cyclone counts
        (HazardCategory.VOLCANO, ORANGE, 60),
        (HazardCategory.VOLCANO, YELLOW, 25),
        (HazardCategory.SOLAR_STORM, RED, 70),
        (HazardCategory.TSUNAMI, YELLOW, 60),
        (HazardCategory.FLOOD, ORANGE, 60),  # an NWS flood: the official table
    ],
)
def test_the_table_of_the_hazards(category: HazardCategory, severity: SeverityLevel, expected: int) -> None:
    assert impact_of_hazards([hazard(category, severity)])[0] == expected


def test_a_gdacs_alert_has_its_own_table() -> None:
    flood = hazard(HazardCategory.FLOOD, ORANGE, "gdacs_fl_1", source="GDACS", sources=["GDACS"])
    assert impact_of_hazards([flood]) == (65, ["GDACS flood alert rated orange by the API"])
    assert impact_of_hazards([flood.model_copy(update={"severity": GREEN})])[0] == 10


def test_an_earthquake_says_its_magnitude_and_its_tsunami_flag() -> None:
    quake = hazard_samples.earthquake("q", ORANGE, value=6.4, tsunami=True)
    assert impact_of_hazards([quake]) == (60, ["earthquake of magnitude 6.4 (orange)", "tsunami flag of the source"])


def test_a_sequence_adds_one_per_earthquake_up_to_five() -> None:
    quakes = [hazard_samples.earthquake(f"q{i}", YELLOW, value=5.2, tsunami=False, timestamp=1_000 + i) for i in range(9)]
    assert impact_of_hazards(quakes[:3])[0] == 32
    assert impact_of_hazards(quakes)[0] == 35


def test_a_tsunami_alone_counts() -> None:
    centre = hazard(HazardCategory.TSUNAMI, YELLOW, "tsunami_1").with_event_key("vtec:PAAQ.TS.0024")
    [alone], _ = group_hazards([centre], lambda h: None)
    assert (alone.kind, alone.family, alone.impact) == ("tsunami", "tsunami", 60)


def test_the_strongest_impact_first() -> None:
    cyclone = hazard(HazardCategory.HURRICANE, GREEN, "storm")
    events, _ = group_hazards([cyclone, hazard_samples.earthquake("q", GREEN, value=4.6, tsunami=False)], lambda h: None)
    warnings = group_warnings(
        [samples.warning("A", level=YELLOW), samples.warning("B", level=ORANGE, identifier="b")], lambda v: None, lambda c: c
    )
    ordered = sorted_events(events + warnings)
    assert [(e.kind, e.impact) for e in ordered] == [("warnings", 60), ("hazard", 60), ("warnings", 20), ("earthquakes", 5)]


# ---------------------------------------------------------------------------------------------- the route


def test_min_impact_keeps_what_counts(recorded: World, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    cyclone = hazard(HazardCategory.HURRICANE, GREEN, "gdacs_tc_1", lat=15.0, lon=130.0, title="Tropical Cyclone X", sources=["GDACS"], source="GDACS")
    small = hazard_samples.earthquake("usgs_small", GREEN, value=4.6, lat=58.0, lon=-32.0, tsunami=False)
    monkeypatch.setattr(recorded.aggregator.gdacs, "fetch", AsyncMock(return_value=[cyclone]))
    monkeypatch.setattr(recorded.aggregator.usgs, "fetch", AsyncMock(return_value=[small]))
    every = recorded.client.get("/v1/events").json()
    counting = recorded.client.get("/v1/events", params={"min_impact": WORLD_MIN_IMPACT}).json()
    assert counting["query"]["min_impact"] == 60
    assert counting["events"] and all(e["impact"] >= 60 for e in counting["events"])
    assert {e["id"] for e in every["events"] if e["impact"] >= 60} == {e["id"] for e in counting["events"]}
    titles = [e["title"] for e in counting["events"]]
    assert "Tropical Cyclone X" in titles and not any("4.6" in t for t in titles)
    assert any("4.6" in e["title"] for e in every["events"])
    assert all(e["level"] in ("ORANGE", "RED") for e in counting["events"] if e["kind"] == "warnings")
    assert counting["counts"]["events"] == len(counting["events"])


def test_families_keep_those_families(recorded: World) -> None:  # noqa: F811
    answer = recorded.client.get("/v1/events", params={"families": "rain-flood,wind"}).json()
    assert answer["events"] and {e["family"] for e in answer["events"]} <= {"rain-flood", "wind"}
    assert set(answer["counts"]["by_family"]) <= {"rain-flood", "wind"}
    refused = recorded.client.get("/v1/events", params={"families": "wind,meteors"})
    assert refused.status_code == 422 and "meteors" in refused.json()["detail"]


@pytest.mark.parametrize("value", ["-1", "101", "high"])
def test_a_bad_min_impact_is_refused(world: World, value: str) -> None:
    assert world.client.get("/v1/events", params={"min_impact": value}).status_code == 422
