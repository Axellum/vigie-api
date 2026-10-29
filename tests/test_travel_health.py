"""The ECDC's communicable disease threats for travellers (``GET /v1/travel/health``), on the feeds recorded on 2026-10-28."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.collectors.health import TTL_S, ECDCHealthCollector, HealthFeed, parse_ecdc, plain
from app.services.geoindex import default_countries
from app.services.health import ALIASES, HealthService, NameMatcher
from tests import health_samples as samples
from tests.helpers import SimClock


class EcdcHost:
    """The recorded ECDC host, and the status it gave to each request."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        response = samples.host(request)
        self.calls.append(response.status_code)
        return response


def collector(host: EcdcHost) -> ECDCHealthCollector:
    return ECDCHealthCollector(http_client=httpx.AsyncClient(transport=httpx.MockTransport(host)))


def test_each_feed_keeps_the_ecdc_words() -> None:
    items = parse_ecdc(samples.raw("ecdc_risk_assessments"), "risk_assessment")
    assert len(items) == 10
    first = items[0]
    assert first.title == (
        "Threat assessment brief – Importation of Ebola disease cases caused by Bundibugyo virus to Kenya and Nigeria"
    )
    assert first.url.startswith("https://www.ecdc.europa.eu/en/publications-data/threat-assessment-brief-importation-ebola")
    assert first.published_utc == 1792766483  # Fri, 23 Oct 2026 16:41:23 +0200
    assert first.summary is not None and first.summary.format == "html"
    assert first.summary.content.startswith("<p>This Threat Assessment Brief assesses the risk")
    assert first.countries is None and first.derived == []
    assert first.attribution.startswith("Source: European Centre for Disease Prevention and Control (ECDC)")
    for name, kind in (("ecdc_epidemiological_updates", "epidemiological_update"), ("ecdc_threats_reports", "threats_report")):
        assert len(parse_ecdc(samples.raw(name), kind)) == 10


def test_an_item_without_a_title_or_a_link_is_skipped_and_a_page_that_is_not_a_feed_raises() -> None:
    feed = b"<rss><channel><item><title>T</title></item><item><title>A</title><link>https://x</link></item></channel></rss>"
    assert [item.title for item in parse_ecdc(feed, "risk_assessment")] == ["A"]
    with pytest.raises(ValueError):
        parse_ecdc(b"<html><body>maintenance</body></html>", "risk_assessment")


def test_the_countries_named_are_matched_whole_and_longest_first() -> None:
    matcher = NameMatcher.for_countries(default_countries())
    assert matcher.countries("Importation of Ebola disease cases to Kenya and Nigeria") == ["KE", "NG"]
    assert matcher.countries("an outbreak in Niger") == ["NE"]
    assert matcher.countries("the Democratic Republic of the Congo (DRC)") == ["CD"]
    assert matcher.countries("Guinea-Bissau and Papua New Guinea") == ["GW", "PG"]
    assert matcher.countries("travellers returning from Cabo Verde") == ["CV"]
    assert matcher.countries("EU/EEA countries, worldwide") == []
    assert matcher.countries("in the kenyan highlands") == []  # a name is matched with its capitals
    assert set(ALIASES.values()) <= set(default_countries().countries())


@pytest.mark.asyncio
async def test_the_feeds_are_asked_conditionally_and_a_304_keeps_the_items(sim_clock: SimClock) -> None:
    host = EcdcHost()
    source = collector(host)
    first: HealthFeed = await source.fetch_feed()
    sim_clock.advance(TTL_S + 1)
    second: HealthFeed = await source.fetch_feed()
    assert host.calls == [200, 200, 200, 304, 304, 304]
    assert len(first.items) == 30
    assert [item.title for item in second.items] == [item.title for item in first.items]
    assert first.items[0].published_utc >= first.items[-1].published_utc  # newest first


def test_the_answer_names_the_countries_as_ours_and_links_the_who_and_the_cdc() -> None:
    service = HealthService(collector(EcdcHost()), default_countries())
    answer = asyncio.run(service.answer())
    assert len(answer.items) == 30
    assert all(item.derived == ["countries"] for item in answer.items)
    brief = next(item for item in answer.items if item.title.startswith("Threat assessment brief – Importation of Ebola"))
    assert brief.countries == ["KE", "NG"]
    assert [(agency.id, agency.read) for agency in answer.agencies] == [("ecdc", True), ("who", False), ("cdc", False)]
    ecdc, who, cdc = answer.agencies
    assert ecdc.state == "ok" and ecdc.items == 30 and ecdc.commercial_use == "allowed"
    assert who.why_not_read and "commercial" in who.why_not_read and who.commercial_use is None
    assert cdc.why_not_read and cdc.url == "https://wwwnc.cdc.gov/travel/notices"
    assert answer.attribution and "CC BY 4.0" in answer.attribution

    kenya = asyncio.run(service.answer("KE"))
    assert [item.title for item in kenya.items] == [brief.title]
    assert kenya.country == "KE"
    assert asyncio.run(service.answer("IS")).items == []


def test_a_summary_is_searched_without_its_tags() -> None:
    assert plain("<p>Since May 2026, an outbreak in the Democratic Republic of the Congo&nbsp;(DRC).</p>") == (
        "Since May 2026, an outbreak in the Democratic Republic of the Congo (DRC)."
    )
