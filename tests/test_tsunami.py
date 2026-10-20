"""The tsunami messages of the U.S. Tsunami Warning Centers: the list of bulletins, the CAP messages, the events and the vigilances.

Everything is replayed from real recordings (``tests/fixtures/tsunami``, see its README):

- the **Panama earthquake of 2026-10-09** (magnitude 7.6, 17:56 UTC), as the Pacific centre (PTWC) told it: four threat messages
  from 18:02 to 19:40 UTC, the last one a "final" with no ``info``; the list of bulletins is replayed as it grew;
- the **Haida Gwaii earthquake of 2012-10-28** (magnitude 7.7), as the Alaska centre (NTWC) told it in its domestic messages:
  a warning, then advisories, then the cancellation, with UGC zone codes and VTEC codes.

The server is an ``httpx.MockTransport``; the clock is simulated.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Optional

import httpx
import pytest

from app.collectors import tsunami as T
from app.collectors.tsunami import (
    Bulletin,
    TsunamiCollector,
    alerts_of,
    country_of_codes,
    events_of,
    hazard_of,
    parse_listing,
    parse_message,
    parse_text_etas,
    vigilances_of,
    wanted_bulletins,
)
from app.models.schemas import HazardCategory, SeverityLevel
from app.models.vigilance import VigilanceStatus
from tests import storm_samples as S
from tests.helpers import SimClock, http_date

PANAMA = "2026-10-09"
CAP_NS = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}


def listing():
    return parse_listing(S.tsunami_fixture("previous.js").decode())


def iso(epoch: Optional[int]) -> Optional[str]:
    return None if epoch is None else datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


# ------------------------------------------------------------------------------------------------ the list of bulletins


def test_the_recorded_list_of_forty_bulletins_is_read() -> None:
    parsed = listing()
    assert len(parsed.bulletins) == 40 and parsed.unreadable == 0
    assert {b.twc for b in parsed.bulletins} == {"PHEB", "PAAQ"}
    newest = max(parsed.bulletins, key=lambda b: b.issued_utc)
    assert (newest.twc, newest.event_id, newest.wmo_id, newest.number, iso(newest.issued_utc)) == ("PAAQ", "tn7cbt", "WEAK53", 1, "2026-10-20 10:45")
    assert newest.path == "/events/PAAQ/2026/10/20/tn7cbt/1"
    assert newest.cap_url == "https://www.tsunami.gov/events/PAAQ/2026/10/20/tn7cbt/1/WEAK53/PAAQCAP.xml"
    assert newest.text_url == "https://www.tsunami.gov/events/PAAQ/2026/10/20/tn7cbt/1/WEAK53/WEAK53.txt"


@pytest.mark.parametrize("text", ["", "<html><body>maintenance</body></html>", "var other = {};", "{'event': []}"])
def test_what_is_not_the_list_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match="previous.js|prevEvents"):
        parse_listing(text)


def entry(**changes: str) -> str:
    values = {
        "id": "26282000", "issueTime": "2026-10-09 18:02:09.0", "URL": "/events/PHEB/2026/10/09/26282000/1", "TWCID": "PHEB",
        "WMOID": "WEPA40", "bulletinNumber": "1",
    }
    values.update(changes)
    body = ",\n".join(f"'{key}' : '{value}'" for key, value in values.items())
    return "{\n" + body + " }"


def listing_text(*entries: str) -> str:
    return "var prevEvents = {\n 'event' : [\n" + ",\n".join(entries) + "\n ]\n};"


def test_an_entry_that_does_not_read_is_counted_and_the_others_are_kept() -> None:
    parsed = parse_listing(listing_text(entry(), entry(TWCID="XXXX"), entry(URL="/events/PHEB/../../etc/passwd"), entry(WMOID="wepa40"),
                                        entry(id="a/b", URL="/events/PHEB/2026/10/09/a/b/1"), entry(issueTime="yesterday"), entry(bulletinNumber="x")))
    assert len(parsed.bulletins) == 1 and parsed.unreadable == 6


def test_a_list_where_nothing_reads_is_refused_but_an_empty_one_is_not() -> None:
    with pytest.raises(ValueError, match="none of its entries"):
        parse_listing(listing_text(entry(TWCID="XXXX")))
    assert parse_listing(listing_text()).bulletins == ()


def test_a_list_longer_than_it_ever_was_is_refused() -> None:
    with pytest.raises(ValueError, match="longer"):
        parse_listing(listing_text(*[entry()] * 2_001))


def wanted(label: str):
    return wanted_bulletins(listing(), S.at(PANAMA, label))


def test_only_the_latest_bulletin_of_each_product_of_an_event_in_the_last_three_hours_is_read() -> None:
    chosen, information = wanted("18:03")
    assert [(b.twc, b.event_id, b.wmo_id, b.number) for b in chosen] == [("PHEB", "26282000", "WEPA40", 1)]
    chosen, _ = wanted("18:55")
    assert [(b.wmo_id, b.number) for b in chosen] == [("WEPA40", 3)]  # the 3rd replaces the 2nd: one document, not three
    chosen, _ = wanted("19:42")
    assert [(b.wmo_id, b.number) for b in chosen] == [("WEPA40", 4)]  # the final: it must be read, it ends the alert
    chosen, _ = wanted("22:45")
    assert chosen == []  # three hours after the last: nothing is asked for any more


def test_a_bulletin_that_is_not_issued_yet_is_not_read() -> None:
    """A list from the future is a clock that is wrong somewhere, here or there: not trusted."""
    assert wanted("17:30")[0] == []
    assert wanted("17:30")[1] == 9  # nine information statements were already in the list


def test_information_statements_are_counted_and_never_read() -> None:
    """A tsunami information statement says no threat, or a hazard not determined: it is not an alert."""
    chosen, information = wanted_bulletins(listing(), S.at("2026-10-20", "10:50"))
    assert chosen == [] and information == 36  # of the 40 bulletins, 4 are threat messages (the Panama ones, long over) and 36 information statements
    assert all(b.wmo_id not in T.INFORMATION_PRODUCTS for b in chosen)


def test_the_bulleted_twin_of_a_product_is_left_out_when_the_segmented_one_is_there() -> None:
    def bulletin(wmo: str) -> str:
        return entry(WMOID=wmo, id="mcl1uw", TWCID="PAAQ", URL="/events/PAAQ/2012/10/28/mcl1uw/1", issueTime="2012-10-28 03:07:00.0")

    chosen, _ = wanted_bulletins(parse_listing(listing_text(bulletin("WEAK51"), bulletin("WEPA41"))), S.at("2012-10-28", "03:30"))
    assert [b.wmo_id for b in chosen] == ["WEPA41"]
    alone, _ = wanted_bulletins(parse_listing(listing_text(bulletin("WEAK51"))), S.at("2012-10-28", "03:30"))
    assert [b.wmo_id for b in alone] == ["WEAK51"]


# ------------------------------------------------------------------------------------------------ the CAP messages


def bulletin_of(twc: str, event: str, wmo: str, number: int, issued: str, day: str = PANAMA) -> Bulletin:
    return Bulletin(twc=twc, event_id=event, wmo_id=wmo, number=number, issued_utc=int(S.at(day, issued)), path=f"/events/{twc}/{day.replace('-', '/')}/{event}/{number}")


def panama(number: int) -> T.Message:
    issued = {1: "18:02", 2: "18:32", 3: "18:51", 4: "19:40"}[number]
    return parse_message(S.tsunami_fixture(f"PHEB_26282000_{number}_WEPA40_CAP.xml"), bulletin_of("PHEB", "26282000", "WEPA40", number, issued))


def haida_gwaii(number: int) -> T.Message:
    return parse_message(S.tsunami_fixture(f"PAAQ_mcl1uw_{number}_WEPA41_CAP.xml"), bulletin_of("PAAQ", "mcl1uw", "WEPA41", number, "03:07", "2012-10-28"))


def test_a_threat_message_of_the_pacific_centre_is_read_as_it_was_sent() -> None:
    message = panama(1)
    assert (message.identifier, message.status, message.message_type, iso(message.sent_utc)) == ("PHEB-1-26282000", "Actual", "Alert", "2026-10-09 18:02")
    info = message.infos[0]
    assert (info.event, info.severity, info.urgency, info.certainty) == ("Tsunami Potential Threat", "Unknown", "Unknown", "Unknown")
    assert info.description.startswith("PTWC TSUNAMI THREAT MESSAGE") and "Preliminary magnitude" in info.description
    assert info.sender_name == "NWS PACIFIC TSUNAMI WARNING CENTER HONOLULU HI"
    assert info.web == "http://www.tsunami.gov/events/PHEB/2026/10/09/26282000/1/WEPA40/WEPA40.txt"


def test_the_final_message_has_no_info_and_so_no_alert() -> None:
    final = panama(4)
    assert final.infos == () and final.status == "Actual"
    assert alerts_of(final, S.at(PANAMA, "19:45")) == []


def test_a_message_that_is_not_an_actual_one_is_never_an_alert() -> None:
    xml = S.tsunami_fixture("PHEB_26282000_1_WEPA40_CAP.xml").replace(b"<status>Actual</status>", b"<status>Exercise</status>")
    assert b"Exercise" in xml
    message = parse_message(xml, bulletin_of("PHEB", "26282000", "WEPA40", 1, "18:02"))
    assert alerts_of(message, S.at(PANAMA, "18:10")) == []
    assert alerts_of(panama(1), S.at(PANAMA, "18:10"))  # the real one is


def test_a_document_that_is_not_a_cap_alert_is_refused() -> None:
    b = bulletin_of("PHEB", "26282000", "WEPA40", 1, "18:02")
    for content in (b"<html><body>maintenance</body></html>", b"<feed xmlns='http://www.w3.org/2005/Atom'/>"):
        with pytest.raises((ValueError, ET.ParseError)):
            parse_message(content, b)
    no_id = S.tsunami_fixture("PHEB_26282000_1_WEPA40_CAP.xml").replace(b"<identifier>PHEB-1-26282000</identifier>", b"")
    with pytest.raises(ValueError, match="identifier"):
        parse_message(no_id, b)


def test_a_document_with_an_entity_declaration_is_refused() -> None:
    bomb = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaaaaaaaa">]><alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">&a;</alert>'
    with pytest.raises(ValueError, match="DTD"):
        parse_message(bomb, bulletin_of("PHEB", "26282000", "WEPA40", 1, "18:02"))
    with pytest.raises(ValueError, match="larger"):
        parse_message(b" " * 1_000_001, bulletin_of("PHEB", "26282000", "WEPA40", 1, "18:02"))


# ------------------------------------------------------------------------------------------------ the levels


def test_the_levels_of_the_pacific_threat_messages_follow_the_wave_height_class() -> None:
    """Potential threat and 1-3 m are ORANGE, 0.3-1 m YELLOW, over 3 m RED: ours, from the classes the centre writes."""
    assert T.THREAT_LEVELS == {
        "tsunami >3.0 meters": SeverityLevel.RED,
        "tsunami 1-3 meters": SeverityLevel.ORANGE,
        "tsunami potential threat": SeverityLevel.ORANGE,
        "tsunami 0.3-1 meters": SeverityLevel.YELLOW,
    }
    first = alerts_of(panama(1), S.at(PANAMA, "18:10"))
    assert [(a.level_original, a.level) for a in first] == [("Tsunami Potential Threat", SeverityLevel.ORANGE)]
    second = alerts_of(panama(2), S.at(PANAMA, "18:40"))
    assert sorted((a.level_original, a.level.value) for a in second) == [("Tsunami 0.3-1 meters", "YELLOW"), ("Tsunami 1-3 meters", "ORANGE")]


def test_the_mindanao_earthquake_of_june_2026_is_a_red_hazard_with_a_milder_class_beside_it() -> None:
    """The one real message with a class over 3 metres: magnitude 7.8 off Mindanao, the 5th message of the PTWC (2026-06-08)."""
    bulletin = bulletin_of("PHEB", "26158002", "WEPA40", 5, "02:48", "2026-06-08")
    message = parse_message(S.tsunami_fixture("PHEB_26158002_5_WEPA40_CAP.xml"), bulletin)
    assert [(i.event, [a.description for a in i.areas]) for i in message.infos] == [("Tsunami >3.0 meters", ["PHILIPPINES."]), ("Tsunami 0.3-1 meters", ["and MALAYSIA."])]
    events, _ = events_of([message], S.at("2026-06-08", "03:00"))
    hazard = hazard_of(events[0])
    assert (hazard.id, hazard.severity, hazard.title) == ("tsunami_pheb_26158002", SeverityLevel.RED, "Tsunami >3.0 meters - MINDANAO PHILIPPINES")
    assert (hazard.lat, hazard.lon) == (5.68, 125.204)
    origin = events[0].origin
    assert (origin.magnitude, origin.magnitude_type, origin.depth_km) == (7.8, "Mwp", 63.0)
    assert sorted((a.level_original, a.level.value) for a in events[0].alerts) == [("Tsunami 0.3-1 meters", "YELLOW"), ("Tsunami >3.0 meters", "RED")]
    etas = parse_text_etas(S.tsunami_fixture("PHEB_26158002_5_WEPA40.txt").decode("latin-1"), message.sent_utc)
    assert [(e.place, e.region) for e in etas] == [("TARAKAN", "INDONESIA"), ("LAHAD DATU", "MALAYSIA")]


def test_a_wave_height_class_that_is_in_no_table_is_no_alert_rather_than_a_guess() -> None:
    xml = S.tsunami_fixture("PHEB_26282000_1_WEPA40_CAP.xml").replace(b"Tsunami Potential Threat", b"Tsunami 5-10 meters")
    message = parse_message(xml, bulletin_of("PHEB", "26282000", "WEPA40", 1, "18:02"))
    assert alerts_of(message, S.at(PANAMA, "18:10")) == []


def test_a_threat_message_without_an_end_of_its_own_counts_for_three_hours() -> None:
    message = panama(1)
    assert message.infos[0].expires_utc is None
    assert alerts_of(message, S.at(PANAMA, "20:59"))  # sent 18:02: three hours is 21:02
    assert not alerts_of(message, S.at(PANAMA, "21:03"))


def test_the_levels_of_the_domestic_messages_are_the_centres_own_category() -> None:
    """Warning is RED, advisory ORANGE, watch YELLOW; information and cancellation are no alert. The CAP severity cannot tell advisory from watch."""
    assert T.DOMESTIC_LEVELS == {"warning": SeverityLevel.RED, "advisory": SeverityLevel.ORANGE, "watch": SeverityLevel.YELLOW}
    first = alerts_of(haida_gwaii(1), S.at("2012-10-28", "03:30"))
    assert [(a.level_original, a.level, a.info.severity) for a in first] == [("Warning", SeverityLevel.RED, "Extreme")]
    third = alerts_of(haida_gwaii(9), S.at("2012-10-28", "08:00"))
    assert [(a.level_original, a.level, a.info.severity) for a in third] == [("Advisory", SeverityLevel.ORANGE, "Moderate")] * 2  # the cancellation info is none
    assert alerts_of(haida_gwaii(13), S.at("2012-10-28", "12:00")) == []


# ------------------------------------------------------------------------------------------------ events, hazards, vigilances


def test_the_pacific_message_is_a_hazard_at_the_epicentre_and_not_an_all_clear() -> None:
    events, unplaced = events_of([panama(1)], S.at(PANAMA, "18:10"))
    assert unplaced == 0 and len(events) == 1
    hazard = hazard_of(events[0])
    assert (hazard.id, hazard.category, hazard.severity) == ("tsunami_pheb_26282000", HazardCategory.TSUNAMI, SeverityLevel.ORANGE)
    assert hazard.title == "Tsunami Potential Threat - PANAMA"
    assert (hazard.lat, hazard.lon) == (7.54, -80.75)
    assert iso(hazard.timestamp_utc) == "2026-10-09 17:56"  # the earthquake time, not the time of the message
    assert (hazard.source, hazard.sources, hazard.tsunami_warning, hazard.value_primary) == ("PTWC", ["PTWC"], True, None)
    assert hazard.attribution is not None and "Tsunami Warning Centers" in hazard.attribution and "Not endorsed" in hazard.attribution
    assert hazard.event_key is None  # an international message has no VTEC code to share with another source


def test_the_domestic_warning_is_red_and_carries_the_vtec_key_of_the_nws() -> None:
    events, _ = events_of([haida_gwaii(1)], S.at("2012-10-28", "03:30"))
    hazard = hazard_of(events[0])
    assert (hazard.id, hazard.severity, hazard.source) == ("tsunami_paaq_mcl1uw", SeverityLevel.RED, "NTWC")
    assert hazard.title == "Tsunami Warning - 25 miles S of Sandspit, British Columbia"
    assert (hazard.lat, hazard.lon) == (52.863, -131.942)
    assert hazard.event_key == "vtec:PAAQ.TS.0024" == T.vtec_event_key("/O.NEW.PAAQ.TS.W.0024.121028T0307Z-000000T0000Z/")


def test_the_vtec_key_is_the_office_the_phenomenon_and_the_number() -> None:
    assert T.vtec_event_key("/O.CON.PAAQ.TS.Y.0024.000000T0000Z-000000T0000Z/") == "vtec:PAAQ.TS.0024"  # advisory, same event
    assert T.vtec_event_key("/O.NEW.PHEB.TS.A.0003.261009T1802Z-000000T0000Z/") == "vtec:PHEB.TS.0003"
    assert T.vtec_event_key("/O.NEW.KLWX.TO.W.0123.261009T1802Z-261009T1830Z/") == "vtec:KLWX.TO.0123"
    assert T.vtec_event_key("") is None and T.vtec_event_key(None) is None and T.vtec_event_key("not a code") is None


def test_the_haida_gwaii_event_goes_from_warning_to_advisory_to_nothing() -> None:
    seen = []
    for number, label in ((1, "03:30"), (6, "05:50"), (9, "08:00"), (13, "12:00")):
        events, _ = events_of([haida_gwaii(number)], S.at("2012-10-28", label))
        seen.append([(hazard_of(e).severity.value, hazard_of(e).title.split(" - ")[0]) for e in events])
    assert seen == [[("RED", "Tsunami Warning")], [("RED", "Tsunami Warning")], [("ORANGE", "Tsunami Advisory")], []]


def test_the_vigilances_name_the_coasts_with_their_codes_and_keep_the_centres_words() -> None:
    message = haida_gwaii(6)
    events, _ = events_of([message], S.at("2012-10-28", "05:50"))
    vigilances = vigilances_of(events[0])
    assert [(v.id, v.country, v.level.value, v.level_original) for v in vigilances] == [
        ("tsunami:PAAQ-6-mcl1uw:0:US", "US", "RED", "Warning"),
        ("tsunami:PAAQ-6-mcl1uw:0:CA", "CA", "RED", "Warning"),
        ("tsunami:PAAQ-6-mcl1uw:1", "US", "ORANGE", "Advisory"),
        ("tsunami:PAAQ-6-mcl1uw:2", "CA", "ORANGE", "Advisory"),
    ]
    warning_us = vigilances[0]
    assert warning_us.source == "tsunami" and warning_us.status == VigilanceStatus.UPDATE and warning_us.message_type == "Update"
    assert iso(warning_us.expires_utc) == "2012-10-28 07:44" and warning_us.link == message.bulletin.cap_url
    assert {c.scheme for c in warning_us.area.geocodes} == {"UGC"} and not warning_us.area.has_polygon
    assert "Issued by" in (warning_us.attribution or "") and "Tsunami Warning Centers" in (warning_us.attribution or "")
    # The words are the centre's, character for character: compared with the XML read on its own.
    root = ET.fromstring(S.tsunami_fixture("PAAQ_mcl1uw_6_WEPA41_CAP.xml"))
    first_info = root.findall("cap:info", CAP_NS)[0]
    text = warning_us.texts[0]
    for field, tag in ((text.headline, "headline"), (text.description, "description"), (text.instruction, "instruction"), (text.event, "event")):
        assert field == first_info.findtext(f"cap:{tag}", namespaces=CAP_NS).strip(), tag
    assert text.sender_name == first_info.findtext("cap:senderName", namespaces=CAP_NS).strip()
    assert any(p.name == "VTEC" and p.value.startswith("/O.CON.PAAQ.TS.W.0024") for p in text.parameters)


def test_the_vigilance_of_an_international_message_names_countries_and_has_no_end_of_its_own() -> None:
    events, _ = events_of([panama(2)], S.at(PANAMA, "18:40"))
    vigilances = vigilances_of(events[0])
    assert sorted(v.level_original for v in vigilances) == ["Tsunami 0.3-1 meters", "Tsunami 1-3 meters"]
    one = next(v for v in vigilances if v.level_original == "Tsunami 1-3 meters")
    assert one.country is None and one.expires_utc is None  # no country code to give: the areas are words ("PANAMA...")
    assert one.area.description and "PANAMA" in one.area.description.upper()
    assert one.texts[0].description is not None and "Event details: Preliminary magnitude 7.6 (Mwp) earthquake" in one.texts[0].description


@pytest.mark.parametrize(
    ("codes", "country"),
    [((("UGC", "BCZ220"), ("UGC", "BCZ110")), "CA"), ((("UGC", "AKZ026"),), "US"), ((("UGC", "BCZ220"), ("UGC", "AKZ026")), "US"),
     ((("SAME", "002100"),), None), ((), None)],
)
def test_the_country_of_a_set_of_zone_codes(codes: tuple[tuple[str, str], ...], country: Optional[str]) -> None:
    assert country_of_codes(codes) == country


# ------------------------------------------------------------------------------------------------ arrival times


def test_the_arrival_times_of_a_domestic_message_come_with_the_coordinates_of_their_place() -> None:
    etas = haida_gwaii(1).etas
    langara = next(e for e in etas if e.place == "Langara Island, British Columbia")
    assert iso(langara.eta_utc) == "2012-10-28 04:16" and (langara.lat, langara.lon) == (54.266701, -133.066696) and langara.source == "cap"
    assert len(etas) == 26


def test_the_arrival_times_of_a_threat_message_are_read_from_its_text_table() -> None:
    text = S.tsunami_fixture("PHEB_26282000_3_WEPA40.txt").decode("latin-1")
    etas = parse_text_etas(text, panama(3).sent_utc)
    assert [(e.place, e.region, e.lat, e.lon, iso(e.eta_utc), e.source) for e in etas] == [
        ("PUNTA MALA", "PANAMA", 7.5, -80.0, "2026-10-09 18:20", "text"),
        ("PUERTO PINA", "PANAMA", 7.4, -78.0, "2026-10-09 18:31", "text"),
        ("PUNTA BURICA", "PANAMA", 8.0, -82.9, "2026-10-09 18:40", "text"),
        ("BALBOA HEIGHTS", "PANAMA", 9.0, -79.6, "2026-10-09 20:41", "text"),
    ]


def test_a_text_without_a_table_or_with_a_broken_one_gives_no_arrival_time() -> None:
    assert parse_text_etas("TSUNAMI MESSAGE NUMBER 1\nNO FORECAST YET", 1_791_000_000) == []
    table = "    LOCATION         REGION             COORDINATES    ETA(UTC)\n    ------\n    PUNTA MALA       PANAMA             7.5N  80.0W   2561 10/09\n"
    assert parse_text_etas(table, int(S.at(PANAMA, "18:51"))) == []  # 25:61 is no time of day
    assert parse_text_etas(S.tsunami_fixture("PHEB_26282000_3_WEPA40.txt").decode(), None) == []


# ------------------------------------------------------------------------------------------------ the collector, a day at a time


class TsunamiServer:
    """tsunami.gov as it was during the Panama event: the list grows, every document is the recorded one."""

    def __init__(self, clock: SimClock) -> None:
        self.clock = clock
        self.requests: list[tuple[str, Optional[str], int]] = []  # (path, If-Modified-Since, status)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == S.TSUNAMI_LISTING_PATH:
            body, modified = S.tsunami_listing_at(self.clock.time())
            since = request.headers.get("If-Modified-Since")
            if modified and since == http_date(modified):
                self.requests.append((path, since, 304))
                return httpx.Response(304, headers={"Last-Modified": http_date(modified)})
            self.requests.append((path, since, 200))
            return httpx.Response(200, content=body, headers={"Last-Modified": http_date(modified)} if modified else {})
        document = S.tsunami_document(path)
        self.requests.append((path, None, 200 if document is not None else 404))
        return httpx.Response(200, content=document) if document is not None else httpx.Response(404)

    def documents(self) -> list[str]:
        return [path for path, _, _ in self.requests if path != S.TSUNAMI_LISTING_PATH]


def replay(sim_clock: SimClock, day: str = PANAMA, start: str = "17:00") -> tuple[TsunamiCollector, TsunamiServer, Callable[[str], Awaitable[list]]]:
    sim_clock.EPOCH = S.at(day, start)
    server = TsunamiServer(sim_clock)
    collector = TsunamiCollector(http_client=httpx.AsyncClient(transport=httpx.MockTransport(server)))

    async def at(label: str) -> list:
        sim_clock.now = S.at(day, label) - sim_clock.EPOCH
        return await collector.fetch()

    return collector, server, at


@pytest.mark.asyncio
async def test_the_panama_earthquake_from_the_first_message_to_the_final_one(sim_clock: SimClock) -> None:
    collector, server, at = replay(sim_clock)

    assert await at("17:30") == []  # the list holds information statements only: no alert, and not an "all clear" either
    assert collector.last_state.information_statements == 9 and collector.last_state.events == ()

    first = await at("18:03")  # one minute after the first message of the PTWC
    assert [(h.id, h.severity.value, h.title) for h in first] == [("tsunami_pheb_26282000", "ORANGE", "Tsunami Potential Threat - PANAMA")]
    assert len(collector.last_state.events[0].etas) == 25  # the first message already has the table of arrival times

    second = await at("18:35")
    assert [(h.severity.value, h.title) for h in second] == [("ORANGE", "Tsunami 1-3 meters - PANAMA")]
    assert [a.level_original for a in collector.last_state.events[0].alerts] == ["Tsunami 1-3 meters", "Tsunami 0.3-1 meters"]

    third = await at("18:55")
    assert [h.title for h in third] == ["Tsunami 1-3 meters - PANAMA"]
    event = collector.event_for("tsunami_pheb_26282000")
    assert event is not None and [(e.place, iso(e.eta_utc)) for e in event.etas][:2] == [("PUNTA MALA", "2026-10-09 18:20"), ("PUERTO PINA", "2026-10-09 18:31")]
    assert collector.event_for("tsunami_pheb_x") is None

    assert await at("19:42") == []  # the final message has no info: the alert is over, whatever the earlier ones said
    assert collector.last_state.events == ()
    assert await at("23:00") == []


@pytest.mark.asyncio
async def test_each_document_is_asked_for_once_and_the_list_only_when_it_changed(sim_clock: SimClock) -> None:
    collector, server, at = replay(sim_clock)
    for label in ("17:30", "18:03", "18:04:05", "18:05:10", "18:06:15", "18:10", "18:35", "18:55", "19:42", "19:43:05", "22:45"):
        await at(label)
    documents = server.documents()
    assert len(documents) == len(set(documents)), "a document was asked for twice"
    # Four CAP messages, one per bulletin worth reading; and the text bulletin of the threat messages (their arrival times).
    assert sorted(p.split("/")[-3] for p in documents if p.endswith("CAP.xml")) == ["1", "2", "3", "4"]
    assert sorted(p.split("/")[-3] for p in documents if p.endswith(".txt")) == ["1", "2", "3"]  # the final message has no table to read
    assert all(p.endswith(("CAP.xml", "WEPA40.txt")) for p in documents)
    # No information statement was ever downloaded: the list says what they are.
    assert not any(wmo in p for p in documents for wmo in ("WEAK53", "WEPA42", "WEHW42", "WEZS42", "WEGM42"))
    listing_requests = [(since, status) for path, since, status in server.requests if path == S.TSUNAMI_LISTING_PATH]
    assert listing_requests[0] == (None, 200)
    assert all(since is not None for since, _ in listing_requests[1:])  # every other look is conditional
    assert sum(1 for _, status in listing_requests if status == 304) >= 2  # ...and some of them found nothing new


@pytest.mark.asyncio
async def test_a_document_that_is_missing_is_left_out_and_asked_again_after_a_minute(sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch) -> None:
    collector, server, at = replay(sim_clock)
    real = S.tsunami_document
    broken = {"on": True}
    monkeypatch.setattr(S, "tsunami_document", lambda path: None if broken["on"] and path.endswith("1/WEPA40/PHEBCAP.xml") else real(path))
    assert await at("18:03") == []  # the one message of the event could not be read: no hazard, and it is counted
    assert collector.last_state.unreadable == 1
    broken["on"] = False
    assert await at("18:03:30") == []  # the cache of the list is 60 seconds, so nothing is asked yet
    assert [h.severity.value for h in await at("18:04:10")] == ["ORANGE"]
    cap_requests = [p for p in server.documents() if p.endswith("1/WEPA40/PHEBCAP.xml")]
    assert len(cap_requests) == 2  # asked again, once, after the minute of rest


@pytest.mark.asyncio
async def test_the_list_that_is_not_the_list_is_a_failure_of_the_source(sim_clock: SimClock) -> None:
    from app.collectors.base import CollectorUnavailableError

    sim_clock.EPOCH = S.at(PANAMA, "18:00")
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html><body>maintenance</body></html>")))
    with pytest.raises(CollectorUnavailableError):
        await TsunamiCollector(http_client=client).fetch()


@pytest.mark.asyncio
async def test_the_vigilances_of_the_collector_are_the_alerts_of_its_last_answer(sim_clock: SimClock) -> None:
    collector, server, at = replay(sim_clock)
    await at("18:03")
    fetched = await collector.fetch_vigilances()
    assert [v.level_original for v in fetched.value] == ["Tsunami Potential Threat"] and fetched.stale is False
    requests = len(server.requests)
    await collector.fetch_vigilances()
    assert len(server.requests) == requests  # they come with the hazards': no request of their own
    await at("19:42")
    assert collector.vigilances() == []
