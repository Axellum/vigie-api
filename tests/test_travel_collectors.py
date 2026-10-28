"""The travel advice collectors, played against the answers recorded on 2026-10-28 (``tests/fixtures/travel``)."""

from __future__ import annotations

import gzip
import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app.attribution import credit_text
from app.collectors import travel as t
from app.collectors import travel_codes as codes
from app.collectors.base import CollectorUnavailableError
from app.services.travel_store import TravelStore
from tests import travel_samples as samples
from tests.helpers import SimClock

NOW = 1_793_220_000


def json_date(text: str) -> int:
    from app.collectors.base import parse_iso_utc

    value = parse_iso_utc(text)
    assert value is not None
    return value


def mock(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def one(feed: t.TravelFeed, country: str) -> t.TravelAdvice:
    [advice] = [a for a in feed.advices if a.country == country]
    return advice


# --- the codes ---------------------------------------------------------------------------------------------------------


def test_the_alpha3_table_is_m49_and_the_two_codes_it_lacks() -> None:
    assert len(codes.ALPHA3_TO_ALPHA2) == 250
    assert codes.ALPHA3_TO_ALPHA2["FRA"] == "FR" and codes.ALPHA3_TO_ALPHA2["NAM"] == "NA"
    assert codes.ALPHA3_TO_ALPHA2["TWN"] == "TW" and codes.ALPHA3_TO_ALPHA2["XKX"] == "XK"
    assert len(set(codes.ALPHA3_TO_ALPHA2.values())) == 250  # one alpha-2 for each alpha-3


def test_every_page_of_the_recorded_indexes_is_placed() -> None:
    entries = t.parse_gb_index(samples.payload("gb_index"))
    assert len(entries) == 226 and all(codes.gb_countries(e.page) for e in entries)
    assert codes.gb_countries("cook-islands-tokelau-and-niue") == ("CK", "TK", "NU")
    assert codes.gb_countries("st-martin-and-st-barthelemy") == ("MF", "BL")
    assert codes.gb_countries("atlantis") == ()


def test_the_codes_of_each_ministry() -> None:
    assert codes.us_countries("destination.irq") == ("IQ",)  # not FIPS "IZ"
    assert codes.us_countries("destination.gbr") == ("GB",)  # not FIPS "UK"
    assert codes.us_countries("destination.xgz") == ("PS",) and codes.us_countries("destination.xks") == ("XK",)
    assert codes.us_countries("french-west-indies-travel-advisory") == ()
    assert codes.us_countries("destination.zzz") == () and codes.us_countries("somewhere") == ()
    assert codes.ca_countries("MX") == ("MX",) and codes.ca_countries("PT-20") == ("PT",) and codes.ca_countries("IC") == ("ES",)
    assert codes.ca_countries("mx") == () and codes.ca_countries("XYZ") == ()
    assert codes.nl_countries("MEX") == ("MX",) and codes.nl_countries("XKX") == ("XK",) and codes.nl_countries("BQ-SA") == ("BQ",)
    assert codes.nl_countries("ZZZ") == ()
    assert codes.au_countries("turkiye") == ("TR",) and codes.au_countries("crisis-alert") == ()


# --- United States -----------------------------------------------------------------------------------------------------


def test_the_department_of_state_answer_is_read_whole() -> None:
    feed = t.parse_us(samples.payload("us_travel_advisories"), NOW)
    assert len(feed.advices) == 216 and feed.skipped == 0
    assert feed.unplaced == ("French West Indies",)  # an older page beside the four that have their own
    assert Counter(a.level for a in feed.advices) == {1: 83, 2: 82, 3: 27, 4: 24}
    iraq = one(feed, "IQ")
    assert iraq.title == "Iraq" and iraq.level == 4 and iraq.level_texts == {"en": "Level 4: Do Not Travel"}
    assert (iraq.common_min, iraq.common_max) == (4, 4) and iraq.regional is None
    assert iraq.source_code == "destination.irq" and iraq.url.endswith("destination.irq.html")
    assert iraq.published_utc == 1792713600 and iraq.checked_utc == NOW  # 2026-10-22T20:00:00-04:00
    assert iraq.summary[0].format == "html" and iraq.summary[0].content.startswith("Do not travel<p>to <b>Iraq </b>")
    assert iraq.derived == ["country", "common_min", "common_max"] and iraq.attribution == credit_text("travel_us")


def test_a_country_with_two_american_pages_keeps_both_titles_and_one_page_per_title() -> None:
    feed = t.parse_us(samples.payload("us_travel_advisories"), NOW)
    palestine = sorted((a.title, a.level) for a in feed.advices if a.country == "PS")
    assert palestine == [("Gaza", 4), ("The West Bank", 3)]
    assert [a.source_code for a in feed.advices if a.country == "TO"] == ["destination.ton"]  # the newer of the two Tonga pages


def test_an_american_title_without_a_level_is_skipped_and_a_list_of_nothing_readable_raises() -> None:
    item = {"Title": "Iraq - Travel Advisory", "Link": "https://travel.state.gov/x/destination.irq.html", "Updated": "2026-10-22T20:00:00-04:00"}
    good = {"Title": "France - Level 2: Exercise Increased Caution", "Link": "https://travel.state.gov/x/destination.fra.html"}
    assert t.parse_us([item, good], NOW).skipped == 1
    with pytest.raises(ValueError):
        t.parse_us([item], NOW)
    with pytest.raises(ValueError):
        t.parse_us({"not": "a list"}, NOW)


# --- Canada ------------------------------------------------------------------------------------------------------------


def test_the_canadian_index_gives_both_languages_and_the_regional_flag() -> None:
    feed = t.parse_ca_index(samples.payload("ca_index_updated"), NOW)
    assert len(feed.advices) == 230 and not feed.unplaced and feed.skipped == 0
    mexico = one(feed, "MX")
    assert mexico.level == 2 and mexico.regional is True
    assert mexico.level_texts == {
        "en": "Exercise a high degree of caution (with regional advisories)",
        "fr": "Faire preuve d’une grande prudence (avec avertissements régionaux)",
    }
    assert mexico.url == "https://travel.gc.ca/destinations/mexico" and mexico.published_utc == 1792776397
    assert [n.language for n in mexico.change_note] == ["en", "fr"] and mexico.summary == []
    # Two parts of a country have advices of their own: the Azores (PT-20) and the Canary Islands (IC).
    assert sorted(a.title for a in feed.advices if a.country == "PT") == ["Azores", "Portugal"]
    assert sorted(a.title for a in feed.advices if a.country == "ES") == ["Canary Islands", "Spain"]
    afghanistan = one(feed, "AF")
    assert afghanistan.level == 4 and afghanistan.level_texts["en"] == "Avoid all travel"


def test_the_canadian_text_of_one_destination_is_kept_in_both_languages() -> None:
    texts = t.parse_ca_detail(samples.payload("ca_cta_cap_mx"))
    assert [(x.language, x.format) for x in texts] == [("en", "html"), ("fr", "html")]
    assert texts[0].content.startswith('<div class="AdvisoryContainer ExHigh"><h3>Mexico - Exercise a high degree of caution</h3>')


@pytest.mark.asyncio
async def test_the_canadian_index_is_asked_conditionally_and_a_304_confirms_it(sim_clock: SimClock) -> None:
    seen: list[dict[str, str]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.headers))
        if request.headers.get("If-None-Match") == samples.headers("ca_index_updated")["etag"]:
            return httpx.Response(304)
        return httpx.Response(200, content=samples.raw("ca_index_updated"), headers=samples.headers("ca_index_updated"))

    collector = t.CanadaTravelCollector(http_client=mock(answer))
    first = await collector.fetch_feed()
    sim_clock.advance(t.TTL_S + 1)
    second = await collector.fetch_feed()
    assert len(seen) == 2 and "If-None-Match" not in seen[0] and seen[1]["if-none-match"] == '"3486688b1c67dd1:0"'
    assert len(second.advices) == len(first.advices)
    assert one(second, "MX").checked_utc == one(first, "MX").checked_utc + t.TTL_S + 1  # confirmed by the 304


@pytest.mark.asyncio
async def test_the_canadian_text_is_read_only_when_asked_and_cached(sim_clock: SimClock) -> None:
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        return samples.hosts()["data.international.gc.ca"](request)

    collector = t.CanadaTravelCollector(http_client=mock(answer))
    await collector.fetch_feed()
    assert asked == ["/travel-voyage/index-updated.json"]
    texts = await collector.fetch_detail("MX")
    await collector.fetch_detail("MX")
    assert asked[1:] == ["/travel-voyage/cta-cap-mx.json"] and len(texts) == 2
    with pytest.raises(ValueError):
        await collector.fetch_detail("../index")


# --- Australia ---------------------------------------------------------------------------------------------------------


def test_smartraveller_levels_are_its_four_words_and_its_dates_are_kept_as_written() -> None:
    feed = t.parse_au(samples.payload("au_destinations_export"), NOW)
    assert len(feed.advices) == 179 and not feed.unplaced and feed.skipped == 0  # "Crisis alert" and "No travel advice" are no destination
    assert Counter(a.level for a in feed.advices) == {2: 78, 1: 64, 4: 25, 3: 12}
    ivory = one(feed, "CI")
    assert ivory.title == "Côte d'Ivoire (Ivory Coast)"  # the export writes it Côte d&#039;Ivoire
    mexico = one(feed, "MX")
    assert mexico.level_texts == {"en": "Exercise a high degree of caution"} and mexico.level == 2
    assert mexico.published_utc is None and mexico.published_text == "Thu, 2026-10-15 17:08"  # no time zone given
    assert mexico.summary[0].format == "html" and mexico.change_note[0].content.startswith("We continue to advise")
    assert mexico.url == "https://www.smartraveller.gov.au/destinations/americas/mexico"


def test_a_smartraveller_level_that_is_not_one_of_its_four_is_unknown() -> None:
    item = {"title": "Mexico", "field_url": "https://www.smartraveller.gov.au/destinations/americas/mexico", "field_overall_advice_level": "Be careful"}
    advice = one(t.parse_au([item], NOW), "MX")
    assert advice.level is None and advice.common_min is None and advice.level_texts == {"en": "Be careful"}


@pytest.mark.asyncio
async def test_smartraveller_is_asked_conditionally(sim_clock: SimClock) -> None:
    calls: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.headers.get("If-None-Match"):
            return httpx.Response(304)
        return samples.hosts()["www.smartraveller.gov.au"](request)

    collector = t.SmartravellerCollector(http_client=mock(answer))
    await collector.fetch_feed()
    sim_clock.advance(t.TTL_S + 1)
    feed = await collector.fetch_feed()
    assert len(calls) == 2 and calls[1].headers["If-None-Match"] == '"1793220303"' and len(feed.advices) == 179


@pytest.mark.asyncio
async def test_smartraveller_is_sent_a_user_agent_without_an_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """Its server holds a request whose User-Agent has a URL or an e-mail until it times out (2026-10-28): the contact is not sent."""
    monkeypatch.setenv("VIGIE_CONTACT", "https://example.org/contact")
    agents: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        agents.append(request.headers["User-Agent"])
        return samples.hosts()["www.smartraveller.gov.au"](request)

    collector = t.SmartravellerCollector()
    real_client = httpx.AsyncClient(transport=httpx.MockTransport(answer), headers={"User-Agent": "vigie-api (https://example.org/contact)"})
    collector._external_client = real_client
    await collector.fetch_feed()
    assert agents == ["vigie-api"]


# --- The Netherlands ---------------------------------------------------------------------------------------------------


def test_the_dutch_colour_is_read_only_when_the_summary_names_one() -> None:
    assert t.nl_level("<p>De kleurcode van het reisadvies voor Réunion is groen. U kunt erheen reizen.</p>") == (1, False)
    assert t.nl_level("<p>De kleurcode voor het reisadvies voor Zuid-Afrika is geel.</p>") == (2, False)
    mexico = "<li>De kleurcode van het reisadvies is rood voor de actieve vulkanen.</li><li>Kleurcode oranje geldt voor meerdere deelstaten.</li>"
    assert t.nl_level(mexico) == (None, True)
    assert t.nl_level("<p>Geel is een kleur, maar hier staat geen zin over de kleurcode.</p>") == (None, False)
    assert t.nl_level("<p>Lees welke veiligheidsrisico's er zijn.</p>") == (None, None)


def test_the_dutch_feed_is_read_whole() -> None:
    feed = t.parse_nl(samples.nl_items(), NOW)
    assert len(feed.advices) == 226 and not feed.unplaced and feed.skipped == 0
    assert Counter(a.level for a in feed.advices) == {1: 81, None: 71, 2: 57, 4: 15, 3: 2}
    reunion = one(feed, "RE")
    assert reunion.level == 1 and reunion.level_texts == {"nl": "groen"} and reunion.regional is False
    assert "level" in reunion.derived and reunion.summary[0].language == "nl"
    mexico = one(feed, "MX")
    assert mexico.level is None and mexico.common_min is None and mexico.regional is True
    assert sorted(a.title for a in feed.advices if a.country == "BQ") == ["Bonaire", "Saba", "Sint Eustatius"]
    assert one(feed, "XK").title == "Kosovo"


@pytest.mark.asyncio
async def test_the_dutch_feed_takes_two_answers_of_200() -> None:
    offsets: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        offsets.append(request.url.params["offset"])
        assert request.url.params["rows"] == "200" and request.url.params["output"] == "json"
        return samples.hosts()["opendata.nederlandwereldwijd.nl"](request)

    feed = await t.NetherlandsTravelCollector(http_client=mock(answer)).fetch_feed()
    assert offsets == ["0", "200"] and len(feed.advices) == 226


# --- United Kingdom ----------------------------------------------------------------------------------------------------


def test_the_fcdo_statuses_give_the_common_scale() -> None:
    assert t.gb_common(()) == (1, 2, False)
    assert t.gb_common(("avoid_all_but_essential_travel_to_parts",)) == (1, 3, True)
    assert t.gb_common(("avoid_all_travel_to_parts", "avoid_all_but_essential_travel_to_parts")) == (1, 4, True)
    assert t.gb_common(("avoid_all_but_essential_travel_to_whole_country",)) == (3, 3, False)
    assert t.gb_common(("avoid_all_travel_to_whole_country", "avoid_all_travel_to_parts")) == (4, 4, True)
    assert t.gb_common(("avoid_travel_on_tuesdays",)) == (None, None, None)


def test_an_fcdo_page_keeps_its_statuses_its_warnings_and_its_history() -> None:
    page = t.parse_gb_page(samples.gb_page("ukraine"), "ukraine", NOW)
    assert page.statuses == ("avoid_all_travel_to_parts", "avoid_all_but_essential_travel_to_parts")
    assert page.warnings_title == "Warnings and insurance" and page.warnings_html is not None
    assert len(page.history) == 181 and all(note for _, note in page.history)
    [advice] = t.gb_advices(page, NOW)
    assert advice.country == "UA" and advice.level is None and advice.statuses == list(page.statuses)
    assert (advice.common_min, advice.common_max, advice.regional) == (1, 4, True)
    assert advice.url == "https://www.gov.uk/foreign-travel-advice/ukraine" and len(advice.source_history) == 181
    assert t.GbPage.from_json(page.to_json()) == page


def test_an_fcdo_page_for_three_territories_is_the_advice_of_each() -> None:
    page = t.parse_gb_page(samples.gb_page("cook-islands-tokelau-and-niue"), "cook-islands-tokelau-and-niue", NOW)
    assert [a.country for a in t.gb_advices(page, NOW)] == ["CK", "TK", "NU"]
    russia = t.gb_advices(t.parse_gb_page(samples.gb_page("russia"), "russia", NOW), NOW)[0]
    assert (russia.common_min, russia.common_max) == (4, 4)


def fcdo_host(pages: tuple[str, ...], reads: list[str], fail: tuple[str, ...] = ()) -> Callable[[httpx.Request], httpx.Response]:
    index = json.dumps(samples.gb_index(pages)).encode()

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/content/foreign-travel-advice":
            if request.headers.get("If-None-Match") == "index-1":
                return httpx.Response(304)
            return httpx.Response(200, content=index, headers={"ETag": "index-1"})
        page = request.url.path.rsplit("/", 1)[-1]
        reads.append(page)
        if page in fail:
            return httpx.Response(503)
        return httpx.Response(200, content=samples.raw(samples.GB_RECORDED[page]))

    return answer


@pytest.mark.asyncio
async def test_the_fcdo_pages_are_read_once_and_survive_a_restart(tmp_path: Path, sim_clock: SimClock) -> None:
    pages = ("france", "mexico", "russia")
    store = TravelStore(tmp_path / "travel.db")
    reads: list[str] = []
    collector = t.FCDOTravelCollector(store, http_client=mock(fcdo_host(pages, reads)), page_spacing_s=0)
    feed = await collector.fetch_feed()
    assert sorted(reads) == sorted(pages) and {a.country for a in feed.advices} == {"FR", "MX", "RU"}
    sim_clock.advance(t.TTL_S + 1)
    await collector.fetch_feed()  # the index answers 304: no page is read again
    assert len(reads) == 3
    restarted = t.FCDOTravelCollector(store, http_client=mock(fcdo_host(pages, reads)), page_spacing_s=0)
    feed = await restarted.fetch_feed()
    assert len(reads) == 3 and len(feed.advices) == 3  # the pages kept in the database are still the latest version
    assert one(feed, "MX").statuses == ["avoid_all_but_essential_travel_to_parts"]


@pytest.mark.asyncio
async def test_an_fcdo_page_with_a_newer_version_is_read_again(tmp_path: Path, sim_clock: SimClock) -> None:
    store = TravelStore(tmp_path / "travel.db")
    reads: list[str] = []
    collector = t.FCDOTravelCollector(store, http_client=mock(fcdo_host(("france", "cuba"), reads)), page_spacing_s=0)
    await collector.fetch_feed()
    index = samples.gb_index(("france", "cuba"))
    for child in index["links"]["children"]:
        if child["details"]["country"]["slug"] == "cuba":
            child["public_updated_at"] = "2026-10-29T09:00:00Z"
    newer = json.dumps(index).encode()

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/content/foreign-travel-advice":
            return httpx.Response(200, content=newer)
        reads.append(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, content=samples.raw("gb_cuba"))

    sim_clock.advance(t.TTL_S + 1)
    collector._external_client = mock(answer)
    feed = await collector.fetch_feed()
    assert reads == ["france", "cuba", "cuba"]
    # The page served still carries its old date (the recording): it is kept and said to be pending, and asked again next time.
    assert feed.pending == ("Cuba",) and one(feed, "CU").published_utc != json_date("2026-10-29T09:00:00Z")


@pytest.mark.asyncio
async def test_an_fcdo_page_that_fails_is_pending_and_the_others_are_served(tmp_path: Path) -> None:
    reads: list[str] = []
    collector = t.FCDOTravelCollector(TravelStore(tmp_path / "travel.db"), http_client=mock(fcdo_host(("france", "cuba"), reads, fail=("cuba",))), page_spacing_s=0)
    feed = await collector.fetch_feed()
    assert [a.country for a in feed.advices] == ["FR"] and feed.pending == ("Cuba",)


@pytest.mark.asyncio
async def test_without_the_fcdo_index_the_kept_pages_are_still_there(tmp_path: Path) -> None:
    store = TravelStore(tmp_path / "travel.db")
    reads: list[str] = []
    await t.FCDOTravelCollector(store, http_client=mock(fcdo_host(("france",), reads)), page_spacing_s=0).fetch_feed()
    down = t.FCDOTravelCollector(store, http_client=mock(lambda r: httpx.Response(503)), page_spacing_s=0)
    with pytest.raises(CollectorUnavailableError):
        await down.fetch_feed()
    kept = down.kept_feed()
    assert kept is not None and [a.country for a in kept.advices] == ["FR"] and kept.advices[0].checked_utc is not None


@pytest.mark.asyncio
async def test_the_fcdo_pages_are_read_one_after_the_other(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pauses: list[float] = []

    async def pause(seconds: float) -> None:
        pauses.append(seconds)

    monkeypatch.setattr(t.asyncio, "sleep", pause)
    reads: list[str] = []
    collector = t.FCDOTravelCollector(None, http_client=mock(fcdo_host(("france", "mexico", "cuba"), reads)))
    await collector.fetch_feed()
    assert pauses == [t.GB_PAGE_SPACING_S, t.GB_PAGE_SPACING_S] and 1 / t.GB_PAGE_SPACING_S <= 10 * 0.9
    assert collector.budget.quota is not None and collector.budget.quota.limits[0].calls == 10


def test_the_recorded_answers_are_the_bytes_sent() -> None:
    for path in sorted(samples.FIXTURES.glob("*.json.gz")):
        json.loads(gzip.decompress(path.read_bytes()))  # every recording is whole JSON
        assert (path.parent / path.name.replace(".json.gz", ".headers.txt")).exists()
