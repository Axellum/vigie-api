"""The MetService collector (New Zealand).

**What is real and what is not.** MetService documents its feed in the *MetService CAP Feed User Guide*, version 1.2 of
20 April 2026 (read on 2026-10-20; see ``SOURCES.md``): the tables of the level (``ColourCode``), the event words and the
headlines below are the guide's. But on 2026-10-20 no warning was in force in New Zealand: the only recorded answers are
the empty RSS and Atom feeds (``tests/fixtures/metservice/``), and they are used as such. **No real item and no real CAP
document has been seen here.** Every feed item and CAP document in this file is therefore **synthetic**: written after
the structure of the guide's Appendices A and B (the area, the identifiers and the texts are invented), to exercise the
reading. They prove the reading follows the guide, not that MetService writes them this way today: the weekly live check
(``tools/check_live_feeds.py``) fails the day a real item cannot be read.
"""

from __future__ import annotations

from typing import Any, Optional, Union

import httpx
import pytest
from shapely.geometry import Point

from app.attribution import credit_text
from app.collectors.base import Fetched
from app.collectors.metservice import (
    FEED_URL,
    MetServiceCollector,
    derive_level,
    derive_phenomenon,
    parse_cap,
    parse_feed,
)
from app.models.schemas import SeverityLevel
from app.models.vigilance import VigilancePhenomenon, VigilanceStatus
from tests import vigilance_samples as samples
from tests.helpers import Provider, SimClock

COPYRIGHT = "Copyright Meteorological Service of New Zealand Limited, 2026. Licensed under Creative Commons BY 4.0."
ISSUER = "Meteorological Service of New Zealand Limited"  # the fixed <senderName> (guide 3.2.9)
RECORDED_LAST_MODIFIED = "Tue, 20 Oct 2026 11:55:21 GMT"
BASE = "https://alerts.metservice.com/cap/"

# A box around Wellington, as CAP writes a polygon: "lat,lon" pairs.
WELLINGTON_BOX = "-41.5,174.5 -41.5,175.0 -41.1,175.0 -41.1,174.5 -41.5,174.5"
HEX = {"Yellow": "#FFEB18", "Orange": "#FF8918", "Red": "#FF181E"}  # ColourCodeHex (guide 3.2.14.3)
CHRISTCHURCH_BOX = "-43.7,172.4 -43.7,172.9 -43.4,172.9 -43.4,172.4 -43.7,172.4"

# Every kind of warning the guide lists, with what it says of each (headlines: 3.2.10; <event>: 3.2.2; "ColourCode":
# 3.2.14.2; Table 4): (headline, <event>, ColourCode, our level, our phenomenon).
DOCUMENTED = [
    ("Heavy Rain Watch", "rain", "Yellow", SeverityLevel.YELLOW, VigilancePhenomenon.RAIN),
    ("Heavy Rain Warning - Orange", "rain", "Orange", SeverityLevel.ORANGE, VigilancePhenomenon.RAIN),
    ("Heavy Rain Warning - Red", "rain", "Red", SeverityLevel.RED, VigilancePhenomenon.RAIN),
    ("Heavy Snow Watch", "snow", "Yellow", SeverityLevel.YELLOW, VigilancePhenomenon.SNOW_ICE),
    ("Heavy Snow Warning - Orange", "snow", "Orange", SeverityLevel.ORANGE, VigilancePhenomenon.SNOW_ICE),
    ("Heavy Snow Warning - Red", "snow", "Red", SeverityLevel.RED, VigilancePhenomenon.SNOW_ICE),
    ("Strong Wind Watch", "wind", "Yellow", SeverityLevel.YELLOW, VigilancePhenomenon.WIND),
    ("Strong Wind Warning - Orange", "wind", "Orange", SeverityLevel.ORANGE, VigilancePhenomenon.WIND),
    ("Strong Wind Warning - Red", "wind", "Red", SeverityLevel.RED, VigilancePhenomenon.WIND),
    ("Road Snowfall Warning", "snow", "Orange", SeverityLevel.ORANGE, VigilancePhenomenon.SNOW_ICE),
    ("Severe Thunderstorm Watch", "thunderstorm", "Yellow", SeverityLevel.YELLOW, VigilancePhenomenon.THUNDERSTORM),
    # "localStorm": a Severe Thunderstorm Watch for a local severe event that is not a thunderstorm (3.2.2).
    ("Severe Thunderstorm Watch", "localStorm", "Yellow", SeverityLevel.YELLOW, VigilancePhenomenon.OTHER),
    ("Severe Thunderstorm Warning", "thunderstorm", "Red", SeverityLevel.RED, VigilancePhenomenon.THUNDERSTORM),
]
WARNING_IDS = [f"{headline} ({event})" for headline, event, *_ in DOCUMENTED]


def alert_address(identifier: str) -> str:
    """The address of a CAP document as the guide's Appendix A writes a link (``/cap/alert?id=<identifier>``)."""
    return f"{BASE}alert?id={identifier}"


# ---------------------------------------------------------------- synthetic documents (see the docstring)


def synthetic_cap(
    identifier: str = "synthetic-1",
    *,
    event: str = "rain",
    headline: str = "Heavy Rain Warning - Orange",
    severity: str = "Moderate",
    colour: Optional[str] = "Orange",
    status: str = "Actual",
    msg_type: str = "Alert",
    polygon: Union[None, str, tuple[str, ...]] = WELLINGTON_BOX,
    area_desc: str = "Wellington (synthetic)",
) -> bytes:
    """A CAP alert in the structure of the guide's Appendix B: an Update carries ``<references>``, a signature closes it."""
    polygons = (polygon,) if isinstance(polygon, str) else (polygon or ())
    area = "".join(f"<polygon>{p}</polygon>" for p in polygons)
    colour_parameters = (
        f"<parameter><valueName>ColourCode</valueName><value>{colour}</value></parameter>"
        f"<parameter><valueName>ColourCodeHex</valueName><value>{HEX.get(colour, '#FF8918')}</value></parameter>"
        if colour is not None
        else ""
    )
    references = (
        f"<references>https://www.metservice.com,{identifier}-previous,2026-10-21T00:00:00+13:00</references>" if msg_type == "Update" else ""
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
<identifier>{identifier}</identifier><sender>https://www.metservice.com</sender><sent>2026-10-21T08:00:00+13:00</sent>
<status>{status}</status><msgType>{msg_type}</msgType><scope>Public</scope>{references}
<info><category>Met</category><event>{event}</event><responseType>Monitor</responseType>
<urgency>Future</urgency><severity>{severity}</severity><certainty>Likely</certainty>
<onset>2026-10-21T12:00:00+13:00</onset><expires>2026-10-22T06:00:00+13:00</expires>
<senderName>{ISSUER}</senderName><headline>{headline}</headline>
<description>Synthetic description of {headline}. Chance of upgrading to a Red Warning: Low.</description>
<instruction>Synthetic instruction. Keep an eye on the forecast.</instruction>
<web>https://metservice.com/warnings/home</web>
<parameter><valueName>ChanceOfUpgrade</valueName><value>Low</value></parameter>{colour_parameters}
<parameter><valueName>NextUpdate</valueName><value>2026-10-21T21:00:00+13:00</value></parameter>
<area><areaDesc>{area_desc}</areaDesc>{area}</area>
</info>
<Signature xmlns="http://www.w3.org/2000/09/xmldsig#"><SignatureValue>c3ludGhldGlj</SignatureValue></Signature>
</alert>""".encode()


def synthetic_feed(*links: str, copyright_line: Optional[str] = COPYRIGHT, guid_is_link: bool = False) -> bytes:
    """An RSS 2.0 feed in the structure of the guide's Appendix A.1: ``guid`` is the identifier, ``link`` the document."""
    items = "".join(
        f"<item><title>Synthetic</title><guid>{link if guid_is_link else f'synthetic-guid-{n}'}</guid><category>Met</category>"
        f"<pubDate>Wed, 21 Oct 2026 08:00:00 +1300</pubDate><description>Synthetic</description>"
        f"{'' if guid_is_link else f'<link>{link}</link>'}</item>"
        for n, link in enumerate(links)
    )
    line = f"<copyright>{copyright_line}</copyright>" if copyright_line else ""
    return (
        '<?xml version="1.0" encoding="UTF-8" ?><rss version="2.0"><channel><title>Metservice Public CAP Alerts</title>'
        f"<link>https://www.metservice.com</link>{line}<pubDate>Wed, 21 Oct 2026 08:00:00 +1300</pubDate>{items}</channel></rss>"
    ).encode()


def server(feed: bytes, documents: dict[str, Any], *, modified: Optional[str] = RECORDED_LAST_MODIFIED) -> Provider:
    """MetService, as the guide describes it: the feed with a Last-Modified (a 304 to the repeated one), the documents."""

    def answer(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == FEED_URL:
            if modified and request.headers.get("If-Modified-Since") == modified:
                return httpx.Response(304)
            return httpx.Response(200, content=feed, headers={"Last-Modified": modified} if modified else {})
        content = documents.get(url, 404)
        return httpx.Response(content) if isinstance(content, int) else httpx.Response(200, content=content)

    return Provider(answer=answer, latency_ticks=0)


# ---------------------------------------------------------------- the recorded feeds


def test_the_recorded_rss_is_an_empty_channel_with_the_licence_line() -> None:
    assert parse_feed(samples.national_fixture("metservice", "rss_empty.xml")) == ([], 0, COPYRIGHT)


def test_the_recorded_headers_are_the_ones_the_collector_relies_on() -> None:
    headers = samples.national_fixture("metservice", "rss.headers.txt").decode()
    assert "Cache-Control: max-age=300, must-revalidate" in headers
    assert f"Last-Modified: {RECORDED_LAST_MODIFIED}" in headers


@pytest.mark.asyncio
async def test_an_empty_feed_is_no_warning_and_is_asked_conditionally_every_five_minutes(sim_clock: SimClock) -> None:
    provider = server(samples.national_fixture("metservice", "rss_empty.xml"), {})
    collector = MetServiceCollector(http_client=provider.client())

    feed = (await collector.fetch_feed()).value
    assert (feed.vigilances, feed.items, feed.unreadable, feed.copyright) == ((), 0, 0, COPYRIGHT)
    assert provider.count == 1 and str(provider.requests[0].url) == FEED_URL
    assert "If-Modified-Since" not in provider.requests[0].headers

    sim_clock.advance(299)
    await collector.fetch_feed()
    assert provider.count == 1
    sim_clock.advance(2)
    again = await collector.fetch_feed()
    assert provider.count == 2 and provider.requests[1].headers["If-Modified-Since"] == RECORDED_LAST_MODIFIED
    assert again.value is feed  # the 304 kept what was read


# ---------------------------------------------------------------- the tables (the guide's own)


@pytest.mark.parametrize(("headline", "event", "colour", "level", "phenomenon"), DOCUMENTED, ids=WARNING_IDS)
def test_the_level_is_the_colour_code_and_the_headline_alone_says_the_same(
    headline: str, event: str, colour: str, level: SeverityLevel, phenomenon: VigilancePhenomenon
) -> None:
    assert derive_level(event, headline, colour) == level  # the issuer's own colour (guide 3.2.14.2)
    assert derive_level(event, headline) == level  # without the parameter, the words of the guide's headline list give the same


@pytest.mark.parametrize(("headline", "event", "colour", "level", "phenomenon"), DOCUMENTED, ids=WARNING_IDS)
def test_the_phenomenon_is_the_event_word(headline: str, event: str, colour: str, level: SeverityLevel, phenomenon: VigilancePhenomenon) -> None:
    assert derive_phenomenon(event, headline) == phenomenon


@pytest.mark.parametrize(
    ("event", "headline", "colour", "level"),
    [
        ("rain", "Heavy Rain Warning - Orange", " orange ", SeverityLevel.ORANGE),  # case and spaces do not matter
        ("rain", "Heavy Rain Warning", "Red", SeverityLevel.RED),  # a colour the headline does not contradict
        ("rain", "Heavy Rain Warning - Orange", "Red", None),  # a colour that contradicts the headline: not guessed
        ("rain", "Heavy Rain Watch", "Orange", None),  # a Watch is yellow: the contradiction is not guessed either
        ("rain", "Heavy Rain Warning - Orange", "Purple", None),  # a colour the guide does not list
        ("rain", "Heavy Rain Warning - Orange", "", SeverityLevel.ORANGE),  # an empty parameter counts as absent
        ("rain", "Heavy Rain Warning", None, None),  # a Warning with neither colour nor colour word: not guessed
        ("rain", "Orange and Red Heavy Rain Warning", None, None),  # two colours in the words: not guessed
        ("Orange Heavy Rain Warning", None, None, SeverityLevel.ORANGE),  # the colour in the event only
        (None, None, None, None),
    ],
)
def test_what_the_colour_and_the_words_do_not_settle_gives_no_level(
    event: Optional[str], headline: Optional[str], colour: Optional[str], level: Optional[SeverityLevel]
) -> None:
    assert derive_level(event, headline, colour) == level


@pytest.mark.parametrize(
    ("event", "headline", "phenomenon"),
    [
        ("localStorm", "Severe Thunderstorm Watch", VigilancePhenomenon.OTHER),  # not a thunderstorm, whatever the headline says
        ("RAIN", None, VigilancePhenomenon.RAIN),  # case does not matter
        ("Heavy Rain Warning", None, VigilancePhenomenon.RAIN),  # an event word the guide does not list (it may become an eventCode): the words
        ("eventCode-17", "Strong Wind Warning - Red", VigilancePhenomenon.WIND),  # the headline when the event says nothing
        ("Tornado Watch", None, VigilancePhenomenon.WIND),
        ("Marine Weather Warning", None, VigilancePhenomenon.OTHER),  # not one of the products of the feed: "other", not a guess
        (None, None, None),
    ],
)
def test_the_phenomenon_falls_back_on_the_words_only_for_an_event_the_guide_does_not_list(
    event: Optional[str], headline: Optional[str], phenomenon: Optional[VigilancePhenomenon]
) -> None:
    assert derive_phenomenon(event, headline) == phenomenon


# ---------------------------------------------------------------- the reading of a CAP document


def test_an_orange_rain_warning_keeps_the_words_and_derives_the_rest() -> None:
    address = alert_address("2.49.0.0.554.0.severeweather.nz.20261021080000000.11")
    [warning] = parse_cap(synthetic_cap("2.49.0.0.554.0.severeweather.nz.20261021080000000.11"), COPYRIGHT, address)
    assert (warning.level, warning.level_original) == (SeverityLevel.ORANGE, "Orange")  # the issuer's colour
    assert (warning.phenomenon, warning.phenomenon_original) == (VigilancePhenomenon.RAIN, "rain")
    assert (warning.country, warning.source, warning.status, warning.message_type) == ("NZ", "metservice", VigilanceStatus.NEW, "Alert")
    assert warning.derived == ["status", "level", "phenomenon"]  # what the API derived; the rest is MetService's own
    text = warning.texts[0]
    assert text.headline == "Heavy Rain Warning - Orange" and text.event == "rain" and text.sender_name == ISSUER
    assert text.description == "Synthetic description of Heavy Rain Warning - Orange. Chance of upgrading to a Red Warning: Low."
    assert text.instruction == "Synthetic instruction. Keep an eye on the forecast."
    assert {(p.name, p.value) for p in text.parameters} == {
        ("ChanceOfUpgrade", "Low"),
        ("ColourCode", "Orange"),
        ("ColourCodeHex", "#FF8918"),
        ("NextUpdate", "2026-10-21T21:00:00+13:00"),
    }
    # 2026-10-21 12:00 in New Zealand (UTC+13) is 2026-10-20 23:00 UTC.
    assert warning.onset_utc == 1_792_537_200 and warning.sent_utc == 1_792_537_200 - 4 * 3600
    assert warning.id == "metservice:2.49.0.0.554.0.severeweather.nz.20261021080000000.11:0" and warning.area.has_polygon is True
    assert (warning.link, warning.web, warning.issuer) == (address, "https://metservice.com/warnings/home", ISSUER)
    assert warning.attribution == f"{credit_text('metservice_alerts')} | {COPYRIGHT} | Issued by {ISSUER}"
    assert warning.shape is not None and warning.shape.covers(Point(174.78, -41.29)) and not warning.shape.covers(Point(172.6, -43.5))


@pytest.mark.parametrize(("headline", "event", "colour", "level", "phenomenon"), DOCUMENTED, ids=WARNING_IDS)
def test_every_documented_warning_is_read_as_the_guide_says(
    headline: str, event: str, colour: str, level: SeverityLevel, phenomenon: VigilancePhenomenon
) -> None:
    # The severity the guide gives to a Severe Thunderstorm Watch is Moderate, and to a Watch of the other kinds Minor: neither decides.
    severity = {"Yellow": "Minor", "Orange": "Moderate", "Red": "Severe"}[colour]
    [warning] = parse_cap(synthetic_cap(event=event, headline=headline, colour=colour, severity=severity))
    assert (warning.level, warning.phenomenon, warning.level_original) == (level, phenomenon, colour)


def test_a_thunderstorm_watch_is_yellow_although_its_cap_severity_is_moderate() -> None:
    [warning] = parse_cap(synthetic_cap(event="thunderstorm", headline="Severe Thunderstorm Watch", colour="Yellow", severity="Moderate"))
    assert warning.level == SeverityLevel.YELLOW  # the guide's Table 4: Moderate here, yet yellow


def test_without_a_colour_code_the_headline_decides_and_the_cap_severity_is_kept_as_the_original_level() -> None:
    [warning] = parse_cap(synthetic_cap(colour=None))
    assert (warning.level, warning.level_original) == (SeverityLevel.ORANGE, "Moderate")
    assert "ColourCode" not in {p.name for p in warning.texts[0].parameters}


def test_a_colour_that_contradicts_the_headline_gives_no_level_and_keeps_the_original_word() -> None:
    [warning] = parse_cap(synthetic_cap(headline="Heavy Rain Warning - Orange", colour="Red"))
    assert (warning.level, warning.level_original) == (None, "Red")


def test_an_update_keeps_its_status_and_the_references_are_ignored() -> None:
    [warning] = parse_cap(synthetic_cap("update-2", msg_type="Update"))
    assert (warning.status, warning.message_type, warning.identifier) == (VigilanceStatus.UPDATE, "Update", "update-2")


def test_a_cancellation_keeps_its_status() -> None:
    # The guide says the feed holds no cancelled alert (2.3): the status is read all the same, in case one is ever served.
    [warning] = parse_cap(synthetic_cap(msg_type="Cancel"))
    assert warning.status == VigilanceStatus.CANCEL


def test_several_polygons_in_one_area_are_one_shape() -> None:
    [warning] = parse_cap(synthetic_cap(polygon=(WELLINGTON_BOX, CHRISTCHURCH_BOX)))  # "Multiple instances of <polygon> can occur" (guide 3.3.2)
    assert warning.shape is not None
    assert warning.shape.covers(Point(174.78, -41.29)) and warning.shape.covers(Point(172.64, -43.53)) and not warning.shape.covers(Point(170.5, -45.0))


def test_a_document_without_polygon_has_no_shape_and_says_so() -> None:
    # The guide says a polygon is always present: a document without one is still read, and localizes as "no polygon".
    [warning] = parse_cap(synthetic_cap(polygon=None))
    assert warning.shape is None and warning.area.has_polygon is False


@pytest.mark.parametrize("status", ["Test", "Exercise", "System", "Draft"])
def test_only_an_actual_alert_is_a_warning(status: str) -> None:
    assert parse_cap(synthetic_cap(status=status)) == []  # "only alerts with a status of Actual should be actioned" (guide 3.1.4)


@pytest.mark.parametrize(
    "content",
    [b"<html>maintenance</html>", b"not xml", b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><alert/>'],
    ids=["not a CAP alert", "not XML", "DTD"],
)
def test_what_is_not_a_cap_alert_raises(content: bytes) -> None:
    with pytest.raises(ValueError):
        parse_cap(content)


# ---------------------------------------------------------------- the reading of the feed


def test_an_item_gives_the_address_of_its_document_and_the_feed_its_licence_line() -> None:
    first, second = alert_address("2.49.0.0.554.0.severeweather.nz.20261021080000000.11"), alert_address("2.49.0.0.554.0.tswatch.nz.20261021080000000.3")
    assert parse_feed(synthetic_feed(first, second)) == ([first, second], 2, COPYRIGHT)


def test_a_feed_that_is_not_rss_raises() -> None:
    with pytest.raises(ValueError, match="not an RSS"):
        parse_feed(b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"/>')
    with pytest.raises(ValueError, match="not XML"):
        parse_feed(b"{}")


# ---------------------------------------------------------------- the collector on a synthetic feed


@pytest.mark.asyncio
async def test_each_document_of_the_feed_is_fetched_once_and_read(sim_clock: SimClock) -> None:
    rain, thunder = alert_address("rain-1"), alert_address("thunder-1")
    documents = {
        rain: synthetic_cap("rain-1"),
        thunder: synthetic_cap("thunder-1", event="thunderstorm", headline="Severe Thunderstorm Warning", colour="Red", severity="Severe"),
    }
    provider = server(synthetic_feed(rain, thunder), documents)
    collector = MetServiceCollector(http_client=provider.client())

    feed = (await collector.fetch_feed()).value
    assert provider.count == 3 and feed.items == 2 and feed.unreadable == 0
    assert [(w.id, w.level, w.link) for w in feed.vigilances] == [
        ("metservice:rain-1:0", SeverityLevel.ORANGE, rain),
        ("metservice:thunder-1:0", SeverityLevel.RED, thunder),
    ]

    sim_clock.advance(301)  # the feed is asked again (and answers 304): the documents are not fetched again
    await collector.fetch_feed()
    assert provider.count == 4 and provider.requests[-1].headers["If-Modified-Since"] == RECORDED_LAST_MODIFIED


@pytest.mark.asyncio
async def test_a_changed_feed_fetches_only_the_new_document_and_forgets_the_old_one(sim_clock: SimClock) -> None:
    first, second = alert_address("a"), alert_address("b")
    documents = {first: synthetic_cap("a"), second: synthetic_cap("b", event="wind", headline="Strong Wind Warning - Red", colour="Red")}
    current = {"feed": synthetic_feed(first)}

    def answer(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == FEED_URL:
            return httpx.Response(200, content=current["feed"])
        return httpx.Response(200, content=documents[url])

    provider = Provider(answer=answer, latency_ticks=0)
    collector = MetServiceCollector(http_client=provider.client())
    assert [w.identifier for w in (await collector.fetch_feed()).value.vigilances] == ["a"]
    current["feed"] = synthetic_feed(first, second)
    sim_clock.advance(301)
    feed = (await collector.fetch_feed()).value
    assert [w.identifier for w in feed.vigilances] == ["a", "b"]
    assert [str(r.url) for r in provider.requests].count(first) == 1  # "a" was not fetched again
    current["feed"] = synthetic_feed(second)
    sim_clock.advance(301)
    assert [w.identifier for w in (await collector.fetch_feed()).value.vigilances] == ["b"]  # "a" left the feed: gone


@pytest.mark.asyncio
async def test_what_cannot_be_read_is_counted_and_never_fetched_from_elsewhere(sim_clock: SimClock) -> None:
    good, missing, broken = alert_address("good"), alert_address("missing"), alert_address("broken")
    foreign = "https://evil.example/steal.cap"
    provider = server(
        synthetic_feed(good, missing, broken, foreign),
        {good: synthetic_cap("good"), broken: b"<html>oops</html>"},  # "missing" is a 404
    )
    collector = MetServiceCollector(http_client=provider.client())
    feed = (await collector.fetch_feed()).value
    assert [w.identifier for w in feed.vigilances] == ["good"]
    assert feed.items == 4 and feed.unreadable == 3
    assert foreign not in {str(r.url) for r in provider.requests}  # an address that is not MetService's is never requested


@pytest.mark.asyncio
async def test_a_feed_that_gives_a_link_in_its_guid_only_is_read(sim_clock: SimClock) -> None:
    # Defensive: the guide says the link is always there (the guid is the identifier), but a guid that is an address is read too.
    address = alert_address("guid")
    collector = MetServiceCollector(http_client=server(synthetic_feed(address, guid_is_link=True), {address: synthetic_cap("guid")}).client())
    assert [w.identifier for w in (await collector.fetch_feed()).value.vigilances] == ["guid"]


@pytest.mark.asyncio
async def test_a_failing_server_falls_back_on_the_last_feed_and_says_so(sim_clock: SimClock) -> None:
    address = alert_address("x")
    ok = server(synthetic_feed(address), {address: synthetic_cap("x")}, modified=None)
    state = {"down": False}

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502) if state["down"] else ok.answer(request)  # type: ignore[misc]

    collector = MetServiceCollector(http_client=Provider(answer=answer, latency_ticks=0).client())
    await collector.fetch_feed()
    state["down"] = True
    sim_clock.advance(301)
    fetched: Fetched[Any] = await collector.fetch_feed()
    assert fetched.stale is True and len(fetched.value.vigilances) == 1


def test_the_collector_is_a_warnings_provider_with_the_published_cadence() -> None:
    # 300 s: the feed's Cache-Control, and inside the guide's "at least every 5 minutes, not more often than every 2" (R.2).
    collector = MetServiceCollector()
    assert collector.provider_id == "metservice_cap" and collector.domain == "warnings" and collector.ttl_seconds == 300
