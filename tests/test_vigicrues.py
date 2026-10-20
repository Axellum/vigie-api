"""The Vigicrues collector: the recorded flow of 2026-10-20, the level table, the lines, and the cadence.

The recorded answer is real (``tests/fixtures/vigicrues/InfoVigiCru_subset.geojson``): the 19 sections that were above
green that day (17 yellow, 2 orange: the Vistre and the Vidourle) and 12 green ones, re-serialized from the 337 of the
full flow (the weight of the real answer, 2.2 MB, is not kept in the repository). The flows built here are **synthetic**:
the recorded one with a level changed, to reach the days when no section is above green or one is red.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import httpx
import pytest

from app.attribution import credit_text
from app.collectors import vigicrues
from app.collectors.base import Fetched
from app.collectors.vigicrues import VigicruesCollector, derive_level, parse_flow
from app.models.schemas import SeverityLevel
from app.models.vigilance import VigilancePhenomenon
from app.services.geoindex import line_distance_km
from tests import vigilance_samples as samples
from tests.helpers import Provider, SimClock


def recorded_payload() -> dict[str, Any]:
    return json.loads(samples.national_fixture("vigicrues", "InfoVigiCru_subset.geojson"))


def with_levels(level: int, only: tuple[str, ...] = ()) -> dict[str, Any]:
    """The recorded flow with every section (or the ones named) set to ``level``."""
    payload = recorded_payload()
    for feature in payload["features"]:
        if not only or feature["properties"]["CdEntCru"] in only:
            feature["properties"]["NivInfViCr"] = level
    return payload


def calm_but(code: str, level: int) -> dict[str, Any]:
    """Every section green except one."""
    payload = with_levels(1)
    for feature in payload["features"]:
        if feature["properties"]["CdEntCru"] == code:
            feature["properties"]["NivInfViCr"] = level
    return payload


# ---------------------------------------------------------------- the recorded flow


def test_the_recorded_flow_is_19_sections_above_green_out_of_31() -> None:
    flow = samples.vigicrues_flow()
    assert flow.sections == 31 and flow.skipped == 0 and len(flow.vigilances) == 19
    by_level = {level: [v.area.description for v in flow.vigilances if v.level == level] for level in (SeverityLevel.YELLOW, SeverityLevel.ORANGE)}
    assert sorted(by_level[SeverityLevel.ORANGE]) == ["Vidourle", "Vistre"]
    assert len(by_level[SeverityLevel.YELLOW]) == 17
    assert (flow.reference, flow.updated_utc) == ("20102026_10", 1_792_482_915)  # 2026-10-20 07:55:15 UTC


def test_a_green_section_is_no_warning() -> None:
    codes = {v.id for v in samples.vigicrues_flow().vigilances}
    green = {f["properties"]["CdEntCru"] for f in recorded_payload()["features"] if f["properties"]["NivInfViCr"] == 1}
    assert len(green) == 12 and not any(f"vigicrues:{code}" in codes for code in green)


def test_a_section_is_normalized_with_its_territory_and_its_official_page() -> None:
    vistre = next(v for v in samples.vigicrues_flow().vigilances if v.id == "vigicrues:GA32")
    assert (vistre.level, vistre.level_original) == (SeverityLevel.ORANGE, "3")
    assert (vistre.phenomenon, vistre.phenomenon_original) == (VigilancePhenomenon.FLOOD, None)
    assert (vistre.country, vistre.source, vistre.issuer) == ("FR", "vigicrues", None)
    assert vistre.area.description == "Vistre" and vistre.area.has_polygon is False
    assert [(g.scheme, g.value) for g in vistre.area.geocodes] == [
        ("VIGICRUES_SECTION", "GA32"),
        ("VIGICRUES_TERRITORY", "20"),
        ("VIGICRUES_BASIN", "TCC20"),
    ]
    assert vistre.web == "https://www.vigicrues.gouv.fr/bulletin_local/20#GA32"  # the local bulletin of the territory, at the section
    assert vistre.link == "https://www.vigicrues.gouv.fr/services/InfoVigiCru.geojson"
    assert vistre.identifier == "20102026_10:GA32" and vistre.sent_utc == 1_792_482_915
    assert (vistre.onset_utc, vistre.expires_utc, vistre.ends_utc) == (None, None, None)  # the flow gives no times: none invented
    assert vistre.texts == []  # it has no words of its own: the official bulletin is the link
    assert vistre.status is None and vistre.derived == ["level", "phenomenon"]  # no status to derive: it is unknown, not invented
    assert vistre.shape is not None and vistre.shape.geom_type == "MultiLineString"


def test_the_credit_names_the_service_the_licence_and_the_date_of_the_last_update() -> None:
    vistre = next(v for v in samples.vigicrues_flow().vigilances if v.id == "vigicrues:GA32")
    assert vistre.attribution == f"{credit_text('vigicrues')} | Last update of the flow: 2026-10-20T07:55:15+00:00"
    assert credit_text("vigicrues") == (
        "Source: Vigicrues, Service central d'hydrométéorologie et d'appui à la prévision des inondations (SCHAPI), "
        "https://www.vigicrues.gouv.fr. Licence Ouverte 2.0"
    )


@pytest.mark.parametrize(
    ("place", "point", "section", "km"),
    [
        ("Sommières, on the Vidourle", samples.SOMMIERES, "Vidourle", 0.1),
        ("Alès, on the Gardon d'Alès", samples.ALES, "Gardon d'Alès", 0.5),
        ("Nîmes centre, 3.9 km from the Vistre", samples.NIMES, "Vistre", 3.9),
    ],
)
def test_the_distance_to_the_nearest_section_is_in_kilometres(place: str, point: tuple[float, float], section: str, km: float) -> None:
    sections = {v.area.description: v for v in samples.vigicrues_flow().vigilances}
    nearest = min(sections.values(), key=lambda v: line_distance_km(*point, v.shape))  # type: ignore[arg-type]
    assert nearest.area.description == section, place
    assert line_distance_km(*point, nearest.shape) == pytest.approx(km, abs=0.1), place  # type: ignore[arg-type]


def test_paris_is_hundreds_of_kilometres_from_any_section_above_green() -> None:
    assert min(line_distance_km(*samples.PARIS, v.shape) for v in samples.vigicrues_flow().vigilances) > 400  # type: ignore[arg-type]


# ---------------------------------------------------------------- the table and the readings


@pytest.mark.parametrize(
    ("value", "level"),
    [
        (1, SeverityLevel.GREEN),
        (2, SeverityLevel.YELLOW),
        (3, SeverityLevel.ORANGE),
        (4, SeverityLevel.RED),
        ("3", SeverityLevel.ORANGE),
        (0, None),
        (5, None),
        ("x", None),
        (None, None),
        (True, None),
        (2.0, None),
    ],
)
def test_the_level_table(value: object, level: object) -> None:
    assert derive_level(value) == level


def test_a_red_section_is_red() -> None:
    flow = parse_flow(calm_but("GA32", 4))
    [red] = flow.vigilances
    assert red.id == "vigicrues:GA32" and red.level_original == "4"


def test_a_calm_day_is_every_section_green_and_nothing_in_force() -> None:
    flow = parse_flow(with_levels(1))
    assert flow.vigilances == () and flow.sections == 31 and flow.skipped == 0


def test_a_section_that_cannot_be_read_is_counted_not_guessed() -> None:
    payload = recorded_payload()
    payload["features"][0]["geometry"] = {"type": "Point", "coordinates": [3.0, 43.0]}  # CO3, yellow: no line
    payload["features"][1]["properties"]["CdEntCru"] = ""  # GA5, yellow: no code
    payload["features"][2]["properties"]["NivInfViCr"] = 9  # GA6: a level that is not one
    payload["features"][3] = "garbage"
    flow = parse_flow(payload)
    assert flow.skipped == 4 and len(flow.vigilances) == 15  # the four first sections of the flow were all above green
    assert not {"vigicrues:CO3", "vigicrues:GA5", "vigicrues:GA6", "vigicrues:GA1"} & {v.id for v in flow.vigilances}


def test_a_section_without_territory_has_no_page() -> None:
    payload = recorded_payload()
    for feature in payload["features"]:
        if feature["properties"]["CdEntCru"] == "GA32":
            del feature["properties"]["cdensup_1"]
    vistre = next(v for v in parse_flow(payload).vigilances if v.id == "vigicrues:GA32")
    assert vistre.web is None and [g.scheme for g in vistre.area.geocodes] == ["VIGICRUES_SECTION", "VIGICRUES_BASIN"]


def test_an_answer_that_is_not_a_flow_is_an_error() -> None:
    for payload in (None, [], {"error": "maintenance"}, {"features": "no"}):
        with pytest.raises(ValueError, match="usable 'features'"):
            parse_flow(payload)


def test_a_flow_beyond_any_size_it_ever_had_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vigicrues, "MAX_FEATURES", 10)
    with pytest.raises(ValueError):
        parse_flow(recorded_payload())


# ---------------------------------------------------------------- the requests


def flow_server(payload: dict[str, Any]) -> Provider:
    """Vigicrues as recorded: the whole flow, no ETag, no Last-Modified, ``Cache-Control: max-age=120``."""
    body = json.dumps(payload).encode()
    return Provider(answer=lambda request: httpx.Response(200, content=body, headers={"Cache-Control": "max-age=120"}), latency_ticks=0)


@pytest.mark.asyncio
async def test_with_a_section_orange_the_flow_is_kept_ten_minutes(sim_clock: SimClock) -> None:
    provider = flow_server(recorded_payload())
    collector = VigicruesCollector(http_client=provider.client())

    flow = (await collector.fetch_flow()).value
    assert len(flow.vigilances) == 19 and provider.count == 1
    assert str(provider.requests[0].url) == "https://www.vigicrues.gouv.fr/services/InfoVigiCru.geojson"
    assert "If-None-Match" not in provider.requests[0].headers and "If-Modified-Since" not in provider.requests[0].headers

    sim_clock.advance(599)
    await collector.fetch_flow()
    assert provider.count == 1
    sim_clock.advance(2)
    await collector.fetch_flow()
    assert provider.count == 2


@pytest.mark.asyncio
async def test_with_only_yellow_sections_the_flow_is_kept_half_an_hour(sim_clock: SimClock) -> None:
    payload = recorded_payload()
    for feature in payload["features"]:
        feature["properties"]["NivInfViCr"] = min(feature["properties"]["NivInfViCr"], 2)  # no orange left
    provider = flow_server(payload)
    collector = VigicruesCollector(http_client=provider.client())
    await collector.fetch_flow()
    sim_clock.advance(1799)
    await collector.fetch_flow()
    assert provider.count == 1
    sim_clock.advance(2)
    await collector.fetch_flow()
    assert provider.count == 2


@pytest.mark.asyncio
async def test_a_calm_day_is_kept_half_an_hour_and_a_flood_that_arrives_shortens_it(sim_clock: SimClock) -> None:
    served = {"payload": with_levels(1)}
    provider = Provider(answer=lambda request: httpx.Response(200, content=json.dumps(served["payload"]).encode()), latency_ticks=0)
    collector = VigicruesCollector(http_client=provider.client())
    assert (await collector.fetch_flow()).value.vigilances == ()
    served["payload"] = calm_but("GA32", 3)
    sim_clock.advance(1801)
    assert len((await collector.fetch_flow()).value.vigilances) == 1 and provider.count == 2
    sim_clock.advance(601)  # now an orange section: the next look is ten minutes later
    await collector.fetch_flow()
    assert provider.count == 3


@pytest.mark.asyncio
async def test_a_failing_server_falls_back_on_the_last_flow_and_says_so(sim_clock: SimClock) -> None:
    ok = flow_server(recorded_payload())
    state = {"down": False}

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503) if state["down"] else ok.answer(request)  # type: ignore[misc]

    collector = VigicruesCollector(http_client=Provider(answer=answer, latency_ticks=0).client())
    await collector.fetch_flow()
    state["down"] = True
    sim_clock.advance(601)
    fetched: Fetched[Any] = await collector.fetch_flow()
    assert fetched.stale is True and len(fetched.value.vigilances) == 19


def test_the_collector_is_a_warnings_provider_with_the_published_cadence() -> None:
    original = copy.copy(vigicrues.TTL_CALM_S), copy.copy(vigicrues.TTL_FLOOD_S)
    assert original == (1800.0, 600.0)
    collector = VigicruesCollector()
    assert collector.provider_id == "vigicrues_flow" and collector.domain == "warnings" and collector.ttl_seconds == 1800
