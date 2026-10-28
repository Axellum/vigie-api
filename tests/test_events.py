"""``GET /v1/events``: the official warnings grouped, the earthquakes of one sequence, the other hazards (ADR 0012).

The rules are pinned on synthetic warnings and hazards; the route plays the recorded feeds of 2026-10-20. Nothing reaches the network.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Optional
from unittest.mock import AsyncMock

import pytest

from app.collectors.base import CollectorUnavailableError, Fetched
from app.collectors.meteoalarm import CapDocument
from app.models.schemas import HazardAlert, HazardCategory, SeverityLevel
from app.models.vigilance import (
    Vigilance,
    VigilanceArea,
    VigilanceGeocode,
    VigilanceParameter,
    VigilancePhenomenon,
    VigilanceText,
)
from app.services.events import (
    QUAKE_LINK_KM,
    QUAKE_LINK_S,
    WARNING_LINK_KM,
    count,
    group_hazards,
    group_warnings,
    sorted_events,
)
from tests import samples as hazard_samples
from tests import vigilance_samples as samples
from tests.conftest import World
from tests.helpers import SimClock
from tests.vigilance_upstream import Upstream, make_service

Box = tuple[float, float, float, float]
KM_PER_DEG = 111.2  # one degree of latitude, near enough for these tests


def box_at(lat: float, lon: float) -> Box:
    return (lon - 0.1, lat - 0.1, lon + 0.1, lat + 0.1)


def at(*places: tuple[str, Optional[Box]]) -> Any:
    """``box_of`` for synthetic warnings: their area code gives their box (or none)."""
    table = dict(places)
    return lambda v: table.get(v.area.geocodes[0].value)


def name(country: str) -> str:
    return {"FR": "France", "CH": "Switzerland", "US": "United States of America"}.get(country, country)


def warn(area: str, **kwargs: Any) -> Vigilance:
    kwargs.setdefault("identifier", f"msg.{area}")
    return samples.warning(area, **kwargs)


# ---------------------------------------------------------------------------------------------- warnings


def test_one_phenomenon_one_level_one_period_close_areas_is_one_event() -> None:
    warnings = [warn("A", onset=1_000, expires=5_000), warn("B", onset=2_000, expires=6_000), warn("C", onset=4_000, expires=9_000)]
    events = group_warnings(warnings, at(("A", box_at(45, 5)), ("B", box_at(45.5, 5.5)), ("C", box_at(46, 6))), name)
    assert len(events) == 1
    event = events[0]
    assert event.kind == "warnings" and event.level == SeverityLevel.YELLOW and event.phenomenon == VigilancePhenomenon.FLOOD
    assert event.areas_count == 3 and event.area_names == ["A", "B", "C"]
    assert event.title == "Floods · France · 3 areas"
    assert (event.start_utc, event.end_utc) == (1_000, 9_000)
    assert event.bbox == [4.9, 44.9, 6.1, 46.1]
    assert event.alerts == warnings  # untouched, in the order of their start
    assert event.alert_ids == [w.id for w in warnings]
    assert set(event.derived) == {"level", "title", "areas_count", "start_utc", "end_utc", "bbox"}


def test_one_area_is_named_in_the_title() -> None:
    [event] = group_warnings([warn("Valais")], at(("Valais", box_at(46.2, 7.6))), name)
    assert event.title == "Floods · Valais"


def test_another_level_another_phenomenon_another_country_is_another_event() -> None:
    warnings = [
        warn("A"),
        warn("B", level=SeverityLevel.ORANGE),
        warn("C", phenomenon=VigilancePhenomenon.WIND),
        warn("D", country="CH"),
    ]
    boxes = at(*((code, box_at(46, 6)) for code in "ABCD"))
    assert len(group_warnings(warnings, boxes, name)) == 4


def test_two_other_warnings_with_different_words_stay_apart() -> None:
    first = warn("A", phenomenon=VigilancePhenomenon.OTHER).model_copy(update={"phenomenon_original": "Ground frost"})
    second = warn("B", phenomenon=VigilancePhenomenon.OTHER).model_copy(update={"phenomenon_original": "Dust"})
    third = warn("C", phenomenon=VigilancePhenomenon.OTHER).model_copy(update={"phenomenon_original": "ground  FROST"})
    events = group_warnings([first, second, third], at(*((c, box_at(46, 6)) for c in "ABC")), name)
    assert sorted(e.areas_count or 0 for e in events) == [1, 2]
    frost = next(e for e in events if e.areas_count == 2)
    assert frost.title == "Ground frost · France · 2 areas"  # the source's word, as given, when the phenomenon is "other"
    assert frost.phenomenon_words == ["Ground frost", "ground  FROST"]


def test_periods_that_do_not_overlap_are_two_events() -> None:
    warnings = [warn("A", onset=1_000, expires=2_000), warn("B", onset=3_000, expires=4_000)]
    assert len(group_warnings(warnings, at(("A", box_at(46, 6)), ("B", box_at(46, 6))), name)) == 2


def test_a_warning_with_no_end_overlaps_everything_after_it() -> None:
    warnings = [warn("A", onset=1_000, expires=None), warn("B", onset=90_000, expires=95_000)]
    [event] = group_warnings(warnings, at(("A", box_at(46, 6)), ("B", box_at(46, 6))), name)
    assert event.end_utc is None


def test_far_areas_are_two_events_and_a_chain_of_close_ones_is_one() -> None:
    step = (WARNING_LINK_KM - 20) / KM_PER_DEG  # each area just within the link of the previous one
    chain = [warn(code) for code in "ABCD"]
    boxes = at(*((code, box_at(40 + i * step, 0)) for i, code in enumerate("ABCD")))
    assert len(group_warnings(chain, boxes, name)) == 1  # A and D are 800 km apart, linked through B and C
    far = [warn("A"), warn("B")]
    assert len(group_warnings(far, at(("A", box_at(40, 0)), ("B", box_at(40 + (WARNING_LINK_KM + 20) / KM_PER_DEG, 0))), name)) == 2


def test_areas_without_a_shape_are_an_event_of_their_own() -> None:
    warnings = [warn("A"), warn("B"), warn("C")]
    events = group_warnings(warnings, at(("A", box_at(46, 6)), ("B", None), ("C", None)), name)
    unplaced = next(e for e in events if e.bbox is None)
    assert unplaced.areas_count == 2 and [e.areas_count for e in events if e.bbox is not None] == [1]


def test_unplaced_warnings_of_the_united_states_are_split_by_state() -> None:
    def nws(code: str) -> Vigilance:
        return samples.warning(code, identifier=f"nws.{code}", country="US", source="noaa_nws", scheme="UGC")

    warnings = [nws("CAZ006"), nws("CAZ007"), nws("TXZ100")]
    events = group_warnings(warnings, lambda v: None, name)
    assert sorted(e.areas_count or 0 for e in events) == [1, 2]


def test_the_highest_level_comes_first_then_the_broadest() -> None:
    warnings = [warn("A"), warn("B"), warn("C", level=SeverityLevel.RED), warn("D", level=None)]
    events = sorted_events(group_warnings(warnings, at(*((c, box_at(46, 6)) for c in "ABCD")), name))
    assert [(e.level, e.areas_count) for e in events] == [(SeverityLevel.RED, 1), (SeverityLevel.YELLOW, 2), (None, 1)]
    assert count(events).by_level == {"RED": 1, "YELLOW": 1, "unknown": 1}


# ---------------------------------------------------------------------------------------------- hazards


def quake(hazard_id: str, lat: float, lon: float, timestamp: int, magnitude: float, severity: SeverityLevel = SeverityLevel.GREEN) -> HazardAlert:
    return hazard_samples.earthquake(hazard_id, severity, value=magnitude, lat=lat, lon=lon, timestamp=timestamp, tsunami=False)


def test_the_earthquakes_of_one_sequence_are_one_event() -> None:
    near = (QUAKE_LINK_KM - 20) / KM_PER_DEG
    quakes = [
        quake("q1", 63.0, -24.0, 10_000, 4.6),
        quake("q2", 63.0 + near, -24.0, 12_000, 5.5, SeverityLevel.YELLOW),
        quake("q3", 63.0 + 2 * near, -24.0, 14_000, 4.8),  # 160 km from q1, linked through q2
        quake("q4", 63.0, -24.0, 14_000 + QUAKE_LINK_S + 1, 4.7),  # the same place, more than six hours after the last one
        quake("q5", 10.0, 120.0, 12_000, 4.5),  # elsewhere
    ]
    events, joined = group_hazards(quakes, lambda h: None)
    assert joined == set()
    sizes = sorted(len(e.hazards) for e in events)
    assert sizes == [1, 1, 3]
    sequence = next(e for e in events if len(e.hazards) == 3)
    assert sequence.kind == "earthquakes" and sequence.level == SeverityLevel.YELLOW
    assert sequence.title == "3 earthquakes · strongest: M 5.5 - Crete, Greece"  # the source's own title of the strongest
    assert (sequence.start_utc, sequence.end_utc) == (10_000, 14_000)
    assert [h.id for h in sequence.hazards] == ["q1", "q2", "q3"]


def test_a_tsunami_is_one_event_with_the_messages_that_carry_its_code() -> None:
    hazard = HazardAlert(
        id="tsunami_paaq_0024",
        category=HazardCategory.TSUNAMI,
        severity=SeverityLevel.ORANGE,
        title="Tsunami Advisory - Gulf of Alaska",
        source="NTWC",
        sources=["NTWC"],
        timestamp_utc=1_000,
        lat=57.0,
        lon=-150.0,
        tsunami_warning=True,
    ).with_event_key("vtec:PAAQ.TS.0024")
    vtec = VigilanceParameter(name="VTEC", value="/O.NEW.PAAQ.TS.W.0024.121028T0307Z-000000T0000Z/")
    message = Vigilance(
        id="tsunami:msg:0",
        source="tsunami",
        country="US",
        attribution="NOAA",
        identifier="msg",
        level=SeverityLevel.RED,
        texts=[VigilanceText(language="en-US", parameters=[vtec])],
        area=VigilanceArea(geocodes=[VigilanceGeocode(scheme="UGC", value="AKZ145")]),
    )
    other = message.model_copy(update={"id": "tsunami:other:0", "texts": []})
    events, joined = group_hazards([hazard], lambda h: "US", [message, other])
    assert joined == {"tsunami:msg:0"}
    [event] = events
    assert event.kind == "tsunami" and event.alerts == [message] and event.hazards == [hazard]
    assert event.level == SeverityLevel.RED  # the official message says more than the centre's hazard


def test_any_other_hazard_is_an_event_of_its_own() -> None:
    storm = hazard_samples.earthquake("gdacs_tc_1", SeverityLevel.RED).model_copy(update={"category": HazardCategory.HURRICANE, "title": "Tropical Cyclone X"})
    events, _ = group_hazards([storm], lambda h: None)
    assert [(e.kind, e.category, e.title) for e in events] == [("hazard", HazardCategory.HURRICANE, "Tropical Cyclone X")]


# ---------------------------------------------------------------------------------------------- the route


@pytest.fixture()
def recorded(world: World, sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch) -> World:
    """The application with the recorded feeds of 2026-10-20 in place of the providers."""
    sim_clock.advance(samples.RECORDED_AT - SimClock.EPOCH)

    async def feed(country: str, **_: Any) -> Fetched[Any]:
        if country in samples.FEED_FILES:
            return Fetched(samples.feed(country))
        return Fetched(samples.feed("DK").__class__(country=country, updated_utc=None, vigilances=()))

    async def document(url: str) -> Fetched[Any]:
        if url.endswith("b77d3bc0-76a2-4886-8da8-bac8d99f57fc"):
            return Fetched(samples.cap())
        return Fetched(CapDocument(available=False))

    monkeypatch.setattr(world.aggregator.meteoalarm, "fetch_country", AsyncMock(side_effect=feed))
    monkeypatch.setattr(world.aggregator.meteoalarm_cap, "fetch_document", AsyncMock(side_effect=document))
    monkeypatch.setattr(world.aggregator.dwd, "fetch_status", AsyncMock(return_value=Fetched(samples.dwd_status())))
    monkeypatch.setattr(world.aggregator.eccc, "fetch_alerts", AsyncMock(return_value=Fetched(samples.eccc_alerts())))
    monkeypatch.setattr(world.aggregator.metservice, "fetch_feed", AsyncMock(return_value=Fetched(samples.empty_metservice())))
    monkeypatch.setattr(world.aggregator.vigicrues, "fetch_flow", AsyncMock(return_value=Fetched(samples.all_green_flow())))
    monkeypatch.setattr(world.aggregator.swic, "fetch_source", AsyncMock(side_effect=CollectorUnavailableError("not in this test")))
    return world


def test_a_country_s_events_hold_every_warning_of_the_country_once(recorded: World) -> None:
    served = recorded.client.get("/v1/vigilances", params={"country": "FR"}).json()
    answer = recorded.client.get("/v1/events", params={"country": "FR"}).json()
    held = [alert for event in answer["events"] for alert in event["alerts"]]
    assert Counter(a["id"] for a in held) == Counter(v["id"] for v in served["vigilances"])  # each warning once
    by_id = {v["id"]: v for v in served["vigilances"]}
    assert all(alert == by_id[alert["id"]] for alert in held)  # exactly as /v1/vigilances serves it
    assert len(answer["events"]) < len(served["vigilances"])
    assert answer["counts"]["events"] == len(answer["events"])
    assert answer["coverage"] == served["coverage"]
    assert answer["disclaimer"] and answer["attribution"] and "MeteoAlarm" in answer["attribution"]
    levels = [e["level"] for e in answer["events"]]
    rank = {"RED": 3, "ORANGE": 2, "YELLOW": 1, "GREEN": 0, None: -1}
    assert [rank[x] for x in levels] == sorted((rank[x] for x in levels), reverse=True)


def test_compact_leaves_the_warnings_out_and_keeps_their_ids(recorded: World) -> None:
    full = recorded.client.get("/v1/events", params={"country": "FR"}).json()
    compact = recorded.client.get("/v1/events", params={"country": "FR", "compact": "true"}).json()
    assert [e["alert_ids"] for e in compact["events"]] == [e["alert_ids"] for e in full["events"]]
    assert all(e["alerts"] == [] and e["hazards"] == [] for e in compact["events"])


def test_the_world_reads_every_served_country_once(recorded: World) -> None:
    answer = recorded.client.get("/v1/events").json()
    countries = {e["country"] for e in answer["events"] if e["kind"] == "warnings"}
    assert {"FR", "CH", "IT", "DE", "CA"} <= countries  # MeteoAlarm, the DWD and the ECCC in one answer
    asked = Counter(call.args[0] for call in recorded.aggregator.meteoalarm.fetch_country.call_args_list)  # type: ignore[attr-defined]
    assert max(asked.values()) == 1 and "DE" not in asked  # Germany is the DWD's; each MeteoAlarm feed once
    swiss = [e for e in answer["events"] if e["country"] == "CH"]
    assert len(swiss) < sum(len(e["alerts"]) for e in swiss)  # the Swiss areas are grouped
    unavailable = [c for c in answer["coverage"] if c["source"] == "swic"]
    assert unavailable and all(c["status"] == "unavailable" for c in unavailable)  # said, not left out


def test_a_country_without_a_source_is_404(recorded: World) -> None:
    response = recorded.client.get("/v1/events", params={"country": "JP"})
    assert response.status_code == 404 and "Japan" in response.json()["detail"]


def test_the_hazards_only_the_nws_reports_are_not_said_twice(recorded: World, monkeypatch: pytest.MonkeyPatch) -> None:
    nws_hazard = hazard_samples.earthquake("noaa_abc", SeverityLevel.RED, source="NOAA_NWS", lat=29.7, lon=-95.4).model_copy(
        update={"category": HazardCategory.FLOOD, "title": "Flash Flood Warning - Harris, TX"}
    )
    quake_in_us = hazard_samples.earthquake("usgs_ca1", SeverityLevel.YELLOW, value=5.1, lat=36.0, lon=-120.0, tsunami=False)
    monkeypatch.setattr(recorded.aggregator.noaa_nws, "fetch", AsyncMock(return_value=[nws_hazard]))
    monkeypatch.setattr(recorded.aggregator.usgs, "fetch", AsyncMock(return_value=[quake_in_us]))
    answer = recorded.client.get("/v1/events", params={"country": "US"}).json()
    ids = {h["id"] for e in answer["events"] for h in e["hazards"]}
    assert ids == {"usgs_ca1"}  # the earthquake in California stays; the NWS flood is the NWS warning's
    elsewhere = recorded.client.get("/v1/events", params={"country": "FR"}).json()
    assert not any(e["hazards"] for e in elsewhere["events"])  # neither is in France


# ---------------------------------------------------------------------------------------------- what it costs


async def test_a_world_view_costs_the_clock_not_the_clients(sim_clock: SimClock) -> None:
    """Ten clients asking the world within a minute, then again after the feeds' 180 s: each feed twice, never per client."""
    sim_clock.advance(samples.RECORDED_AT - SimClock.EPOCH)
    upstream = Upstream()
    upstream.default_cap = samples.fixture("cap_france_b77d3bc0.xml")
    service = make_service(upstream)
    for _ in range(10):
        await service.for_world()
        sim_clock.advance(5)
    sim_clock.advance(200)
    for _ in range(10):
        await service.for_world()
    feeds = Counter(upstream.feed_requests)
    print(f"\n20 world views: MeteoAlarm feeds {sum(feeds.values())} requests for {len(feeds)} feeds, {len(upstream.cap_requests)} CAP documents")
    assert feeds and max(feeds.values()) == 2
    assert max(Counter(upstream.cap_requests).values()) == 1  # a document once
