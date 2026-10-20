"""One storm, one tsunami: the same event reported by several sources is one hazard, and one vigilance.

What is real here:

- **Isaias**: the National Hurricane Center's own record of 2026-10-09 23:58 UTC (``tests/fixtures/nhc``) and GDACS's own record of the
  same storm, read from its search API on 2026-10-20 (``tests/fixtures/gdacs``: ``ISAIAS-26``, event 1001334, from 2026-10-06 21:00,
  last position 37.5 N 84.0 W, 194 km/h). GDACS no longer follows the storm (``iscurrent`` is ``false`` now): the tests set it
  back to ``true``, which is what the record said while the storm lasted. The two records are 3.5 days and 760 km apart in time and
  place, which is why the position and the time cannot tell they are one storm and the name and the season can.
- **Haida Gwaii 2012**: the Alaska centre's own warning (``tests/fixtures/tsunami``). The NWS relays such a message as an alert of its
  own; the record of that relay was not kept, so the tests build it **from the message** (its text, its UGC zones, its VTEC code, its
  time), the way api.weather.gov writes a tsunami warning; the polygon is the test's.
- GDACS had no tsunami event (``TS``) for the Panama earthquake (the search answers HTTP 204 for ``TS`` since 2026-09-01, read
  2026-10-20), so the GDACS ``TS`` record of the last test is **shaped** like the real ones, not a recording.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.collectors import tsunami as T
from app.collectors.disasters import GDACSCollector, NOAANWSCollector
from app.collectors.nhc import hazard_of as storm_hazard
from app.collectors.nhc import parse_current_storms
from app.models.schemas import HazardAlert, HazardCategory, SeverityLevel
from app.services.geodesy import deduplicate_hazards
from tests import storm_samples as S
from tests.test_tsunami import haida_gwaii

GDACS_TC = json.loads((S.NHC.parent / "gdacs" / "geteventlist_TC_2026-10-20.json").read_text(encoding="utf-8"))


def gdacs_storm(name: str, current: bool = True) -> HazardAlert:
    """The GDACS hazard of a storm of the recorded answer (``ISAIAS-26``...), read by the collector's own parser."""
    payload = json.loads(json.dumps(GDACS_TC))
    payload["features"] = [f for f in payload["features"] if f["properties"]["eventname"] == name]
    for feature in payload["features"]:
        feature["properties"]["iscurrent"] = "true" if current else "false"
    parsed = GDACSCollector.parse_geojson(payload)
    assert len(parsed) == 1, name
    return parsed[0]


def nhc_storm(name: str) -> HazardAlert:
    storm = next(s for s in parse_current_storms(S.current_storms()).storms if s.name == name)
    return storm_hazard(storm)


# ------------------------------------------------------------------------------------------------ the storms


def test_the_gdacs_record_of_isaias_is_far_from_the_nhc_one_in_time_and_place() -> None:
    gdacs, nhc = gdacs_storm("ISAIAS-26"), nhc_storm("Isaias")
    assert (gdacs.id, gdacs.source, gdacs.severity) == ("gdacs_tc_1001334", "GDACS", SeverityLevel.ORANGE)
    assert (gdacs.lat, gdacs.lon, gdacs.value_primary, gdacs.value_unit) == (37.5, -84.0, 194.4, "km/h")
    assert gdacs.event_key == nhc.event_key == "cyclone:isaias:2026"
    assert abs(gdacs.timestamp_utc - nhc.timestamp_utc) > 3 * 86_400  # GDACS dates the storm by its first day, the NHC by its advisory


def test_the_same_storm_from_gdacs_and_the_nhc_is_one_hazard_the_nhcs() -> None:
    merged = deduplicate_hazards([gdacs_storm("ISAIAS-26"), nhc_storm("Isaias")])
    assert len(merged) == 1
    storm = merged[0]
    assert storm.id == "nhc_al092026"  # the id the forecast route answers to
    assert sorted(storm.sources) == ["GDACS", "NHC"] and storm.category == HazardCategory.HURRICANE
    # The storm of now: the NHC's position, wind and level, not GDACS's last position and its peak (194 km/h).
    assert (storm.lat, storm.lon, storm.title) == (30.1, -86.6, "Hurricane Isaias")
    assert (storm.value_primary, storm.value_unit, storm.severity) == (pytest.approx(175.9), "km/h", SeverityLevel.ORANGE)
    assert storm.event_key == "cyclone:isaias:2026"
    assert "Global Disaster Alert and Coordination System" in (storm.attribution or "") and "National Hurricane Center" in (storm.attribution or "")  # both are credited


def test_the_order_in_which_the_sources_answer_does_not_matter() -> None:
    a = deduplicate_hazards([gdacs_storm("ISAIAS-26"), nhc_storm("Isaias")])[0]
    b = deduplicate_hazards([nhc_storm("Isaias"), gdacs_storm("ISAIAS-26")])[0]
    assert (a.id, a.lat, a.lon, a.value_primary, a.severity) == (b.id, b.lat, b.lon, b.value_primary, b.severity)


def test_a_storm_that_gdacs_alone_follows_stays_as_it_is() -> None:
    merged = deduplicate_hazards([gdacs_storm("CHAMPI-26"), nhc_storm("Isaias")])
    assert sorted(h.id for h in merged) == ["gdacs_tc_1001338", "nhc_al092026"]


def test_two_storms_of_the_same_name_in_two_seasons_are_two_storms() -> None:
    old = gdacs_storm("ISAIAS-26").model_copy()
    old = old.with_event_key("cyclone:isaias:2020")  # the Atlantic reuses its names every six years
    assert len(deduplicate_hazards([old, nhc_storm("Isaias")])) == 2


def test_the_key_comes_from_the_event_name_and_a_name_without_a_year_gives_none() -> None:
    from app.collectors.disasters import _gdacs_cyclone_key

    assert _gdacs_cyclone_key({"eventname": "ISAIAS-26"}) == "cyclone:isaias:2026"
    assert _gdacs_cyclone_key({"eventname": "CHOI-WAN-26"}) == "cyclone:choi-wan:2026"
    assert _gdacs_cyclone_key({"eventname": "TWENTYEIGHT-26"}) == "cyclone:twentyeight:2026"
    assert _gdacs_cyclone_key({"eventname": "ISAIAS"}) is None and _gdacs_cyclone_key({"eventname": ""}) is None and _gdacs_cyclone_key({}) is None
    assert _gdacs_cyclone_key({"eventname": "ONE-26"}) == "cyclone:one:2026"


def test_a_gdacs_cyclone_without_a_usable_name_keeps_the_old_rule_of_time_and_place() -> None:
    """No key: two reports are one event when within two minutes and a hundred kilometres, as before."""
    nhc = nhc_storm("Isaias")
    near = gdacs_storm("ISAIAS-26").model_copy(update={"lat": nhc.lat + 0.2, "lon": nhc.lon, "timestamp_utc": nhc.timestamp_utc + 60})
    assert near.event_key is not None
    unkeyed = HazardAlert(**{**near.model_dump(), "id": "gdacs_tc_x"})  # a copy without the private key
    assert unkeyed.event_key is None
    assert len(deduplicate_hazards([unkeyed, nhc])) == 1
    far = unkeyed.model_copy(update={"timestamp_utc": nhc.timestamp_utc + 86_400})
    assert len(deduplicate_hazards([far, nhc])) == 2


def test_an_earthquake_is_still_merged_the_old_way_and_keeps_its_highest_level() -> None:
    def quake(source: str, magnitude: float, level: SeverityLevel, dt: int) -> HazardAlert:
        return HazardAlert(id=f"{source}_1", category=HazardCategory.EARTHQUAKE, severity=level, title="M7", source=source, sources=[source],
                           timestamp_utc=1_791_000_000 + dt, lat=7.5, lon=-80.7, value_primary=magnitude, value_unit="Mw")

    merged = deduplicate_hazards([quake("USGS", 7.6, SeverityLevel.ORANGE, 0), quake("EMSC", 7.7, SeverityLevel.RED, 30)])
    assert len(merged) == 1 and merged[0].severity == SeverityLevel.RED and merged[0].value_primary == 7.7 and merged[0].id == "USGS_1"


# ------------------------------------------------------------------------------------------------ the tsunamis


def nws_tsunami_warning(polygon: bool = True) -> dict[str, Any]:
    """The NWS alert that relays the first message of the Haida Gwaii event, made from that message (see the top of this file)."""
    message = haida_gwaii(1)
    info = message.infos[0]
    vtec = next(v for k, v in info.parameters if k == "VTEC")
    ugc = [code for area in info.areas for scheme, code in area.geocodes if scheme == "UGC" and not code.startswith("BC")]
    sent = "2012-10-28T03:07:00+00:00"
    box = [[-135.0, 55.0], [-122.0, 55.0], [-122.0, 38.0], [-135.0, 38.0], [-135.0, 55.0]]
    return {
        "id": "https://api.weather.gov/alerts/urn:oid:2.49.0.1.840.0.2012.1028.0307.WEAK51",
        "geometry": {"type": "Polygon", "coordinates": [box]} if polygon else None,
        "properties": {
            "id": "urn:oid:2.49.0.1.840.0.2012.1028.0307.WEAK51",
            "event": "Tsunami Warning", "severity": "Extreme", "status": "Actual", "messageType": "Alert",
            "sent": sent, "effective": sent, "expires": "2012-10-28T05:07:00+00:00",
            "senderName": "NWS National Tsunami Warning Center Palmer AK",
            "areaDesc": "Coastal areas between and including the north tip of Vancouver Island and Point Conception",
            "headline": info.headline, "description": info.description, "instruction": info.instruction,
            "geocode": {"UGC": ugc, "SAME": []},
            "parameters": {"VTEC": [vtec], "AWIPSidentifier": ["TSUAK1"]},
        },
    }


def ntwc_hazard() -> HazardAlert:
    events, _ = T.events_of([haida_gwaii(1)], S.at("2012-10-28", "03:30"))
    return T.hazard_of(events[0])


def test_the_nws_tsunami_alert_is_a_hazard_with_the_vtec_key_of_the_message_it_relays() -> None:
    nws = NOAANWSCollector.parse_alerts({"features": [nws_tsunami_warning()]})
    assert len(nws) == 1 and nws[0].category == HazardCategory.TSUNAMI
    assert nws[0].event_key == "vtec:PAAQ.TS.0024" == ntwc_hazard().event_key
    assert NOAANWSCollector.parse_alerts({"features": [nws_tsunami_warning(polygon=False)]}) == []  # no polygon, no position, no hazard


def test_a_tsunami_that_the_centre_and_the_nws_both_report_is_one_hazard_the_centres() -> None:
    nws = NOAANWSCollector.parse_alerts({"features": [nws_tsunami_warning()]})[0]
    centre = ntwc_hazard()
    # The NWS places its warning at the centre of its zones, the centre at the epicentre: far apart, and they are one event.
    from app.services.geodesy import haversine_km

    assert haversine_km(nws.lat, nws.lon, centre.lat, centre.lon) > 500
    merged = deduplicate_hazards([nws, centre])
    assert len(merged) == 1
    assert (merged[0].id, merged[0].lat, merged[0].lon, merged[0].severity) == ("tsunami_paaq_mcl1uw", 52.863, -131.942, SeverityLevel.RED)
    assert sorted(merged[0].sources) == ["NOAA_NWS", "NTWC"]
    assert deduplicate_hazards([centre, nws])[0].id == "tsunami_paaq_mcl1uw"


def test_another_tsunami_of_the_same_office_is_another_event() -> None:
    other = NOAANWSCollector.parse_alerts({"features": [nws_tsunami_warning()]})[0].with_event_key("vtec:PAAQ.TS.0025")
    assert len(deduplicate_hazards([other, ntwc_hazard()])) == 2


def test_an_nws_alert_that_is_not_a_tsunami_never_gets_a_tsunami_key() -> None:
    feature = nws_tsunami_warning()
    feature["properties"].update({"event": "Tornado Warning", "severity": "Severe"})
    assert NOAANWSCollector.parse_alerts({"features": [feature]})[0].event_key is None


def test_a_gdacs_tsunami_of_the_same_place_and_time_is_merged_by_the_old_rule() -> None:
    """The PTWC message and a GDACS ``TS`` event for the same earthquake (a record shaped like the real ones, see the top of this file)."""
    events, _ = T.events_of([_panama_1()], S.at("2026-10-09", "18:10"))
    centre = T.hazard_of(events[0])
    payload = {
        "features": [
            {
                "geometry": {"type": "Point", "coordinates": [-80.75, 7.54]},
                "properties": {
                    "eventtype": "TS", "eventid": 1001340, "eventname": "", "name": "Tsunami in Panama", "alertlevel": "Orange",
                    "iscurrent": "true", "country": "Panama", "fromdate": "2026-10-09T17:56:08", "severitydata": {"severity": 1.5, "severityunit": "m"},
                },
            }
        ]
    }
    gdacs = GDACSCollector.parse_geojson(payload)[0]
    assert gdacs.category == HazardCategory.TSUNAMI and gdacs.event_key is None and centre.event_key is None
    merged = deduplicate_hazards([gdacs, centre])
    assert len(merged) == 1 and merged[0].id == "tsunami_pheb_26282000"  # the centre's id and title
    assert merged[0].title == "Tsunami Potential Threat - PANAMA" and sorted(merged[0].sources) == ["GDACS", "PTWC"]
    assert merged[0].value_primary == 1.5  # the old rule: the best value known (no key, so the first-hand rule does not apply)


def _panama_1() -> T.Message:
    from tests.test_tsunami import panama

    return panama(1)
