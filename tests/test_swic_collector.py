"""Reading a service of the WMO registry: its feeds, the CAP documents they list, the WMO's copy, and what is asked how often.

The feeds and documents are the recorded ones of 2026-10-20 (``tests/fixtures/swic/``), played back by ``FixtureServer`` (nothing
touches the network). A test that builds a document or a feed says it is **synthetic**.
"""

from __future__ import annotations

import httpx
import pytest

from app.collectors import swic
from app.collectors.base import CollectorUnavailableError
from app.collectors.swic import SwicCollector, may_follow, parse_index
from app.collectors.swic_cap import CapError, instant, parse_alert
from app.collectors.swic_registry import SwicSource
from app.models.schemas import CircuitState
from tests import swic_samples as samples
from tests.helpers import SimClock
from tests.swic_samples import FixtureServer

RECORDED = samples.RECORDED_AT
MIRROR = "https://severeweather.wmo.int/v2/cap-alerts/{}/rss.xml"


def collector(server: FixtureServer) -> SwicCollector:
    return SwicCollector(http_client=server.client())


# ---------------------------------------------------------------------------------------------------- the indexes


@pytest.mark.parametrize("name", sorted(samples.MANIFEST["indexes"]))
def test_every_recorded_index_is_read(name: str) -> None:
    state = parse_index(samples.fixture(name), samples.index_url(name))
    expected = samples.MANIFEST["indexes"][name]["kept_entries"]
    if name == "index_jm_no_active.xml":
        # "There are no active watches, warnings or advisories": one entry, which links to the feed itself, so no CAP document.
        assert state.entries == 1 and state.items == ()
    else:
        assert len(state.items) == expected, name
    for item in state.items:
        assert item.link.startswith("http")


def test_an_atom_index_that_says_what_each_document_is() -> None:
    """Algeria's Atom has the CAP elements in each entry: the expired and the test ones need not be downloaded."""
    state = parse_index(samples.fixture("index_dz_onm.xml"), samples.index_url("index_dz_onm.xml"))
    first = state.items[0]
    assert first.status == "actual" and first.expires_utc is not None and first.published_utc is not None
    assert first.link == samples.document_url("cap_dz_onm_rain.xml")


def test_an_rss_index_with_a_guid_that_is_not_an_address() -> None:
    state = parse_index(samples.fixture("index_es_aemet.xml"), samples.index_url("index_es_aemet.xml"))
    assert [item.link for item in state.items] == [
        samples.document_url("cap_es_aemet_update_1.xml"),
        samples.document_url("cap_es_aemet_update_2.xml"),
        samples.document_url("cap_es_aemet_alert.xml"),
    ]


@pytest.mark.parametrize(
    "content",
    [b"", b"<html><title>404</title></html>", b"not xml", b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "b">]><rss version="2.0"><channel/></rss>', b"<rss version='2.0'/>"],
)
def test_what_is_not_a_feed_is_an_error(content: bytes) -> None:
    """Synthetic: an error page, a DTD, a feed with no channel."""
    with pytest.raises(CapError):
        parse_index(content, "https://example.org/feed.xml")


# ---------------------------------------------------------------------------------------------------- where a link may lead


@pytest.mark.parametrize(
    ("link", "feed", "allowed"),
    [
        ("https://smn.conagua.gob.mx/tools/FILES/a.xml", "https://smn.conagua.gob.mx/tools/PHP/feedsmn/cap.php", True),
        ("http://alert.met.no/a.xml", "https://alert.met.no/feed?lang=en", True),
        ("https://api01.nve.no/a.xml", "https://cap.nve.no/feed", True),  # the same site, another host
        ("https://ametvigilance.meteo.dz/CAPs/a.xml", "https://ametvigilance.meteo.dz/rss/rss_meteo_dz.xml", True),
        ("https://www.tmd.go.th/x.xml", "https://www.tmd.go.th/en/api/xml/CAP", True),
        ("https://sachet.ndma.gov.in/a.xml", "https://sachet.ndma.gov.in/rss.xml", True),
        # Another site, an address, the machine itself: refused.
        ("https://evil.example.com/a.xml", "https://smn.conagua.gob.mx/feed", False),
        ("http://127.0.0.1/admin", "https://smn.conagua.gob.mx/feed", False),
        ("http://[::1]/admin", "https://smn.conagua.gob.mx/feed", False),
        ("http://169.254.169.254/latest/meta-data/", "https://smn.conagua.gob.mx/feed", False),
        ("http://10.0.0.5/a.xml", "https://smn.conagua.gob.mx/feed", False),
        ("http://localhost/a.xml", "https://smn.conagua.gob.mx/feed", False),
        ("http://printer.local/a.xml", "https://smn.conagua.gob.mx/feed", False),
        ("ftp://smn.conagua.gob.mx/a.xml", "https://smn.conagua.gob.mx/feed", False),
        ("file:///etc/passwd", "https://smn.conagua.gob.mx/feed", False),
        ("//smn.conagua.gob.mx/a.xml", "https://smn.conagua.gob.mx/feed", False),
        ("https://smn.conagua.gob.mx.evil.com/a.xml", "https://smn.conagua.gob.mx/feed", False),
        ("https://evilsmn.conagua.gob.mx.evil.mx/a.xml", "https://smn.conagua.gob.mx/feed", False),
        ("https://smn.conagua.gob.mx/" + "a" * 2100, "https://smn.conagua.gob.mx/feed", False),
        # A feed on a site that hosts other people's data: the very host is required (a bucket holds many services).
        ("https://cap-sources.s3.amazonaws.com/in-imd-en/a.xml", "https://cap-sources.s3.amazonaws.com/in-imd-en/rss.xml", True),
        ("https://other-bucket.s3.amazonaws.com/a.xml", "https://cap-sources.s3.amazonaws.com/in-imd-en/rss.xml", False),
        ("https://evil.s3.amazonaws.com/a.xml", "https://cap-sources.s3.amazonaws.com/in-imd-en/rss.xml", False),
    ],
)
def test_where_a_link_of_a_feed_may_lead(link: str, feed: str, allowed: bool) -> None:
    """Synthetic addresses: a feed that says "the document is over there" must not send this server anywhere it likes."""
    assert may_follow(link, feed) is allowed


async def test_a_link_to_another_site_is_never_fetched(sim_clock: SimClock) -> None:
    """Synthetic: a feed lists one honest document and three that lead elsewhere; only the honest one is asked."""
    sim_clock.advance(samples.at(RECORDED))
    feed_url = "https://alerts.example.org/rss.xml"
    host = "https://alerts.example.org"
    items = [
        f"<item><link>{link}</link><pubDate>Tue, 20 Oct 2026 16:00:00 +0000</pubDate></item>"
        for link in (host + "/ok.xml", "http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:8080/x", "https://evil.example.com/x.xml")
    ]
    server = FixtureServer()
    server.answers[feed_url] = ('<rss version="2.0"><channel>' + "".join(items) + "</channel></rss>").encode()
    server.answers[host + "/ok.xml"] = samples.cap("ok-1", infos=[samples.info()])
    feed = (await collector(server).fetch_source(SwicSource("xx-test-en", "XX", "", "test", (feed_url,)))).value
    assert [request.url.host for request in server.own() if request.url.path != "/rss.xml"] == ["alerts.example.org"]
    assert len(feed.vigilances) == 1 and feed.documents == 1


# ---------------------------------------------------------------------------------------------------- one service, read


async def test_spain_is_read_from_aemet_and_the_update_chain_is_followed(sim_clock: SimClock) -> None:
    sim_clock.advance(samples.at(1_792_680_000))  # 2026-10-22T14:40Z: the yellow wind warning is in force, the orange rain ones are over
    server = FixtureServer("index_es_aemet.xml")
    service = samples.source("es-aemet-es", "ES", "index_es_aemet.xml")
    feed = (await collector(server).fetch_source(service)).value
    assert feed.documents == 3 and feed.unreadable == 0 and feed.pending == 0 and feed.feeds_read == 1
    assert [v.identifier for v in feed.vigilances] == ["2.49.0.0.724.0.ES.20261020092533.712701VIRM22221792488333"]
    vigilance = feed.vigilances[0]
    assert vigilance.source == "swic" and vigilance.country == "ES"
    assert "AEMET" in vigilance.attribution  # the credit of the service, from swic_terms
    assert vigilance.shape is not None


async def test_a_warning_that_is_not_in_force_is_not_returned(sim_clock: SimClock) -> None:
    sim_clock.advance(samples.at(1_792_706_400 + 3600))  # an hour after the end of the yellow warning
    server = FixtureServer("index_es_aemet.xml")
    feed = (await collector(server).fetch_source(samples.source("es-aemet-es", "ES", "index_es_aemet.xml"))).value
    assert feed.vigilances == ()
    assert feed.documents == 3  # but they were read: an expired alert is not asked again either


async def test_each_document_is_asked_once_and_the_feed_is_asked_conditionally(sim_clock: SimClock) -> None:
    sim_clock.advance(samples.at(1_792_680_000))
    server = FixtureServer("index_es_aemet.xml")
    service = samples.source("es-aemet-es", "ES", "index_es_aemet.xml")
    swic_collector = collector(server)
    await swic_collector.fetch_source(service)
    assert sum(server.counts().values()) == 4  # the feed, then its three documents
    await swic_collector.fetch_source(service)
    assert sum(server.counts().values()) == 4  # kept for five minutes
    sim_clock.advance(swic.SOURCE_TTL_S + 1)
    await swic_collector.fetch_source(service)
    url = samples.index_url("index_es_aemet.xml")
    assert server.count(url) == 2 and server.not_modified == [url]  # asked again with its validator, and answered 304
    assert server.requests[-1].headers["If-Modified-Since"] == samples.MANIFEST["indexes"]["index_es_aemet.xml"]["last_modified"]
    assert sum(server.counts().values()) == 5  # no document again
    assert (await swic_collector.fetch_source(service)).value.documents == 3


async def test_a_feed_that_gives_no_validator_is_downloaded_again_each_time(sim_clock: SimClock) -> None:
    """Argentina's feed gave none on 2026-10-20: every read downloads it whole (and it lists only what the feed lists)."""
    sim_clock.advance(samples.at(RECORDED))
    server = FixtureServer("index_ar_smn.xml")
    url = samples.index_url("index_ar_smn.xml")
    assert server.validators[url] == {}
    service = samples.source("ar-smn-es", "AR", "index_ar_smn.xml")
    swic_collector = collector(server)
    for _ in range(3):
        await swic_collector.fetch_source(service)
        sim_clock.advance(swic.SOURCE_TTL_S + 1)
    assert server.count(url) == 3 and server.not_modified == []
    assert sum(server.counts().values()) == 3 + 2  # the two documents once


async def test_a_cancel_removes_nothing_that_is_not_there_and_gives_no_warning(sim_clock: SimClock) -> None:
    sim_clock.advance(samples.at(RECORDED))
    server = FixtureServer("index_by_belhydromet.xml")
    feed = (await collector(server).fetch_source(samples.source("by-belhydromet-en", "BY", "index_by_belhydromet.xml"))).value
    assert feed.vigilances == () and feed.documents == 2 and feed.unreadable == 0


async def test_a_warning_called_off_by_a_later_cancel_leaves_the_answer(sim_clock: SimClock) -> None:
    """Synthetic: the feed lists an alert and a Cancel that names it."""
    sim_clock.advance(samples.at(RECORDED))
    base = "https://alerts.example.org"
    feed_url = base + "/rss.xml"
    server = FixtureServer()
    server.answers[feed_url] = (
        '<rss version="2.0"><channel>'
        f"<item><link>{base}/a.xml</link><pubDate>Tue, 20 Oct 2026 15:00:00 +0000</pubDate></item>"
        f"<item><link>{base}/b.xml</link><pubDate>Tue, 20 Oct 2026 16:00:00 +0000</pubDate></item>"
        "</channel></rss>"
    ).encode()
    server.answers[base + "/a.xml"] = samples.cap("a-1", sender="s@example.org", infos=[samples.info()])
    server.answers[base + "/b.xml"] = samples.cap("b-1", sender="s@example.org", msg_type="Cancel", references="s@example.org,a-1,2026-10-20T16:00:00+00:00")
    feed = (await collector(server).fetch_source(SwicSource("xx-test-en", "XX", "", "test", (feed_url,)))).value
    assert feed.vigilances == () and feed.documents == 2


async def test_a_test_status_is_never_a_warning(sim_clock: SimClock) -> None:
    """Colombia's documents of 2026-10-20 were all status Test."""
    sim_clock.advance(samples.at(RECORDED))
    server = FixtureServer("index_co_ungrd.xml")
    feed = (await collector(server).fetch_source(samples.source("co-ungrd-es", "CO", "index_co_ungrd.xml"))).value
    assert feed.vigilances == () and feed.documents == 2


async def test_an_index_with_no_alert_is_no_warning_and_no_other_request(sim_clock: SimClock) -> None:
    sim_clock.advance(samples.at(RECORDED))
    server = FixtureServer("index_jm_no_active.xml")
    feed = (await collector(server).fetch_source(samples.source("jm-jms-en", "JM", "index_jm_no_active.xml"))).value
    assert feed.vigilances == () and feed.documents == 0 and feed.feeds_read == 1
    assert len(server.own()) == 1


async def test_items_older_than_fourteen_days_are_not_downloaded(sim_clock: SimClock) -> None:
    """Trinidad and Tobago lists 848 items and no validator: the old ones are never asked for."""
    sim_clock.advance(samples.at(RECORDED))
    server = FixtureServer("index_tt_ttms.xml")
    feed = (await collector(server).fetch_source(samples.source("tt-ttms-en", "TT", "index_tt_ttms.xml"))).value
    assert feed.documents == 0 and feed.old_items == 2 and feed.vigilances == ()
    assert len(server.own()) == 1


async def test_the_same_alert_in_two_languages_under_two_identifiers_russia(sim_clock: SimClock) -> None:
    """Russia: an English feed and a Russian feed, one document per language, an identifier each (ending .EN and .RU)."""
    sim_clock.advance(samples.at(RECORDED - 3600))
    server = FixtureServer("index_ru_en.xml", "index_ru_ru.xml")
    service = samples.source("ru-roshydromet-en", "RU", "index_ru_en.xml", "index_ru_ru.xml")
    feed = (await collector(server).fetch_source(service)).value
    assert feed.documents == 4 and feed.feeds_read == 2
    assert len(feed.vigilances) == 2
    for vigilance in feed.vigilances:
        assert {text.language for text in vigilance.texts} == {"en", "ru"}
    fire = next(v for v in feed.vigilances if "0266022" in v.identifier)
    assert fire.area.description == "Orenburg Region"  # the first feed's words stay the primary ones
    assert {text.event for text in fire.texts} == {"Forestfire", "Пожарная опасность"}


async def test_the_same_alert_in_two_languages_under_two_identifiers_kazakhstan(sim_clock: SimClock) -> None:
    """Kazakhstan: an identifier ending in -RU and one ending in -EN, in two feeds."""
    sent = parse_alert(samples.fixture("cap_kz_en.xml")).sent_utc
    assert sent is not None
    sim_clock.advance(samples.at(sent + 600))
    server = FixtureServer("index_kz_ru.xml", "index_kz_en.xml")
    service = samples.source("kz-kazhydromet-ru", "KZ", "index_kz_ru.xml", "index_kz_en.xml")
    feed = (await collector(server).fetch_source(service)).value
    assert len(feed.vigilances) == 1
    assert {text.language for text in feed.vigilances[0].texts} == {"ru-RU", "en-US"}


async def test_two_documents_of_one_identifier_in_two_languages_are_one_warning(sim_clock: SimClock) -> None:
    """Synthetic: a service that writes one document per language under one identifier (none of the recorded ones does)."""
    sim_clock.advance(samples.at(RECORDED))
    base = "https://alerts.example.org"
    feed_url = base + "/rss.xml"
    items = "".join(f"<item><link>{base}/{n}.xml</link><pubDate>Tue, 20 Oct 2026 16:00:00 +0000</pubDate></item>" for n in ("en", "fr", "en-again"))
    server = FixtureServer()
    server.answers[feed_url] = f'<rss version="2.0"><channel>{items}</channel></rss>'.encode()
    server.answers[base + "/en.xml"] = samples.cap("one-1", infos=[samples.info(language="en")])
    server.answers[base + "/fr.xml"] = samples.cap("one-1", infos=[samples.info(language="fr", event="Fortes pluies")])
    server.answers[base + "/en-again.xml"] = samples.cap("one-1", infos=[samples.info(language="en")])  # the same document listed twice
    feed = (await collector(server).fetch_source(SwicSource("xx-test-en", "XX", "", "test", (feed_url,)))).value
    assert len(feed.vigilances) == 1 and {text.language for text in feed.vigilances[0].texts} == {"en", "fr"}


async def test_two_possible_twins_are_not_joined(sim_clock: SimClock) -> None:
    """Synthetic: one Russian document could be the translation of either of two identical English ones: no text goes on a guess."""
    sim_clock.advance(samples.at(RECORDED))
    base = "https://alerts.example.org"
    feed_url = base + "/rss.xml"
    names = ("en1", "en2", "ru1")
    items = "".join(f"<item><link>{base}/{n}.xml</link><pubDate>Tue, 20 Oct 2026 16:00:00 +0000</pubDate></item>" for n in names)
    server = FixtureServer()
    server.answers[feed_url] = f'<rss version="2.0"><channel>{items}</channel></rss>'.encode()
    server.answers[base + "/en1.xml"] = samples.cap("en-1", infos=[samples.info(language="en")])
    server.answers[base + "/en2.xml"] = samples.cap("en-2", infos=[samples.info(language="en")])
    server.answers[base + "/ru1.xml"] = samples.cap("ru-1", infos=[samples.info(language="ru", event="Сильный дождь")])
    feed = (await collector(server).fetch_source(SwicSource("xx-test-en", "XX", "", "test", (feed_url,)))).value
    assert len(feed.vigilances) == 3
    assert all(len(v.texts) == 1 for v in feed.vigilances)


async def test_a_document_that_is_not_cap_is_counted_and_does_not_stop_the_others(sim_clock: SimClock) -> None:
    """Synthetic: Guyana's feed listed itself as a document."""
    sim_clock.advance(samples.at(RECORDED))
    base = "https://alerts.example.org"
    feed_url = base + "/rss.xml"
    server = FixtureServer()
    server.answers[feed_url] = (
        '<rss version="2.0"><channel>'
        f"<item><link>{base}/html.xml</link><pubDate>Tue, 20 Oct 2026 16:00:00 +0000</pubDate></item>"
        f"<item><link>{base}/good.xml</link><pubDate>Tue, 20 Oct 2026 16:00:00 +0000</pubDate></item>"
        f"<item><link>{base}/gone.xml</link><pubDate>Tue, 20 Oct 2026 16:00:00 +0000</pubDate></item>"
        "</channel></rss>"
    ).encode()
    server.answers[base + "/html.xml"] = b"<html><body>Not a CAP alert</body></html>"
    server.answers[base + "/good.xml"] = samples.cap("good-1", infos=[samples.info()])
    server.answers[base + "/gone.xml"] = 404
    swic_collector = collector(server)
    feed = (await swic_collector.fetch_source(SwicSource("xx-test-en", "XX", "", "test", (feed_url,)))).value
    assert len(feed.vigilances) == 1 and feed.unreadable == 2
    assert any("could not be read as a CAP alert" in note for note in swic.notes_of(feed))
    sim_clock.advance(swic.SOURCE_TTL_S + 1)
    await swic_collector.fetch_source(SwicSource("xx-test-en", "XX", "", "test", (feed_url,)))
    assert server.count(base + "/html.xml") == 1 and server.count(base + "/gone.xml") == 1  # not asked again


# ---------------------------------------------------------------------------------------------------- sobriety


async def test_at_most_sixty_new_documents_are_downloaded_per_read(sim_clock: SimClock) -> None:
    """Synthetic: a feed that lists 70 alerts at once (a cold start): 60 now, the others at the next read."""
    sim_clock.advance(samples.at(RECORDED))
    base = "https://alerts.example.org"
    feed_url = base + "/rss.xml"
    items = "".join(f"<item><link>{base}/a{n}.xml</link><pubDate>Tue, 20 Oct 2026 16:00:00 +0000</pubDate></item>" for n in range(70))
    server = FixtureServer()
    server.answers[feed_url] = f'<rss version="2.0"><channel>{items}</channel></rss>'.encode()
    for n in range(70):
        server.answers[f"{base}/a{n}.xml"] = samples.cap(f"id-{n}", infos=[samples.info()])
    source = SwicSource("xx-test-en", "XX", "", "test", (feed_url,))
    swic_collector = collector(server)
    first = (await swic_collector.fetch_source(source)).value
    assert first.documents == 70 and first.pending == 10 and len(first.vigilances) == 60
    assert any("10 CAP documents are not read yet" in note for note in swic.notes_of(first))
    sim_clock.advance(swic.SOURCE_TTL_S + 1)
    second = (await swic_collector.fetch_source(source)).value
    assert second.pending == 0 and len(second.vigilances) == 70
    assert max(server.counts()[f"{base}/a{n}.xml"] for n in range(70)) == 1


async def test_a_heavy_feed_without_validator_is_asked_less_often(sim_clock: SimClock) -> None:
    """Synthetic: 130 KB on the wire each time, no validator: 3 TTLs (15 minutes) instead of 1."""
    sim_clock.advance(samples.at(RECORDED))
    feed_url = "https://alerts.example.org/rss.xml"
    padding = "<!--" + "x" * (2 * swic.HEAVY_FEED_STEP_BYTES) + "-->"
    server = FixtureServer()
    server.answers[feed_url] = ('<rss version="2.0"><channel>' + padding + "</channel></rss>").encode()
    feed = (await collector(server).fetch_source(SwicSource("xx-test-en", "XX", "", "test", (feed_url,)))).value
    assert feed.refresh_s == 3 * swic.SOURCE_TTL_S
    light = FixtureServer("index_ar_smn.xml")
    assert (await collector(light).fetch_source(samples.source("ar-smn-es", "AR", "index_ar_smn.xml"))).value.refresh_s == swic.SOURCE_TTL_S
    kept = FixtureServer("index_es_aemet.xml")
    assert (await collector(kept).fetch_source(samples.source("es-aemet-es", "ES", "index_es_aemet.xml"))).value.refresh_s == swic.SOURCE_TTL_S


async def test_the_size_caps(sim_clock: SimClock) -> None:
    """Synthetic: a feed over 2 MB and a document over 1 MB are refused, not read."""
    sim_clock.advance(samples.at(RECORDED))
    base = "https://alerts.example.org"
    feed_url = base + "/rss.xml"
    big_feed = FixtureServer()
    big_feed.answers[feed_url] = b"<rss version='2.0'><channel>" + b"x" * (swic.MAX_INDEX_BYTES + 10) + b"</channel></rss>"
    with pytest.raises(CollectorUnavailableError):
        await collector(big_feed).fetch_source(SwicSource("xx-test-en", "XX", "", "test", (feed_url,)))
    big_document = FixtureServer()
    big_document.answers[feed_url] = f'<rss version="2.0"><channel><item><link>{base}/big.xml</link><pubDate>Tue, 20 Oct 2026 16:00:00 +0000</pubDate></item></channel></rss>'.encode()
    big_document.answers[base + "/big.xml"] = b"<alert>" + b"x" * (swic.MAX_DOCUMENT_BYTES + 10) + b"</alert>"
    feed = (await collector(big_document).fetch_source(SwicSource("xx-test-en", "XX", "", "test", (feed_url,)))).value
    assert feed.vigilances == () and feed.pending == 1


async def test_an_error_page_is_not_read(sim_clock: SimClock) -> None:
    """Saudi Arabia's registered feed answers 404 with 251 KB of HTML: the collector takes the status and closes."""
    sim_clock.advance(samples.at(RECORDED))
    feed_url = "https://ncm.example.sa/feed.aspx"
    read: list[int] = []

    class Page(httpx.AsyncByteStream):
        async def __aiter__(self):  # type: ignore[no-untyped-def]
            for _ in range(250):
                read.append(1)
                yield b"x" * 1024

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, stream=Page(), headers={"Content-Type": "text/html"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(CollectorUnavailableError):
        await SwicCollector(http_client=client).fetch_source(SwicSource("sa-ncm-en", "SA", "", "ncm", (feed_url,)))
    assert read == []  # not one chunk of the 251 KB page was read


# ---------------------------------------------------------------------------------------------------- a service that does not answer


async def test_a_service_that_fails_does_not_close_the_circuit_of_the_others(sim_clock: SimClock) -> None:
    sim_clock.advance(samples.at(RECORDED))
    server = FixtureServer("index_ar_smn.xml")
    bad_url = "https://down.example.org/feed.xml"
    server.answers[bad_url] = httpx.ConnectError("refused")
    swic_collector = collector(server)
    bad = SwicSource("xx-bad-en", "XX", "", "bad", (bad_url,))
    good = samples.source("ar-smn-es", "AR", "index_ar_smn.xml")
    for _ in range(swic.SERVICE_FAILURES_BEFORE_BACKOFF + 1):
        with pytest.raises(CollectorUnavailableError):
            await swic_collector.fetch_source(bad)
        sim_clock.advance(swic.SOURCE_TTL_S + 1)
    assert (await swic_collector.fetch_source(good)).value.documents == 2
    assert swic_collector.get_health().circuit_state is CircuitState.CLOSED
    assert swic_collector.consecutive_failures == 1  # the number of services in trouble


async def test_a_failing_service_is_asked_less_and_less_often(sim_clock: SimClock) -> None:
    sim_clock.advance(samples.at(RECORDED))
    server = FixtureServer()
    bad_url = "https://down.example.org/feed.xml"
    server.answers[bad_url] = httpx.ConnectError("refused")
    swic_collector = collector(server)
    bad = SwicSource("xx-bad-en", "XX", "", "bad", (bad_url,))
    for _ in range(12):  # twelve cycles of five minutes: an hour
        with pytest.raises(CollectorUnavailableError):
            await swic_collector.fetch_source(bad)
        sim_clock.advance(swic.SOURCE_TTL_S + 1)
    asked = server.count(bad_url)
    assert asked < 12, asked  # not every five minutes
    assert asked >= swic.SERVICE_FAILURES_BEFORE_BACKOFF


async def test_a_feed_that_is_gone_is_asked_again_in_an_hour_not_in_a_minute(sim_clock: SimClock) -> None:
    """A 404 or a 403 (a bot check) is not a hiccup: Saudi Arabia's feed address of the registry answers 404."""
    sim_clock.advance(samples.at(RECORDED))
    server = FixtureServer()
    gone = "https://gone.example.org/feed.xml"
    server.answers[gone] = 404
    swic_collector = collector(server)
    source = SwicSource("xx-gone-en", "XX", "", "gone", (gone,))
    for _ in range(11):
        with pytest.raises(CollectorUnavailableError):
            await swic_collector.fetch_source(source)
        sim_clock.advance(swic.SOURCE_TTL_S + 1)
    assert server.count(gone) == 1  # the first answer; the rest of the hour waits
    sim_clock.advance(swic.PERSISTENT_FAILURE_RETRY_S)
    with pytest.raises(CollectorUnavailableError):
        await swic_collector.fetch_source(source)
    assert server.count(gone) == 2


async def test_the_last_answer_is_served_stale_when_a_service_stops_answering(sim_clock: SimClock) -> None:
    sim_clock.advance(samples.at(1_792_680_000))
    server = FixtureServer("index_es_aemet.xml")
    service = samples.source("es-aemet-es", "ES", "index_es_aemet.xml")
    swic_collector = collector(server)
    assert not (await swic_collector.fetch_source(service)).stale
    server.answers[samples.index_url("index_es_aemet.xml")] = 503
    sim_clock.advance(swic.SOURCE_TTL_S + 1)
    again = await swic_collector.fetch_source(service)
    assert again.stale and len(again.value.vigilances) == 1  # never "nothing in force": the last answer, said to be old


# ---------------------------------------------------------------------------------------------------- the WMO's copy


def national(source_id: str = "xx-nat-en", index: str = "index_es_aemet.xml") -> SwicSource:
    return samples.source(source_id, "XX", index)


async def test_the_wmo_copy_is_asked_for_a_service_on_a_national_host_and_a_miss_is_remembered(sim_clock: SimClock) -> None:
    sim_clock.advance(samples.at(1_792_680_000))
    server = FixtureServer("index_es_aemet.xml")
    swic_collector = collector(server)
    first = (await swic_collector.fetch_source(national())).value
    assert first.mirror == "missing"
    url = MIRROR.format("xx-nat-en")
    assert server.count(url) == 1
    for _ in range(5):  # the copy answered 404: not asked again for this id for a day
        sim_clock.advance(swic.SOURCE_TTL_S + 1)
        await swic_collector.fetch_source(national())
    assert server.count(url) == 1
    sim_clock.advance(swic.MIRROR_MISSING_S)
    await swic_collector.fetch_source(national())
    assert server.count(url) == 2


async def test_the_wmo_copy_is_paused_for_an_hour_after_three_misses_in_a_row(sim_clock: SimClock) -> None:
    sim_clock.advance(samples.at(1_792_680_000))
    server = FixtureServer("index_es_aemet.xml")
    swic_collector = collector(server)
    outcomes = []
    for n in range(5):
        feed = (await swic_collector.fetch_source(national(f"xx-n{n}-en"))).value
        outcomes.append(feed.mirror)
    assert outcomes == ["missing", "missing", "missing", "paused", "paused"]
    assert sum(1 for request in server.requests if request.url.host == "severeweather.wmo.int") == 3


async def test_a_service_in_the_wmo_bucket_is_not_asked_twice(sim_clock: SimClock) -> None:
    """The copy is made from the bucket: it cannot hold what the bucket does not."""
    sim_clock.advance(samples.at(RECORDED))
    bucket = "https://cap-sources.s3.amazonaws.com/xx-test-en/rss.xml"
    server = FixtureServer()
    server.answers[bucket] = b'<rss version="2.0"><channel/></rss>'
    feed = (await collector(server).fetch_source(SwicSource("xx-test-en", "XX", "", "test", (bucket,)))).value
    assert feed.mirror == "not asked" and len(server.requests) == 1


async def test_the_wmo_copy_adds_what_the_service_feed_lacks_and_an_alert_is_read_once(sim_clock: SimClock) -> None:
    """Recorded: the one copy of a feed that answered on 2026-10-20 (China's, first two items). The service's own feed is down."""
    sim_clock.advance(samples.at(RECORDED))
    server = FixtureServer()
    own = "https://own.example.org/rss.xml"
    server.answers[own] = 503
    server.answers[MIRROR.format("xx-copy-en")] = samples.fixture("mirror_rss.xml")
    swic_collector = collector(server)
    feed = (await swic_collector.fetch_source(SwicSource("xx-copy-en", "XX", "", "copy", (own,)))).value
    assert feed.mirror == "read" and feed.feeds_read == 0 and feed.feeds_total == 1
    assert feed.documents == 2  # the two items of the copy, whose documents are not in the server: gone
    assert any("own feed did not answer" in note for note in swic.notes_of(feed))
    assert instant("2026-10-20T16:20:00Z") is not None


# ---------------------------------------------------------------------------------------------------- documents of other services


async def test_the_document_cache_is_shared_by_the_feeds_of_a_service_and_forgets_what_no_index_lists(sim_clock: SimClock) -> None:
    sim_clock.advance(samples.at(RECORDED - 3600))
    server = FixtureServer("index_ru_en.xml", "index_ru_ru.xml")
    swic_collector = collector(server)
    service = samples.source("ru-roshydromet-en", "RU", "index_ru_en.xml", "index_ru_ru.xml")
    await swic_collector.fetch_source(service)
    assert len(swic_collector._documents) == 4  # noqa: SLF001
    # The feeds now list nothing: the documents are forgotten (memory is bounded by what the feeds list).
    for name in ("index_ru_en.xml", "index_ru_ru.xml"):
        server.answers[samples.index_url(name)] = b'<feed xmlns="http://www.w3.org/2005/Atom"><updated>2026-10-20T17:00:00Z</updated></feed>'
    sim_clock.advance(swic.SOURCE_TTL_S + 1)
    await swic_collector.fetch_source(service)
    assert len(swic_collector._documents) == 0  # noqa: SLF001
