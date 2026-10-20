"""The Vigicrues collector: the recorded flow of 2026-10-20, the level table, the lines, and the requests.

The recorded answer is real (``tests/fixtures/vigicrues/InfoVigiCru_subset.geojson``): the 19 sections that were above
green that day (17 yellow, 2 orange: the Vistre and the Vidourle) and 12 green ones, re-serialized from the 337 of the
full flow (the weight of the real answer, 2.2 MB, is not kept in the repository). So is the head of the national map image
(``img_france.headers.txt``), which the collector asks instead of the flow to know whether a publication happened. The flows
built here are **synthetic**: the recorded one with a level changed, to reach the days when no section is above green or one
is red; and the publications that follow the recorded one (a new image stamp, a new flow) are simulated too.
"""

from __future__ import annotations

import json
from typing import Any, Optional

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

FLOW = vigicrues.FLOW_URL
IMAGE = vigicrues.SIGNAL_URL
HOUR = 3600


def recorded_image_headers() -> dict[str, str]:
    """The ``Last-Modified`` and ``ETag`` the image sent on 2026-10-20 (13:35 UTC), as recorded."""
    lines = samples.national_fixture("vigicrues", "img_france.headers.txt").decode().splitlines()
    headers = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)
    return {"Last-Modified": headers["Last-Modified"], "ETag": headers["ETag"]}


class FakeVigicrues:
    """Vigicrues as recorded: the whole flow without validator (a ``GET``), and the map image with its validators (a ``HEAD``).

    ``publish`` is a new publication: a new flow, and the image regenerated (a new ``Last-Modified``). Setting ``image`` to
    ``None`` makes the image disappear (404), to ``{}`` makes it answer without validator.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.image: Optional[dict[str, str]] = recorded_image_headers()
        self.flow_late = False  # the image moves with a publication, the flow follows later
        self.down = False
        self.provider = Provider(answer=self.answer, latency_ticks=0)
        self.publications = 0

    def answer(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            return httpx.Response(503)
        url = str(request.url)
        if url == IMAGE:
            assert request.method == "HEAD"
            return httpx.Response(404) if self.image is None else httpx.Response(200, headers=self.image)
        assert url == FLOW and request.method == "GET"
        body = json.dumps(self.payload).encode()
        return httpx.Response(200, content=body, headers={"Cache-Control": "max-age=120"})

    def publish(self, payload: Optional[dict[str, Any]] = None, *, reference: str = "", updated: str = "") -> None:
        """A new publication: the image gets a new stamp; the flow gets new levels and a new reference unless it is late."""
        self.publications += 1
        self.image = {"Last-Modified": f"Tue, 20 Oct 2026 13:55:{self.publications:02d} GMT", "ETag": f'"2f637-{self.publications}"'}
        if not self.flow_late:
            self.payload = payload or self.payload
            self.payload["RefInfoVigiCru"] = reference or f"20102026_{16 + self.publications}"
            self.payload["DtHrInfoVigiCru"] = updated or f"2026-10-20T13:55:{self.publications:02d}+00:00"

    def methods(self) -> list[str]:
        return [f"{r.method} {'image' if str(r.url) == IMAGE else 'flow'}" for r in self.provider.requests]


@pytest.mark.asyncio
async def test_the_first_look_asks_the_image_then_reads_the_flow_once(sim_clock: SimClock) -> None:
    server = FakeVigicrues(recorded_payload())
    collector = VigicruesCollector(http_client=server.provider.client())

    flow = (await collector.fetch_flow()).value
    assert len(flow.vigilances) == 19
    assert server.methods() == ["HEAD image", "GET flow"]
    assert str(server.provider.requests[1].url) == "https://www.vigicrues.gouv.fr/services/InfoVigiCru.geojson"
    assert "If-None-Match" not in server.provider.requests[1].headers  # the flow has no validator: none is sent


@pytest.mark.asyncio
async def test_while_nothing_is_published_only_the_image_is_asked_and_the_flow_is_kept(sim_clock: SimClock) -> None:
    server = FakeVigicrues(recorded_payload())  # two orange sections: a look every five minutes
    collector = VigicruesCollector(http_client=server.provider.client())
    first = (await collector.fetch_flow()).value

    sim_clock.advance(299)
    await collector.fetch_flow()
    assert server.provider.count == 2  # still fresh
    sim_clock.advance(2)
    again = (await collector.fetch_flow()).value
    assert server.methods() == ["HEAD image", "GET flow", "HEAD image"]
    assert again is first  # the very same flow: nothing was read again


@pytest.mark.asyncio
async def test_on_a_calm_day_the_image_is_asked_every_quarter_of_an_hour(sim_clock: SimClock) -> None:
    server = FakeVigicrues(with_levels(1))
    collector = VigicruesCollector(http_client=server.provider.client())
    await collector.fetch_flow()
    sim_clock.advance(899)
    await collector.fetch_flow()
    assert server.provider.count == 2
    sim_clock.advance(2)
    await collector.fetch_flow()
    assert server.methods() == ["HEAD image", "GET flow", "HEAD image"]


@pytest.mark.asyncio
async def test_a_publication_is_noticed_at_the_next_look_and_the_flow_is_read_again(sim_clock: SimClock) -> None:
    server = FakeVigicrues(with_levels(1))
    collector = VigicruesCollector(http_client=server.provider.client())
    assert (await collector.fetch_flow()).value.vigilances == ()

    server.publish(calm_but("GA32", 3))  # a flood: the Vistre turns orange
    sim_clock.advance(901)
    flow = (await collector.fetch_flow()).value
    assert [v.id for v in flow.vigilances] == ["vigicrues:GA32"]  # nothing lost: the new level is served at the next look
    assert server.methods() == ["HEAD image", "GET flow", "HEAD image", "GET flow"]

    sim_clock.advance(301)  # a section is orange now: the next look is five minutes later, and nothing moved
    await collector.fetch_flow()
    assert server.methods()[-1] == "HEAD image" and server.provider.count == 5


@pytest.mark.asyncio
async def test_the_flow_is_read_again_at_the_safety_age_whatever_the_image_says(sim_clock: SimClock) -> None:
    calm = FakeVigicrues(with_levels(1))
    collector = VigicruesCollector(http_client=calm.provider.client())
    await collector.fetch_flow()
    for _ in range(47):  # 11 h 45 min of quarter-hours: the image never moved
        sim_clock.advance(901)
        await collector.fetch_flow()
    assert calm.methods().count("GET flow") == 1
    sim_clock.advance(901 * 2)  # past 12 hours
    await collector.fetch_flow()
    assert calm.methods().count("GET flow") == 2

    flood = FakeVigicrues(recorded_payload())  # two orange sections: a look every five minutes, the same safety
    collector = VigicruesCollector(http_client=flood.provider.client())
    await collector.fetch_flow()
    for _ in range(143):  # 11 h 55 min of five-minute looks
        sim_clock.advance(301)
        await collector.fetch_flow()
    assert flood.methods().count("GET flow") == 1
    sim_clock.advance(301 * 2)  # past 12 hours
    await collector.fetch_flow()
    assert flood.methods().count("GET flow") == 2


@pytest.mark.asyncio
async def test_a_flow_that_comes_after_its_image_is_asked_again_up_to_three_times(sim_clock: SimClock) -> None:
    server = FakeVigicrues(recorded_payload())
    collector = VigicruesCollector(http_client=server.provider.client())
    await collector.fetch_flow()
    server.flow_late = True
    server.publish()  # the image moved, the flow is still the old one
    reads = []
    for _ in range(5):
        sim_clock.advance(301)
        await collector.fetch_flow()
        reads.append(server.methods().count("GET flow"))
    assert reads == [2, 3, 4, 5, 5]  # the change and three tries read the flow, then the image is believed: back to looking at it only
    # ...and had the flow come after the second try, it would have been read.
    server2 = FakeVigicrues(with_levels(1))
    collector2 = VigicruesCollector(http_client=server2.provider.client())
    await collector2.fetch_flow()
    server2.flow_late = True
    server2.publish()
    sim_clock.advance(901)
    await collector2.fetch_flow()  # too early: the same flow
    server2.flow_late = False
    server2.payload = calm_but("GA32", 3)
    server2.publish(server2.payload)
    sim_clock.advance(901)
    assert [v.id for v in (await collector2.fetch_flow()).value.vigilances] == ["vigicrues:GA32"]


@pytest.mark.asyncio
async def test_a_flow_that_is_late_is_read_even_if_the_image_goes_back_to_a_value_seen_before(sim_clock: SimClock) -> None:
    server = FakeVigicrues(with_levels(1))
    collector = VigicruesCollector(http_client=server.provider.client())
    await collector.fetch_flow()
    first_image = dict(server.image or {})
    server.flow_late = True
    server.publish()  # the image moved, the flow did not
    sim_clock.advance(901)
    await collector.fetch_flow()
    server.image = first_image  # and the image is the very one of the first read again, with a flow that did change
    server.flow_late = False
    server.payload = calm_but("GA32", 3)
    server.payload["RefInfoVigiCru"], server.payload["DtHrInfoVigiCru"] = "20102026_18", "2026-10-20T15:55:00+00:00"
    sim_clock.advance(901)
    assert [v.id for v in (await collector.fetch_flow()).value.vigilances] == ["vigicrues:GA32"]


@pytest.mark.asyncio
@pytest.mark.parametrize("image", [None, {}], ids=["image gone", "image without validator"])
async def test_without_a_usable_image_the_flow_itself_is_read_every_hour_or_twenty_minutes(sim_clock: SimClock, image: Optional[dict[str, str]]) -> None:
    calm = FakeVigicrues(with_levels(1))
    calm.image = image
    collector = VigicruesCollector(http_client=calm.provider.client())
    await collector.fetch_flow()
    sim_clock.advance(3599)
    await collector.fetch_flow()
    assert calm.methods() == ["HEAD image", "GET flow"]
    sim_clock.advance(2)
    await collector.fetch_flow()
    assert calm.methods() == ["HEAD image", "GET flow", "HEAD image", "GET flow"]

    flood = FakeVigicrues(recorded_payload())
    flood.image = image
    collector = VigicruesCollector(http_client=flood.provider.client())
    await collector.fetch_flow()
    sim_clock.advance(1201)
    await collector.fetch_flow()
    assert flood.methods().count("GET flow") == 2


@pytest.mark.asyncio
async def test_an_image_that_comes_back_after_a_gap_is_followed_again(sim_clock: SimClock) -> None:
    server = FakeVigicrues(with_levels(1))
    server.image = None
    collector = VigicruesCollector(http_client=server.provider.client())
    await collector.fetch_flow()
    server.image = recorded_image_headers()
    sim_clock.advance(3601)
    await collector.fetch_flow()  # reads the flow and learns the image
    sim_clock.advance(901)
    await collector.fetch_flow()
    assert server.methods()[-1] == "HEAD image" and server.methods().count("GET flow") == 2


@pytest.mark.asyncio
async def test_a_failing_server_falls_back_on_the_last_flow_and_says_so(sim_clock: SimClock) -> None:
    server = FakeVigicrues(recorded_payload())
    collector = VigicruesCollector(http_client=server.provider.client())
    await collector.fetch_flow()
    server.down = True
    sim_clock.advance(301)
    fetched: Fetched[Any] = await collector.fetch_flow()
    assert fetched.stale is True and len(fetched.value.vigilances) == 19
    assert server.methods() == ["HEAD image", "GET flow", "HEAD image"]  # the flow was not asked of a server that is down


def test_the_image_the_collector_looks_at_is_the_recorded_one() -> None:
    headers = recorded_image_headers()
    assert headers == {"Last-Modified": "Tue, 20 Oct 2026 07:55:36 GMT", "ETag": '"2f637-65e40f4aa8e00"'}
    # The recorded flow was published at 07:55:15 UTC: the image was regenerated 21 seconds later.
    assert samples.vigicrues_flow().updated_utc == 1_792_482_915


def test_the_collector_is_a_warnings_provider_with_its_cadence() -> None:
    assert (vigicrues.CHECK_CALM_S, vigicrues.CHECK_FLOOD_S) == (900.0, 300.0)
    assert vigicrues.SAFETY_S == 12 * HOUR
    assert (vigicrues.DEGRADED_CALM_S, vigicrues.DEGRADED_FLOOD_S) == (3600.0, 1200.0)
    collector = VigicruesCollector()
    assert collector.provider_id == "vigicrues_flow" and collector.domain == "warnings" and collector.ttl_seconds == 900
