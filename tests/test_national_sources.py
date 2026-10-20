"""The national sources in the service: which source answers for a country, the duplicates, the rivers, the pushes.

The service runs on the simulated upstream of ``tests/vigilance_upstream.py``, which serves the answers recorded on
2026-10-20 (MeteoAlarm, DWD, ECCC, MetService, Vigicrues) unless a test puts something else in their place. The clock stands at
the moment of the recordings, so that the warnings are in force. Nothing reaches the network.

What is **synthetic** here (marked as such) is only what the recordings of that day do not hold: a river section in the
Drôme, a warning that appears, a server that fails.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import pytest

from app.attribution import METEOALARM_DISCLAIMER, credit_text
from app.models.schemas import SeverityLevel
from app.models.vigilance import VigilanceStatus
from app.services.vigilance import NATIONAL_SOURCES, FollowedPoint, VigilanceService
from tests import vigilance_samples as samples
from tests.helpers import SimClock
from tests.test_metservice import BASE, synthetic_cap, synthetic_feed
from tests.vigilance_upstream import Upstream, atom_entry, atom_feed, make_service

DROME_DOC = "b77d3bc0-76a2-4886-8da8-bac8d99f57fc"
KEHL = (48.5734, 7.8152)  # on the German side of the Rhine, 2 km from France


@pytest.fixture()
def upstream() -> Upstream:
    return Upstream()


@pytest.fixture()
def service(upstream: Upstream, sim_clock: SimClock) -> VigilanceService:
    sim_clock.advance(samples.NATIONAL_RECORDED_AT - SimClock.EPOCH)
    return make_service(upstream, national=True)


def summary(response: Any) -> list[tuple[str, Optional[str], Optional[str]]]:
    """(source, level, area) of each warning of an answer, in the order given."""
    return [(v.source, v.level.value if v.level else None, v.area.description) for v in response.vigilances]


def river(code: str, name: str, level: int, *coordinates: tuple[float, float]) -> dict[str, Any]:
    """A SYNTHETIC river section of the Vigicrues flow, in the form of the recorded ones."""
    return {
        "type": "Feature",
        "properties": {"CdTCC": "TCC20", "CdEntCru": code, "lbentcru": name, "cdensup_1": "20", "NivInfViCr": level},
        "geometry": {"type": "MultiLineString", "coordinates": [[list(c) for c in coordinates]]},
    }


def flow(*sections: dict[str, Any]) -> bytes:
    return json.dumps(
        {"type": "FeatureCollection", "RefInfoVigiCru": "synthetic", "DtHrInfoVigiCru": "2026-10-20T07:55:15+00:00", "features": list(sections)}
    ).encode()


# A SYNTHETIC section 1.5 km from Valence (Drôme), whose level the tests change.
VALENCE_RIVER = (4.8924, 44.9934), (4.9124, 44.9034)


def recorded_flow_with(code: str, level: int) -> bytes:
    payload = json.loads(samples.national_fixture("vigicrues", "InfoVigiCru_subset.geojson"))
    for feature in payload["features"]:
        if feature["properties"]["CdEntCru"] == code:
            feature["properties"]["NivInfViCr"] = level
    return json.dumps(payload).encode()


# ---------------------------------------------------------------- which source answers for a country


def test_the_national_sources_are_germany_canada_and_new_zealand() -> None:
    assert NATIONAL_SOURCES == {"DE": "dwd", "CA": "eccc", "NZ": "metservice"}


@pytest.mark.parametrize(
    ("country", "plan"),
    [
        ("DE", ["dwd"]),  # the national service, not MeteoAlarm's copies of it
        ("CA", ["eccc"]),
        ("NZ", ["metservice"]),
        ("FR", ["meteoalarm", "vigicrues"]),  # MeteoAlarm for the départements, Vigicrues for the rivers
        ("IT", ["meteoalarm"]),  # no national collector: MeteoAlarm stays
        ("IS", ["meteoalarm"]),
        ("US", []),  # the NWS costs no request of its own (its answer is the hazards')
        ("JP", []),
    ],
)
def test_the_sources_asked_for_a_country(service: VigilanceService, country: str, plan: list[str]) -> None:
    assert service._plan(country) == plan


def test_without_the_national_collectors_germany_is_meteoalarms(upstream: Upstream, sim_clock: SimClock) -> None:
    plain = make_service(upstream)  # the service of before: the collectors are not wired
    assert plain._plan("DE") == ["meteoalarm"] and plain._plan("FR") == ["meteoalarm"] and plain._plan("CA") == []


# ---------------------------------------------------------------- Germany: the DWD, and no copy of it


@pytest.mark.asyncio
async def test_a_point_in_germany_gets_the_dwd_and_meteoalarm_is_not_asked(service: VigilanceService, upstream: Upstream) -> None:
    response = await service.for_point(*samples.BORKUM)
    assert response.countries == ["DE"]
    assert summary(response) == [("dwd", "YELLOW", "Insel Borkum")]
    assert [c.source for c in response.coverage] == ["dwd"] and response.coverage[0].status == "ok"
    assert response.attribution == credit_text("dwd_warnings") == "Source: Deutscher Wetterdienst"
    assert response.disclaimer is None  # the disclaimer is MeteoAlarm's; the DWD is the official service itself
    assert upstream.feed_requests == [] and upstream.dwd_provider.count == 1  # nothing was asked of MeteoAlarm for Germany


@pytest.mark.asyncio
async def test_a_point_with_no_dwd_warning_is_a_calm_answer_not_a_missing_one(service: VigilanceService) -> None:
    response = await service.for_point(*samples.BERLIN)
    assert response.vigilances == [] and response.coverage[0].status == "ok" and response.note is None
    assert response.coverage[0].feed_updated_utc == 1_792_497_016


@pytest.mark.asyncio
async def test_germany_as_a_whole_lists_the_seven_areas_in_german_english_and_french(service: VigilanceService) -> None:
    response = await service.for_country("DE")
    assert len(response.vigilances) == 7 and {v.source for v in response.vigilances} == {"dwd"}
    assert all(v.area.localization == "polygon" for v in response.vigilances)
    only_french = await service.for_country("DE", ("fr",))
    assert all([t.language for t in v.texts] == ["fr"] for v in only_french.vigilances)
    two = await service.for_country("DE", ("de", "en"))
    assert all([t.language for t in v.texts] == ["de-DE", "en"] for v in two.vigilances)


@pytest.mark.asyncio
async def test_when_the_dwd_does_not_answer_meteoalarm_stands_in_and_says_so(
    upstream: Upstream, sim_clock: SimClock
) -> None:
    sim_clock.advance(1_792_450_000 - SimClock.EPOCH)  # the morning, when the recorded German MeteoAlarm warnings were in force
    upstream.dwd = 503
    service = make_service(upstream, national=True)
    response = await service.for_country("DE")
    assert [(c.source, c.status) for c in response.coverage] == [("dwd", "unavailable"), ("meteoalarm", "ok")]
    assert "gave no answer" in (response.coverage[0].note or "")
    assert "national service gave no answer" in (response.coverage[1].note or "")
    assert {v.source for v in response.vigilances} == {"meteoalarm"} and len(response.vigilances) == 6
    # Only the source that answered is credited, and the disclaimer goes with MeteoAlarm.
    assert response.attribution == credit_text("meteoalarm") and response.disclaimer == METEOALARM_DISCLAIMER
    assert upstream.feed_requests == ["germany"]


@pytest.mark.asyncio
async def test_the_fallback_is_for_germany_only_a_country_without_feed_is_just_unavailable(upstream: Upstream, sim_clock: SimClock) -> None:
    sim_clock.advance(samples.NATIONAL_RECORDED_AT - SimClock.EPOCH)
    upstream.eccc = 500
    response = await make_service(upstream, national=True).for_country("CA")
    assert [(c.source, c.status) for c in response.coverage] == [("eccc", "unavailable")]
    assert response.vigilances == [] and response.attribution is None  # nothing answered: nothing credited


# ---------------------------------------------------------------- Canada and New Zealand


@pytest.mark.asyncio
async def test_a_point_in_the_yukon_gets_its_wind_warning_in_both_languages(service: VigilanceService) -> None:
    response = await service.for_point(*samples.WHITEHORSE)
    assert response.countries == ["CA"] and summary(response) == [("eccc", "YELLOW", "Whitehorse")]
    [warning] = response.vigilances
    assert [t.language for t in warning.texts] == ["en", "fr"] and warning.texts[1].event == "avertissement de vent"
    assert response.attribution == credit_text("eccc_alerts") and response.disclaimer is None
    assert "GeoMet" in (response.coverage[0].note or "") and "Datamart" in (response.coverage[0].note or "")  # the note says where it comes from


@pytest.mark.asyncio
async def test_the_texts_of_canada_can_be_limited_to_one_language(service: VigilanceService) -> None:
    response = await service.for_point(*samples.WHITEHORSE, ("fr",))
    assert [t.language for t in response.vigilances[0].texts] == ["fr"]


@pytest.mark.asyncio
async def test_a_point_with_no_alert_in_canada_and_the_whole_country(service: VigilanceService) -> None:
    assert (await service.for_point(*samples.TORONTO)).vigilances == []
    assert len((await service.for_country("CA")).vigilances) == 36
    assert len((await service.for_point(*samples.RONDEAU)).vigilances) == 1


@pytest.mark.asyncio
async def test_new_zealand_on_a_day_without_warning(service: VigilanceService) -> None:
    response = await service.for_point(*samples.WELLINGTON)
    assert response.countries == ["NZ"] and response.vigilances == []
    assert [(c.source, c.status) for c in response.coverage] == [("metservice", "ok")] and response.note is None
    assert response.attribution == credit_text("metservice_alerts")


@pytest.mark.asyncio
async def test_a_synthetic_orange_warning_in_new_zealand_reaches_a_point_inside_its_polygon(upstream: Upstream, sim_clock: SimClock) -> None:
    """SYNTHETIC feed and document (see tests/test_metservice.py): the reading of what MetService will publish."""
    sim_clock.advance(1_792_537_000 - SimClock.EPOCH)  # inside the synthetic warning (2026-10-20 22:56 UTC)
    address = BASE + "wellington.cap"
    upstream.metservice = synthetic_feed(address)
    upstream.metservice_documents = {address: synthetic_cap("wellington-1")}
    service = make_service(upstream, national=True)
    inside = await service.for_point(*samples.WELLINGTON)
    assert summary(inside) == [("metservice", "ORANGE", "Wellington (synthetic)")]
    assert inside.vigilances[0].area.localization == "polygon"
    assert (await service.for_point(-43.53, 172.64)).vigilances == []  # Christchurch is outside the polygon


@pytest.mark.asyncio
async def test_a_service_that_reads_what_it_cannot_understand_says_so(upstream: Upstream, sim_clock: SimClock) -> None:
    sim_clock.advance(samples.NATIONAL_RECORDED_AT - SimClock.EPOCH)
    upstream.metservice = synthetic_feed(BASE + "gone.cap", "https://elsewhere.example/x.cap")  # a 404, and an address that is not MetService's
    response = await make_service(upstream, national=True).for_country("NZ")
    assert response.vigilances == [] and "2 of the 2 items" in (response.coverage[0].note or "")


# ---------------------------------------------------------------- a border


@pytest.mark.asyncio
async def test_a_point_by_the_rhine_gets_the_dwd_for_germany_and_the_french_sources_for_france(service: VigilanceService, upstream: Upstream) -> None:
    response = await service.for_point(*KEHL)
    assert response.countries == ["DE", "FR"]
    assert [c.source for c in response.coverage] == ["dwd", "meteoalarm", "vigicrues"]
    assert upstream.feed_requests == ["france"]  # MeteoAlarm for France only
    assert response.attribution is not None and credit_text("dwd_warnings") in response.attribution


# ---------------------------------------------------------------- France: MeteoAlarm and the rivers


@pytest.mark.asyncio
async def test_a_point_near_a_river_in_orange_gets_the_section_with_its_distance(service: VigilanceService) -> None:
    response = await service.for_point(*samples.NIMES)
    [vistre] = response.vigilances
    assert (vistre.source, vistre.level, vistre.area.description) == ("vigicrues", SeverityLevel.ORANGE, "Vistre")
    assert vistre.distance_km == pytest.approx(3.9, abs=0.1) and vistre.area.localization == "line"
    assert vistre.web == "https://www.vigicrues.gouv.fr/bulletin_local/20#GA32"
    assert vistre.attribution.startswith(credit_text("vigicrues"))
    assert response.query.river_radius_km == 5.0


@pytest.mark.asyncio
async def test_a_town_on_a_river_gets_it_at_a_hundred_metres(service: VigilanceService) -> None:
    response = await service.for_point(*samples.SOMMIERES)
    [vidourle] = response.vigilances
    assert (vidourle.area.description, vidourle.level) == ("Vidourle", SeverityLevel.ORANGE)
    assert vidourle.distance_km is not None and vidourle.distance_km < 0.2


@pytest.mark.asyncio
async def test_a_yellow_section_in_an_orange_departement_gives_both(service: VigilanceService) -> None:
    """Alès: the Gardon d'Alès is yellow, the Gard is orange for MeteoAlarm: the département is higher, so it stays."""
    response = await service.for_point(*samples.ALES)
    assert summary(response) == [("meteoalarm", "ORANGE", "Gard"), ("vigicrues", "YELLOW", "Gardon d'Alès")]


@pytest.mark.asyncio
async def test_far_from_any_river_in_vigilance_there_is_no_section(service: VigilanceService) -> None:
    response = await service.for_point(*samples.PARIS)
    assert response.vigilances == [] and [c.status for c in response.coverage] == ["ok", "ok"]
    assert "19 of the 31 river sections are above green" in (response.coverage[1].note or "")


@pytest.mark.asyncio
async def test_the_radius_is_the_clients_to_choose(service: VigilanceService) -> None:
    near = await service.for_point(*samples.NIMES, river_radius_km=3.0)  # the Vistre is 3.9 km away: out
    assert [v.source for v in near.vigilances] == ["meteoalarm"]  # and what MeteoAlarm says of the Gard stays (see the rule below)
    wide = await service.for_point(*samples.MONTPELLIER, river_radius_km=25.0)
    assert sorted(v.area.description or "" for v in wide.vigilances) == ["Vidourle", "Vistre"]
    assert {v.distance_km is not None for v in wide.vigilances} == {True}
    assert wide.query.river_radius_km == 25.0


@pytest.mark.asyncio
async def test_the_geojson_draws_the_river_as_a_line(service: VigilanceService) -> None:
    collection = service.to_geojson(await service.for_point(*samples.SOMMIERES))
    [feature] = collection.features
    assert feature.geometry is not None and feature.geometry["type"] == "MultiLineString"
    assert feature.properties["geometry_source"] == "line" and feature.properties["distance_km"] < 0.2


@pytest.mark.asyncio
async def test_only_a_river_section_has_a_distance(service: VigilanceService) -> None:
    drome = await service.for_point(*samples.VALENCE)
    assert [(v.source, v.distance_km) for v in drome.vigilances] == [("meteoalarm", None)]


# ---- the duplicates between MeteoAlarm and Vigicrues


@pytest.mark.asyncio
async def test_the_gard_flooding_of_meteoalarm_is_left_out_when_the_river_in_orange_says_it(service: VigilanceService) -> None:
    """Nîmes (Gard): MeteoAlarm's orange "Flooding" of the département is the Vistre's orange: one warning, the finer."""
    gard = await service.for_point(*samples.NIMES)
    assert summary(gard) == [("vigicrues", "ORANGE", "Vistre")]


@pytest.mark.asyncio
async def test_without_a_section_in_reach_the_departements_flooding_stays(service: VigilanceService) -> None:
    """Valence: no section above green within 5 km, so what MeteoAlarm says of the Drôme is all there is."""
    assert summary(await service.for_point(*samples.VALENCE)) == [("meteoalarm", "YELLOW", "Drôme")]


@pytest.mark.asyncio
async def test_a_country_list_drops_every_flooding_a_section_of_the_same_level_covers_and_keeps_the_rain_floods(service: VigilanceService) -> None:
    response = await service.for_country("FR")
    meteoalarm = [v for v in response.vigilances if v.source == "meteoalarm"]
    assert not [v for v in meteoalarm if (v.phenomenon_original or "").lower() == "flooding"]  # the 8 départements are all covered
    assert {(v.phenomenon_original, v.area.description) for v in meteoalarm if v.phenomenon_original == "Rain-flood"} == {
        ("Rain-flood", "Corse-du-Sud"),
        ("Rain-flood", "Haute-Corse"),
    }  # "Rain-flood" is another vigilance and always stays
    assert len([v for v in response.vigilances if v.source == "vigicrues"]) == 19


@pytest.mark.asyncio
async def test_synthetic_a_flooding_of_a_higher_level_than_the_section_stays(upstream: Upstream, sim_clock: SimClock) -> None:
    """SYNTHETIC: an orange MeteoAlarm flooding of the Drôme and a yellow section next to Valence: both are in the answer."""
    sim_clock.advance(samples.NATIONAL_RECORDED_AT - SimClock.EPOCH)
    upstream.feeds["france"] = atom_feed(atom_entry(DROME_DOC, level="Orange"))
    upstream.vigicrues = flow(river("SYN1", "Rivière synthétique", 2, *VALENCE_RIVER))
    response = await make_service(upstream, national=True).for_point(*samples.VALENCE)
    assert summary(response) == [("meteoalarm", "ORANGE", "Drôme"), ("vigicrues", "YELLOW", "Rivière synthétique")]


@pytest.mark.asyncio
async def test_synthetic_a_flooding_of_the_same_level_is_left_out_and_a_higher_section_too(upstream: Upstream, sim_clock: SimClock) -> None:
    sim_clock.advance(samples.NATIONAL_RECORDED_AT - SimClock.EPOCH)
    upstream.feeds["france"] = atom_feed(atom_entry(DROME_DOC, level="Yellow"))
    upstream.vigicrues = flow(river("SYN1", "Rivière synthétique", 2, *VALENCE_RIVER))
    same = await make_service(upstream, national=True).for_point(*samples.VALENCE)
    assert summary(same) == [("vigicrues", "YELLOW", "Rivière synthétique")]
    upstream.vigicrues = flow(river("SYN1", "Rivière synthétique", 4, *VALENCE_RIVER))  # a red section covers a yellow flooding
    higher = await make_service(upstream, national=True).for_point(*samples.VALENCE)
    assert summary(higher) == [("vigicrues", "RED", "Rivière synthétique")]


@pytest.mark.asyncio
async def test_synthetic_a_rain_flood_is_never_left_out(upstream: Upstream, sim_clock: SimClock) -> None:
    sim_clock.advance(samples.NATIONAL_RECORDED_AT - SimClock.EPOCH)
    upstream.feeds["france"] = atom_feed(atom_entry(DROME_DOC, level="Yellow", kind="Rain-flood"))
    upstream.vigicrues = flow(river("SYN1", "Rivière synthétique", 4, *VALENCE_RIVER))
    response = await make_service(upstream, national=True).for_point(*samples.VALENCE)
    assert summary(response) == [("vigicrues", "RED", "Rivière synthétique"), ("meteoalarm", "YELLOW", "Drôme")]


@pytest.mark.asyncio
async def test_synthetic_when_vigicrues_does_not_answer_nothing_is_left_out(upstream: Upstream, sim_clock: SimClock) -> None:
    sim_clock.advance(samples.NATIONAL_RECORDED_AT - SimClock.EPOCH)
    upstream.feeds["france"] = atom_feed(atom_entry(DROME_DOC, level="Yellow"))
    upstream.vigicrues = 503
    response = await make_service(upstream, national=True).for_point(*samples.VALENCE)
    assert summary(response) == [("meteoalarm", "YELLOW", "Drôme")]
    assert [(c.source, c.status) for c in response.coverage] == [("meteoalarm", "ok"), ("vigicrues", "unavailable")]
    assert response.attribution == credit_text("meteoalarm")  # Vigicrues said nothing: it is not credited


# ---------------------------------------------------------------- keeping the feeds fresh


@pytest.mark.asyncio
async def test_the_scheduler_refreshes_the_national_service_of_a_followed_point_not_meteoalarm(service: VigilanceService, upstream: Upstream) -> None:
    point = FollowedPoint(key="p", lat=samples.BERLIN[0], lon=samples.BERLIN[1], label={}, audience="followers")
    await service.refresh([point])
    assert upstream.dwd_provider.count == 1 and upstream.feed_requests == []
    await service.refresh([point])  # cached: no second request
    assert upstream.dwd_provider.count == 1


@pytest.mark.asyncio
async def test_the_scheduler_refreshes_both_french_sources(service: VigilanceService, upstream: Upstream) -> None:
    point = FollowedPoint(key="p", lat=samples.PARIS[0], lon=samples.PARIS[1], label={}, audience="followers")
    await service.refresh([point])
    assert upstream.feed_requests == ["france"] and upstream.vigicrues_provider.count == 1


# ---------------------------------------------------------------- the pushes


SOMMIERES = FollowedPoint(key="p:sommieres", lat=samples.SOMMIERES[0], lon=samples.SOMMIERES[1], label={}, audience="followers")


async def look(service: VigilanceService, clock: SimClock, *points: FollowedPoint) -> list[tuple[str, str]]:
    clock.advance(1801)  # past every cache lifetime
    return [(change.change, change.vigilance.area.description or "?") for _, change in await service.poll_changes(list(points))]


@pytest.mark.asyncio
async def test_a_river_that_goes_up_and_down_is_pushed_with_its_distance(service: VigilanceService, upstream: Upstream, sim_clock: SimClock) -> None:
    upstream.feeds["france"] = atom_feed()
    upstream.vigicrues = recorded_flow_with("GA6", 1)  # the Vidourle calm
    assert await service.poll_changes([SOMMIERES]) == []  # the baseline
    upstream.vigicrues = recorded_flow_with("GA6", 3)
    sim_clock.advance(1801)
    [(_, change)] = await service.poll_changes([SOMMIERES])
    assert (change.change, change.vigilance.area.description, change.vigilance.level) == ("appeared", "Vidourle", SeverityLevel.ORANGE)
    assert change.vigilance.distance_km is not None and change.vigilance.distance_km < 0.2
    assert change.attribution is not None and change.attribution.startswith(credit_text("vigicrues")) and change.disclaimer is None
    upstream.vigicrues = recorded_flow_with("GA6", 4)
    assert await look(service, sim_clock, SOMMIERES) == [("upgraded", "Vidourle")]
    upstream.vigicrues = recorded_flow_with("GA6", 1)
    assert await look(service, sim_clock, SOMMIERES) == [("ended", "Vidourle")]


@pytest.mark.asyncio
async def test_a_source_that_is_down_ends_nothing_and_does_not_mute_the_others(
    service: VigilanceService, upstream: Upstream, sim_clock: SimClock
) -> None:
    upstream.feeds["france"] = atom_feed()
    assert await service.poll_changes([SOMMIERES]) == []  # the Vidourle is orange at the baseline
    upstream.vigicrues = 503
    # MeteoAlarm says a thunderstorm in the Gard (FR812) while Vigicrues is down: pushed, and the river is not "ended".
    upstream.feeds["france"] = atom_feed(atom_entry("11111111-1111-1111-1111-111111111111", kind="Thunderstorm", code="FR812", name="Gard"))
    assert await look(service, sim_clock, SOMMIERES) == [("appeared", "Gard")]
    upstream.vigicrues = recorded_flow_with("GA6", 3)  # back, unchanged: nothing to say
    assert await look(service, sim_clock, SOMMIERES) == []


@pytest.mark.asyncio
async def test_a_source_that_was_down_at_the_baseline_does_not_push_what_was_already_there(
    service: VigilanceService, upstream: Upstream, sim_clock: SimClock
) -> None:
    upstream.feeds["france"] = atom_feed()
    upstream.vigicrues = 503
    assert await service.poll_changes([SOMMIERES]) == []  # the baseline, without Vigicrues
    upstream.vigicrues = recorded_flow_with("GA6", 3)
    assert await look(service, sim_clock, SOMMIERES) == []  # the Vidourle was orange before: its first look is its baseline
    upstream.vigicrues = recorded_flow_with("GA6", 4)
    assert await look(service, sim_clock, SOMMIERES) == [("upgraded", "Vidourle")]


@pytest.mark.asyncio
async def test_the_copies_of_the_fallback_are_not_pushed(upstream: Upstream, sim_clock: SimClock) -> None:
    sim_clock.advance(1_792_450_000 - SimClock.EPOCH)
    upstream.dwd = 503
    service = make_service(upstream, national=True)
    berlin = FollowedPoint(key="p:de", lat=samples.BERLIN[0], lon=samples.BERLIN[1], label={}, audience="followers")
    assert await service.poll_changes([berlin]) == []
    assert service._tracked["p:de"] == {}  # MeteoAlarm's copies (stand-in for the DWD) are in the answers, not in the pushes


@pytest.mark.asyncio
async def test_a_dwd_warning_that_appears_is_pushed_with_its_words(upstream: Upstream, sim_clock: SimClock) -> None:
    sim_clock.advance(samples.NATIONAL_RECORDED_AT - SimClock.EPOCH)
    service = make_service(upstream, national=True)
    borkum = FollowedPoint(key="p:borkum", lat=samples.BORKUM[0], lon=samples.BORKUM[1], label={}, audience="followers")
    upstream.dwd = b"PK\x05\x06" + b"\x00" * 18  # an empty ZIP: nothing in force
    assert await service.poll_changes([borkum]) == []
    upstream.dwd = samples.national_fixture("dwd", "status_district_mul.zip")
    sim_clock.advance(121)
    [(_, change)] = await service.poll_changes([borkum])
    assert (change.change, change.vigilance.source, change.vigilance.area.description) == ("appeared", "dwd", "Insel Borkum")
    assert change.vigilance.status == VigilanceStatus.NEW and change.vigilance.texts[0].language == "de-DE"
    assert change.attribution is not None and change.attribution.startswith("Source: Deutscher Wetterdienst")
    assert change.disclaimer is None
