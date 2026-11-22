"""``GET /v1/travel`` and ``GET /v1/travel/{country}``: the ministries' levels, the comparison (ours) and the archive of the versions."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app.collectors import travel as collectors
from app.collectors.health import ECDCHealthCollector
from app.models.travel import TravelLevel
from app.services import travel as service_module
from app.services.geoindex import default_countries
from app.services.health import HealthService
from app.services.travel import TravelService, compare
from app.services.travel_store import TravelStore
from tests import health_samples
from tests import travel_samples as samples
from tests.conftest import World
from tests.helpers import SimClock, make_service


def recorded_hosts(gb_pages: tuple[str, ...] = tuple(samples.GB_RECORDED)) -> Callable[[httpx.Request], httpx.Response]:
    """Every ministry as recorded; the FCDO index lists only the pages that were recorded."""
    hosts = samples.hosts()
    index = json.dumps(samples.gb_index(gb_pages)).encode()
    pages = hosts["www.gov.uk"]

    def gb(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/content/foreign-travel-advice":
            return httpx.Response(200, content=index)
        return pages(request)

    hosts["www.gov.uk"] = gb
    hosts["www.ecdc.europa.eu"] = health_samples.host
    return lambda request: hosts[request.url.host](request)


def make_travel(
    tmp_path: Path,
    answer: Callable[[httpx.Request], httpx.Response] | None = None,
    off: dict[str, str] | None = None,
) -> TravelService:
    client = httpx.AsyncClient(transport=httpx.MockTransport(answer or recorded_hosts()))
    store = TravelStore(tmp_path / "travel.db")
    sources = {
        "gb": collectors.FCDOTravelCollector(store, http_client=client, page_spacing_s=0),
        "us": collectors.USTravelCollector(http_client=client),
        "ca": collectors.CanadaTravelCollector(http_client=client),
        "au": collectors.SmartravellerCollector(http_client=client),
        "nl": collectors.NetherlandsTravelCollector(http_client=client),
    }
    for ministry in off or {}:
        del sources[ministry]  # switched off: not read at all
    health = HealthService(ECDCHealthCollector(http_client=client), default_countries())
    return TravelService(sources, default_countries(), store, health=health, off=off)


def level(ministry: str, low: int | None, high: int | None, part: bool = False) -> TravelLevel:
    return TravelLevel(ministry=ministry, title="X", part=part, common_min=low, common_max=high)  # type: ignore[arg-type]


# --- the comparison (ours) ---------------------------------------------------------------------------------------------


def test_ministries_disagree_only_when_their_ranges_cannot_meet() -> None:
    agree = compare([level("us", 2, 2), level("gb", 1, 2), level("au", 2, 2)])
    assert (agree.disagree, agree.gap, agree.strictest) == (False, 0, [])
    split = compare([level("us", 4, 4), level("ca", 2, 2), level("gb", 1, 2), level("au", 3, 3)])
    assert (split.disagree, split.gap, split.strictest, split.laxest) == (True, 2, ["us"], ["ca", "gb"])
    assert split.compared == ["au", "ca", "gb", "us"] and "disagree" in split.derived


def test_a_part_of_a_country_an_unknown_level_or_a_single_ministry_is_not_compared() -> None:
    one = compare([level("us", 4, 4, part=True), level("ca", 1, 1), level("nl", None, None)])
    assert one.compared == ["ca"] and one.disagree is None and one.gap is None
    # The FCDO's statuses for parts leave the rest of the country open: up to 4, so it can meet a "do not travel".
    assert compare([level("us", 4, 4), level("gb", 1, 4)]).disagree is False


# --- the overview ------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_overview_has_every_ministry_and_says_what_is_ours(tmp_path: Path) -> None:
    overview = await make_travel(tmp_path).overview()
    states = {m.id: (m.read, m.state) for m in overview.ministries}
    assert states == {"gb": (True, "ok"), "us": (True, "ok"), "ca": (True, "ok"), "au": (True, "ok"), "nl": (True, "ok"),
                      "de": (False, None), "fr": (False, None)}
    germany = next(m for m in overview.ministries if m.id == "de")
    assert germany.why_not_read and germany.url.startswith("https://www.auswaertiges-amt.de/")
    us = next(m for m in overview.ministries if m.id == "us")
    assert [s.words["en"] for s in us.levels][-1] == "Level 4: Do Not Travel" and us.commercial_use == "allowed"
    assert next(m for m in overview.ministries if m.id == "au").commercial_use == "not_verified"
    assert overview.common_scale[4] == "avoid all travel" and "not an official level" in overview.notice
    france = next(c for c in overview.countries if c.country == "FR")
    assert france.name == "France" and sorted(a.ministry for a in france.advices) == ["au", "ca", "gb", "nl", "us"]
    assert all("Source:" in part for part in (overview.attribution or "").split(" | "))


@pytest.mark.asyncio
async def test_the_overview_of_the_recorded_day_flags_the_disagreements(tmp_path: Path) -> None:
    overview = await make_travel(tmp_path).overview()
    by_country = {c.country: c for c in overview.countries}
    russia = by_country["RU"].comparison
    assert russia.disagree is False and russia.compared == ["au", "ca", "gb", "us"]  # the Dutch summary names several colours
    france = by_country["FR"]
    levels = {a.ministry: (a.common_min, a.common_max) for a in france.advices}
    assert levels["us"] == levels["ca"] == levels["au"] == (2, 2) and levels["gb"] == (1, 2) and france.comparison.disagree is False
    # Uganda, on 2026-10-28: "Level 4: Do Not Travel" for the Department of State, "Avoid non-essential travel" for Canada, a high
    # degree of caution for Smartraveller and yellow for the Netherlands: two steps apart.
    uganda = by_country["UG"].comparison
    assert (uganda.disagree, uganda.gap, uganda.strictest, uganda.laxest) == (True, 2, ["us"], ["au", "nl"])
    assert sum(1 for c in overview.countries if c.comparison.disagree) == 66
    palestine = by_country["PS"]
    assert {a.title for a in palestine.advices if a.ministry == "us"} == {"Gaza", "The West Bank"}
    assert "us" not in palestine.comparison.compared  # two parts, no advice for the whole country


@pytest.mark.asyncio
async def test_the_overview_stays_small(tmp_path: Path) -> None:
    overview = await make_travel(tmp_path).overview()
    size = len(overview.model_dump_json().encode())
    assert size < 256_000, size  # no texts: the levels of about 1,000 advices (224 kB on the recorded day)


# --- one country -------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_country_has_every_text_untouched_and_the_canadian_one_is_read_then(tmp_path: Path) -> None:
    detail = await make_travel(tmp_path).country("MX")
    by_ministry = {a.ministry: a for a in detail.advices}
    assert sorted(by_ministry) == ["au", "ca", "gb", "nl", "us"] and detail.not_covered == []
    assert [t.language for t in by_ministry["ca"].summary] == ["en", "fr"]  # read now, from cta-cap-mx.json
    assert by_ministry["gb"].summary[0].title == "Warnings and insurance" and by_ministry["gb"].source_history
    assert by_ministry["nl"].regional is True and by_ministry["nl"].level is None
    assert detail.name == "Mexico" and detail.attribution and "Open Government Licence" in detail.attribution
    assert {c.ministry for c in detail.history} == {"au", "ca", "gb", "nl", "us"} and all(c.first for c in detail.history)
    assert detail.history_since_utc is not None


@pytest.mark.asyncio
async def test_a_country_no_ministry_covers_answers_with_nothing_and_says_so(tmp_path: Path) -> None:
    detail = await make_travel(tmp_path).country("ZZ")
    assert detail.advices == [] and detail.not_covered == ["au", "ca", "gb", "nl", "us"] and detail.name is None
    assert detail.comparison.compared == [] and detail.history == []


# --- the archive -------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_new_level_is_archived_and_a_feed_read_again_adds_nothing(tmp_path: Path, sim_clock: SimClock) -> None:
    travel = make_travel(tmp_path)
    await travel.refresh()
    await travel.refresh()  # the same cached feeds: nothing new
    first = travel.history("MX")
    assert len(first) == 5 and all(change.first for change in first)

    changed = samples.payload("us_travel_advisories")
    for item in changed:
        if item["Link"].endswith("destination.mex.html"):
            item["Title"] = "Mexico Travel Advisory - Level 3: Reconsider Travel"
            item["Updated"] = "2026-10-30T12:00:00-04:00"
    hosts = recorded_hosts()
    travel.sources["us"]._external_client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=changed) if r.url.host == "cadataapi.state.gov" else hosts(r))
    )
    sim_clock.advance(collectors.TTL_S + 1)
    await travel.refresh()
    history = travel.history("MX")
    assert len(history) == 6
    newest = history[0]
    assert newest.ministry == "us" and newest.level == 3 and newest.first is False and newest.level_changed is True
    assert newest.level_texts == {"en": "Level 3: Reconsider Travel"}
    # A new instance on the same database knows the versions already archived.
    again = make_travel(tmp_path)
    await again.refresh()
    assert len(again.history("MX")) == 7  # the recorded Mexico of the Department of State is a new version again (level 2)


# --- a ministry that is slow or down -----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_ministry_still_loading_does_not_hold_the_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module, "WAIT_S", 0.05)
    hosts = recorded_hosts()
    release = asyncio.Event()

    async def slow_gb(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.gov.uk":
            await release.wait()
        return hosts(request)

    travel = make_travel(tmp_path, slow_gb)  # type: ignore[arg-type]
    overview = await travel.overview()
    gb = next(m for m in overview.ministries if m.id == "gb")
    assert gb.state == "loading" and gb.advices == 0
    assert all(a.ministry != "gb" for c in overview.countries for a in c.advices)
    release.set()
    await asyncio.sleep(0.05)
    overview = await travel.overview()
    assert next(m for m in overview.ministries if m.id == "gb").state == "ok"


@pytest.mark.asyncio
async def test_a_ministry_that_times_out_every_time_says_unavailable_not_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Smartraveller seen on 2026-11-01: each request waits longer than the answer does, then times out.
    monkeypatch.setattr(service_module, "WAIT_S", 0.05)
    hosts = recorded_hosts()
    release = asyncio.Event()
    answer = False

    async def slow_au(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.smartraveller.gov.au":
            await release.wait()
            if not answer:
                raise httpx.ReadTimeout("", request=request)
        return hosts(request)

    def au(overview):  # type: ignore[no-untyped-def]
        return next(m for m in overview.ministries if m.id == "au")

    travel = make_travel(tmp_path, slow_au)  # type: ignore[arg-type]
    assert au(await travel.overview()).state == "loading"  # the first read is running
    release.set()
    await asyncio.sleep(0.05)  # it times out
    release.clear()
    overview = await travel.overview()  # a new read is running, after one that failed
    assert au(overview).state == "unavailable" and au(overview).advices == 0
    assert all(a.ministry != "au" for c in overview.countries for a in c.advices)
    answer = True
    release.set()
    await asyncio.sleep(0.05)
    assert au(await travel.overview()).state == "ok"


@pytest.mark.asyncio
async def test_without_the_fcdo_its_kept_pages_are_served_and_said_to_be_kept(tmp_path: Path) -> None:
    await make_travel(tmp_path).overview()
    hosts = recorded_hosts()
    down = make_travel(tmp_path, lambda r: httpx.Response(503) if r.url.host == "www.gov.uk" else hosts(r))
    overview = await down.overview()
    gb = next(m for m in overview.ministries if m.id == "gb")
    assert gb.state == "kept" and gb.advices == 9  # 7 pages, one for three territories


# --- the routes --------------------------------------------------------------------------------------------------------


def test_the_routes_answer_and_check_the_country(world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(world.aggregator, "travel", make_travel(tmp_path))
    overview = world.client.get("/v1/travel")
    assert overview.status_code == 200 and len(overview.json()["countries"]) > 200
    detail = world.client.get("/v1/travel/fr")
    assert detail.status_code == 200 and detail.json()["country"] == "FR"
    assert world.client.get("/v1/travel/FRA").status_code == 422
    assert world.client.get("/v1/travel/1A").status_code == 422
    health = world.client.get("/v1/travel/health")  # declared before /v1/travel/{country}: not a code refused
    assert health.status_code == 200 and len(health.json()["items"]) == 30
    assert [a["id"] for a in health.json()["agencies"]] == ["ecdc", "who", "cdc"]
    kenya = world.client.get("/v1/travel/KE").json()["health"]
    assert kenya["country"] == "KE" and [i["countries"] for i in kenya["items"]] == [["KE", "NG"]]
    assert kenya["items"][0]["derived"] == ["countries"]


# --- Smartraveller, off unless the instance turns it on -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "enabled"),
    [(None, False), ("", False), ("0", False), ("no", False), ("1", True), ("true", True), ("YES", True), (" on ", True)],
)
def test_smartraveller_switch(monkeypatch: pytest.MonkeyPatch, value: str | None, enabled: bool) -> None:
    if value is None:
        monkeypatch.delenv("VIGIE_ENABLE_SMARTRAVELLER", raising=False)
    else:
        monkeypatch.setenv("VIGIE_ENABLE_SMARTRAVELLER", value)
    assert collectors.smartraveller_enabled_from_env() is enabled


@pytest.mark.asyncio
async def test_a_ministry_switched_off_is_listed_as_not_read_and_never_asked(tmp_path: Path) -> None:
    hosts = recorded_hosts()
    asked: set[str] = set()

    def answer(request: httpx.Request) -> httpx.Response:
        asked.add(request.url.host)
        return hosts(request)

    travel = make_travel(tmp_path, answer, off={"au": collectors.SMARTRAVELLER_NOT_READ})
    overview = await travel.overview()
    au = next(m for m in overview.ministries if m.id == "au")
    assert (au.read, au.state, au.advices) == (False, None, None)
    assert au.why_not_read == collectors.SMARTRAVELLER_NOT_READ and au.url  # its pages stay linked
    assert au.attribution is None and au.commercial_use is None  # nothing of it is relayed
    assert sorted(m.id for m in overview.ministries if m.read) == ["ca", "gb", "nl", "us"]
    assert all(a.ministry != "au" for c in overview.countries for a in c.advices)
    detail = await travel.country("MX")
    assert sorted(a.ministry for a in detail.advices) == ["ca", "gb", "nl", "us"] and detail.not_covered == []
    assert "www.smartraveller.gov.au" not in asked and "www.gov.uk" in asked


def test_smartraveller_is_off_by_default(world: World) -> None:
    service = world.aggregator
    assert service.smartraveller_enabled is False
    assert service.travel_au not in service.get_all_collectors() and "au" not in service.travel.sources
    assert service.travel.off == {"au": collectors.SMARTRAVELLER_NOT_READ}
    health = world.client.get("/v1/health").json()
    off = next(s for s in health["disabled_sources"] if s["provider_id"] == "travel_au")
    assert off["domain"] == "travel" and "VIGIE_ENABLE_SMARTRAVELLER=1" in off["reason"]
    assert "travel_au" not in {p["provider_id"] for p in health["providers"]}


def test_an_instance_can_turn_smartraveller_back_on(tmp_path: Path) -> None:
    service = make_service(tmp_path / "au.db", smartraveller_enabled=True)
    assert service.travel_au in service.get_all_collectors() and service.travel.sources["au"] is service.travel_au
    assert service.travel.off == {} and "travel_au" not in {s["provider_id"] for s in service.disabled_sources()}
