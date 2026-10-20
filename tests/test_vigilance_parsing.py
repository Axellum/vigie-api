"""Parsing of the official warnings: the MeteoAlarm Atom feeds and CAP documents, and the NWS answer.

The feeds are real ones, recorded on 2026-10-20 (``tests/fixtures/meteoalarm/``); nothing here reaches the network.
What is checked: the source's words are kept as given, our own fields (level, phenomenon, status) come from tables
and are marked as derived, and what is unknown stays ``None``.
"""

from __future__ import annotations

import pytest

from app.attribution import METEOALARM_DISCLAIMER, credit_text
from app.collectors.disasters import NWS_LEVELS, NOAANWSCollector, derive_nws_phenomenon
from app.collectors.meteoalarm import (
    CAP_URL_PREFIX,
    COUNTRY_FEEDS,
    LEVELS,
    PHENOMENA,
    derive_level,
    derive_phenomenon,
    parse_cap,
    parse_feed,
    with_cap,
)
from app.models.schemas import SeverityLevel
from app.models.vigilance import VigilancePhenomenon, VigilanceStatus
from tests import vigilance_samples as samples

# ---------------------------------------------------------------- the Atom feeds


def test_the_french_feed_gives_one_warning_per_area() -> None:
    feed = samples.feed("FR")
    assert len(feed.vigilances) == 19 and feed.skipped == 0
    assert feed.updated_utc == samples.RECORDED_AT
    drome = feed.vigilances[0]
    assert drome.id == "meteoalarm:b77d3bc0-76a2-4886-8da8-bac8d99f57fc:6-0-1"
    assert drome.source == "meteoalarm" and drome.country == "FR"
    assert drome.identifier == "2.49.0.0.250.1.FR.20261020114209.041024"
    assert (drome.sent_utc, drome.effective_utc, drome.onset_utc, drome.expires_utc) == (
        1792489329, 1792533600, 1792533600, 1792620000,
    )
    assert drome.area.description == "Drôme"
    assert [(g.scheme, g.value) for g in drome.area.geocodes] == [("EMMA_ID", "FR031"), ("NUTS3", "FR713")]
    assert drome.area.has_polygon is False and drome.shape is None
    assert drome.link == "https://feeds.meteoalarm.org/api/v1/warnings/feeds-france/b77d3bc0-76a2-4886-8da8-bac8d99f57fc"
    assert drome.web == "https://meteoalarm.org?geocode=EMMA_ID:FR031"


def test_the_source_words_are_kept_beside_our_normalization() -> None:
    drome = samples.feed("FR").vigilances[0]
    # What the source says, as given.
    assert (drome.level_original, drome.phenomenon_original, drome.event) == ("Yellow", "Flooding", "Moderate flooding warning")
    assert drome.message_type == "Alert"
    # What the API derived, and says so.
    assert (drome.level, drome.phenomenon, drome.status) == (SeverityLevel.YELLOW, VigilancePhenomenon.FLOOD, VigilanceStatus.NEW)
    assert drome.derived == ["status", "level", "phenomenon"]
    assert drome.issuer is None and drome.texts == []  # the words of the issuer are in the CAP document


def test_every_warning_carries_the_credit_of_meteoalarm() -> None:
    for country in ("FR", "IS", "CH", "DE", "BE", "IT", "NO"):
        for warning in samples.feed(country).vigilances:
            assert warning.attribution == credit_text("meteoalarm"), (country, warning.id)
    assert "meteoalarm.org" in credit_text("meteoalarm") and "CC BY 4.0" in credit_text("meteoalarm")


def test_the_warnings_of_one_cap_document_have_distinct_ids() -> None:
    ids = [w.id for w in samples.feed("FR").vigilances]
    assert len(ids) == len(set(ids))


def test_a_polygon_country_gives_the_shape_of_the_source() -> None:
    feed = samples.feed("CH")
    assert len(feed.vigilances) == 4
    for warning in feed.vigilances:
        assert warning.area.has_polygon and warning.shape is not None and warning.shape.geom_type == "Polygon"
        assert warning.area.geocodes == []


def test_a_country_that_mixes_codes_and_polygons_keeps_both() -> None:
    feed = samples.feed("IS")
    assert len(feed.vigilances) == 14
    by_polygon = [w for w in feed.vigilances if w.area.has_polygon]
    by_code = [w for w in feed.vigilances if not w.area.has_polygon]
    assert len(by_polygon) == 7 and all(w.shape is not None and not w.area.geocodes for w in by_polygon)
    assert len(by_code) == 7 and all(w.shape is None and [g.scheme for g in w.area.geocodes] == ["EMMA_ID"] for w in by_code)
    assert {(w.message_type, w.status) for w in feed.vigilances} == {("Update", VigilanceStatus.UPDATE)}


def test_belgium_names_its_areas_with_nuts2_and_emma_id() -> None:
    codes = {(g.scheme, g.value) for w in samples.feed("BE").vigilances for g in w.area.geocodes}
    assert {("NUTS2", "BE25"), ("NUTS2", "BE33"), ("NUTS2", "BE21"), ("EMMA_ID", "BE009")} <= codes


def test_an_empty_feed_is_a_valid_answer() -> None:
    feed = samples.feed("DK")
    assert feed.vigilances == () and feed.skipped == 0 and feed.updated_utc is not None


def test_a_feed_that_is_not_atom_is_refused() -> None:
    with pytest.raises(ValueError, match="not an Atom feed"):
        parse_feed(b"<html><body>Service unavailable</body></html>", "FR")
    with pytest.raises(ValueError):
        parse_feed(b"not xml at all", "FR")


def test_a_feed_whose_entries_are_all_broken_is_an_error_not_an_empty_list() -> None:
    broken = (
        b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:cap="urn:oasis:names:tc:emergency:cap:1.2">'
        b"<updated>2026-10-20T10:00:00Z</updated>"
        b"<entry><title>x</title><cap:status>Actual</cap:status><cap:identifier>a</cap:identifier>"
        b"<cap:polygon>not,a,polygon</cap:polygon><cap:sent>never</cap:sent></entry></feed>"
    )
    feed = parse_feed(broken, "FR")
    # An unreadable time or polygon is unknown, not a reason to drop the warning: the entry is still a warning.
    assert len(feed.vigilances) == 1
    only = feed.vigilances[0]
    assert only.sent_utc is None and only.level is None and only.phenomenon is None and only.area.has_polygon is False


def test_tests_and_exercises_are_not_warnings() -> None:
    feed = (
        b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:cap="urn:oasis:names:tc:emergency:cap:1.2">'
        b"<entry><title>Yellow Wind Warning issued for X</title><cap:status>Test</cap:status><cap:identifier>a</cap:identifier></entry>"
        b"<entry><title>Yellow Wind Warning issued for X</title><cap:status>Actual</cap:status></entry></feed>"
    )
    parsed = parse_feed(feed, "FR")
    assert parsed.vigilances == () and parsed.skipped == 2  # a test, and an entry with no identifier


@pytest.mark.parametrize(
    "document",
    [
        b'<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY a "aaaa">]><feed xmlns="http://www.w3.org/2005/Atom"></feed>',
        b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>&x;</title></feed><!ENTITY x "y">',
    ],
)
def test_a_dtd_or_an_entity_is_refused(document: bytes) -> None:
    with pytest.raises(ValueError, match="DTD"):
        parse_feed(document, "FR")
    with pytest.raises(ValueError, match="DTD"):
        parse_cap(document)


def test_a_country_without_a_feed_is_known_to_the_table() -> None:
    assert len(COUNTRY_FEEDS) == 39
    assert COUNTRY_FEEDS["FR"] == "france" and COUNTRY_FEEDS["MK"] == "republic-of-north-macedonia"
    assert "US" not in COUNTRY_FEEDS  # the United States come from the NWS


# ---------------------------------------------------------------- the tables


@pytest.mark.parametrize(
    ("word", "level"),
    [
        ("Green", SeverityLevel.GREEN),
        ("Yellow", SeverityLevel.YELLOW),
        ("orange", SeverityLevel.ORANGE),
        (" RED ", SeverityLevel.RED),
        ("Purple", None),
        ("", None),
        (None, None),
    ],
)
def test_meteoalarm_levels(word: str | None, level: SeverityLevel | None) -> None:
    assert derive_level(word) == level


@pytest.mark.parametrize(
    ("name", "phenomenon"),
    [
        ("Wind", VigilancePhenomenon.WIND),
        ("Snow-ice", VigilancePhenomenon.SNOW_ICE),
        ("Thunderstorm", VigilancePhenomenon.THUNDERSTORM),
        ("Fog", VigilancePhenomenon.FOG),
        ("High-temperature", VigilancePhenomenon.HEAT),
        ("Low-temperature", VigilancePhenomenon.COLD),
        ("CoastalEvent", VigilancePhenomenon.COASTAL),
        ("Forest-fire", VigilancePhenomenon.FIRE),
        ("Avalanches", VigilancePhenomenon.AVALANCHE),
        ("Rain", VigilancePhenomenon.RAIN),
        ("Flooding", VigilancePhenomenon.FLOOD),
        ("Rain-flood", VigilancePhenomenon.FLOOD),
        ("Marine-Hazard", VigilancePhenomenon.COASTAL),
        ("Drought", VigilancePhenomenon.OTHER),
        ("Volcano", None),
        (None, None),
    ],
)
def test_meteoalarm_phenomena(name: str | None, phenomenon: VigilancePhenomenon | None) -> None:
    assert derive_phenomenon(name) == phenomenon


def test_every_group_of_the_brief_is_reachable() -> None:
    reachable = set(PHENOMENA.values()) | {derive_nws_phenomenon(e) for e in ("Tornado Warning", "Red Flag Warning", "Dense Fog Advisory")}
    assert reachable == set(VigilancePhenomenon)
    assert set(LEVELS.values()) == set(SeverityLevel)


def test_an_unknown_word_is_none_never_a_guess() -> None:
    feed = (
        b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:cap="urn:oasis:names:tc:emergency:cap:1.2">'
        b"<entry><title>Mauve Tsunamis Warning issued for X</title><cap:status>Actual</cap:status>"
        b"<cap:identifier>a</cap:identifier><cap:message_type>Alert</cap:message_type></entry></feed>"
    )
    warning = parse_feed(feed, "FR").vigilances[0]
    assert (warning.level, warning.phenomenon) == (None, None)
    assert (warning.level_original, warning.phenomenon_original) == ("Mauve", "Tsunamis")  # the words are kept


@pytest.mark.parametrize(
    ("event", "phenomenon"),
    [
        ("Tornado Warning", VigilancePhenomenon.WIND),
        ("Hurricane Warning", VigilancePhenomenon.WIND),
        ("High Wind Warning", VigilancePhenomenon.WIND),
        ("Wind Chill Warning", VigilancePhenomenon.COLD),
        ("Flash Flood Warning", VigilancePhenomenon.FLOOD),
        ("Flood Watch", VigilancePhenomenon.FLOOD),
        ("Coastal Flood Warning", VigilancePhenomenon.COASTAL),
        ("Storm Surge Warning", VigilancePhenomenon.COASTAL),
        ("Severe Thunderstorm Warning", VigilancePhenomenon.THUNDERSTORM),
        ("Blizzard Warning", VigilancePhenomenon.SNOW_ICE),
        ("Winter Storm Warning", VigilancePhenomenon.SNOW_ICE),
        ("Ice Storm Warning", VigilancePhenomenon.SNOW_ICE),
        ("Dense Fog Advisory", VigilancePhenomenon.FOG),
        ("Freezing Fog Advisory", VigilancePhenomenon.FOG),
        ("Excessive Heat Warning", VigilancePhenomenon.HEAT),
        ("Hard Freeze Warning", VigilancePhenomenon.COLD),
        ("Red Flag Warning", VigilancePhenomenon.FIRE),
        ("Avalanche Warning", VigilancePhenomenon.AVALANCHE),
        ("Dust Storm Warning", VigilancePhenomenon.OTHER),
        ("", None),
        (None, None),
    ],
)
def test_nws_phenomena(event: str | None, phenomenon: VigilancePhenomenon | None) -> None:
    assert derive_nws_phenomenon(event) == phenomenon


def test_nws_levels_follow_the_cap_severity() -> None:
    assert NWS_LEVELS == {
        "minor": SeverityLevel.GREEN,
        "moderate": SeverityLevel.YELLOW,
        "severe": SeverityLevel.ORANGE,
        "extreme": SeverityLevel.RED,
    }


# ---------------------------------------------------------------- the CAP documents


def test_the_cap_document_gives_the_words_of_the_issuer_untouched() -> None:
    document = samples.cap()
    assert document.available and document.issuer == "METEO-FRANCE"
    languages = {text.language: text for text in document.texts}
    assert set(languages) == {"fr-FR", "en-GB"}
    french = languages["fr-FR"]
    assert french.headline == "Vigilance jaune inondations"
    assert french.event == "Vigilance jaune inondations" and french.sender_name == "METEO-FRANCE"
    assert french.description.startswith("Des phénomènes habituels dans la région mais occasionnellement")
    assert french.instruction.startswith("Soyez attentifs si vous pratiquez des activités sensibles")
    assert languages["en-GB"].headline == "Moderate flooding warning"


def test_a_document_in_many_languages_keeps_them_all() -> None:
    document = samples.cap("cap_germany_581d4ba4.xml")
    assert document.issuer == "Deutscher Wetterdienst"
    assert [text.language for text in document.texts] == ["de-DE", "en", "fr", "es", "ar", "ru", "tr", "pl"]


def test_adding_the_words_names_the_issuer_in_the_credit_and_keeps_the_rest() -> None:
    warning = samples.feed("FR").vigilances[0]
    enriched = with_cap(warning, samples.cap())
    assert enriched.issuer == "METEO-FRANCE" and len(enriched.texts) == 2
    assert enriched.attribution == f"{credit_text('meteoalarm')} | Issued by METEO-FRANCE"
    assert enriched.id == warning.id and enriched.level == warning.level and enriched.area == warning.area
    assert warning.texts == [] and warning.issuer is None  # the original is not changed


def test_languages_can_be_chosen_by_prefix() -> None:
    warning = samples.feed("FR").vigilances[0]
    only_french = with_cap(warning, samples.cap(), ("fr",))
    assert [t.language for t in only_french.texts] == ["fr-FR"]
    both = with_cap(warning, samples.cap(), ("fr", "en"))
    assert [t.language for t in both.texts] == ["fr-FR", "en-GB"]
    nothing = with_cap(warning, samples.cap(), ("ja",))
    assert nothing.texts == [] and nothing.issuer == "METEO-FRANCE"


def test_without_a_document_the_warning_is_unchanged() -> None:
    warning = samples.feed("FR").vigilances[0]
    assert with_cap(warning, None) == warning


def test_a_cap_document_that_is_not_cap_is_refused() -> None:
    with pytest.raises(ValueError, match="not a CAP alert"):
        parse_cap(b"<feed xmlns='http://www.w3.org/2005/Atom'/>")


def test_the_cap_links_of_the_feed_stay_on_the_address_that_is_fetched() -> None:
    assert all(w.link and w.link.startswith(CAP_URL_PREFIX) for w in samples.feed("FR").vigilances)


def test_the_mandatory_disclaimer_is_the_one_of_the_terms() -> None:
    assert METEOALARM_DISCLAIMER == (
        "Time delays between this website and the www.meteoalarm.org website are possible. For the most up-to-date "
        "awareness information as published by the participating National Meteorological and Hydrological Services, "
        "please refer to www.meteoalarm.org."
    )


# ---------------------------------------------------------------- NOAA NWS


def test_nws_alerts_become_warnings_with_the_words_of_the_forecast_office() -> None:
    warnings = NOAANWSCollector.parse_vigilances(samples.nws_payload())
    assert len(warnings) == 7
    flood = warnings[0]
    assert flood.id == "noaa_nws:" + flood.identifier and flood.source == "noaa_nws" and flood.country == "US"
    assert flood.event == flood.phenomenon_original == "Flood Warning"
    assert (flood.level, flood.level_original) == (SeverityLevel.ORANGE, "Severe")
    assert flood.phenomenon == VigilancePhenomenon.FLOOD
    assert (flood.message_type, flood.status) == ("Update", VigilanceStatus.UPDATE)
    assert {(w.message_type, w.status) for w in warnings} == {("Update", VigilanceStatus.UPDATE), ("Alert", VigilanceStatus.NEW)}
    assert [(g.scheme, g.value) for g in flood.area.geocodes] == [("UGC", "GAC025"), ("SAME", "013025")]
    assert flood.area.has_polygon and flood.shape is not None
    assert len(flood.texts) == 1 and flood.texts[0].headline and flood.texts[0].description
    assert flood.issuer and flood.attribution == f"{credit_text('noaa_nws')} | Issued by {flood.issuer}"


def test_nws_alerts_that_name_zones_have_no_polygon() -> None:
    warnings = NOAANWSCollector.parse_vigilances(samples.nws_payload())
    zoned = [w for w in warnings if not w.area.has_polygon]
    assert len(zoned) == 5 and all(w.shape is None and w.area.geocodes for w in zoned)


def test_nws_tests_and_missing_ids_are_not_warnings() -> None:
    payload = {
        "features": [
            {"id": "a", "properties": {"id": "a", "status": "Test", "event": "Tornado Warning"}, "geometry": None},
            {"properties": {"status": "Actual", "event": "Tornado Warning"}, "geometry": None},
            {"id": "c", "properties": {"id": "c", "status": "Actual", "event": "Tornado Warning", "severity": "Unknown"}, "geometry": None},
        ]
    }
    warnings = NOAANWSCollector.parse_vigilances(payload)
    assert [w.identifier for w in warnings] == ["c"]
    assert warnings[0].level is None and warnings[0].level_original == "Unknown"


def test_nws_polygon_holes_and_bad_geometry_do_not_break_the_list() -> None:
    payload = {
        "features": [
            {"id": "a", "properties": {"id": "a", "status": "Actual", "event": "Flood Warning"}, "geometry": {"type": "Polygon", "coordinates": "bad"}},
            {"id": "b", "properties": {"id": "b", "status": "Actual", "event": "Flood Warning"}, "geometry": None},
        ]
    }
    assert [w.identifier for w in NOAANWSCollector.parse_vigilances(payload)] == ["a", "b"]
