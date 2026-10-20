"""Reading the CAP documents of the services of the WMO registry: the recorded ones (``tests/fixtures/swic/``) and a few built here.

A test on a recorded document says so (``cap_*.xml``); a test on a document written here says it is **synthetic**: no service
sent it. The tables of the API (level, phenomenon) are tested on the words the services really wrote.
"""

from __future__ import annotations

import pytest
from shapely.geometry import Point

from app.collectors import swic_cap as cap
from app.models.schemas import SeverityLevel
from app.models.vigilance import VigilancePhenomenon, VigilanceStatus
from tests import swic_samples as samples

RECORDED = samples.RECORDED_AT


def warnings_of(file: str, now: float = RECORDED) -> list:
    """The warnings of a recorded document, whether or not they are in force at ``now`` (the first group only: see the tests)."""
    alert = cap.parse_alert(samples.fixture(file), samples.document_url(file))
    out = []
    for variant, group in enumerate(cap.group_translations(alert.infos)):
        out.extend(cap.to_vigilances(alert, group, variant, source_id="xx-test-en", country="XX", credit="Source: test"))
    return out


# ---------------------------------------------------------------------------------------------------- every family of format


@pytest.mark.parametrize("file", samples.alert_files())
def test_every_recorded_document_is_a_cap_alert(file: str) -> None:
    alert = cap.parse_alert(samples.fixture(file), "https://example.org/" + file)
    assert alert.identifier and alert.sent_utc is not None
    assert alert.status in ("actual", "test") and alert.scope == "public"


def test_the_recorded_families_are_all_there() -> None:
    """One document at least for each way of writing CAP that was seen on 2026-10-20 (README of the folder)."""
    files = set(samples.alert_files())
    assert {
        "cap_es_aemet_alert.xml",  # no CAP namespace prefix issue, two languages in one document, colour parameter
        "cap_mx_smn_orange.xml",  # severity only, one language, a polygon
        "cap_dz_onm_rain.xml",  # French and English infos in one document
        "cap_ph_pagasa_1.xml",  # codes only (SAME), Minor severity, an Update
        "cap_in_ndma_geocodes.xml",  # codes only (LGD districts)
        "cap_ru_en_orange.xml",  # one document per language, an identifier each (.EN, .RU)
        "cap_kz_en.xml",  # one document per language, an identifier each
        "cap_by_cancel_1.xml",  # a Cancel with no info at all
        "cap_co_ungrd_test_1.xml",  # status Test
        "cap_th_tmd_67_polygons.xml",  # 67 polygons in one area
        "cap_uy_inumet_two_infos.xml",  # two different warnings in one document
        "cap_kw_dom_arabic_english.xml",  # Arabic
        "cap_no_met_update.xml",  # an Update that replaces two alerts
    } <= files


# ---------------------------------------------------------------------------------------------------- the tables


@pytest.mark.parametrize(
    ("file", "level", "original", "phenomenon"),
    [
        ("cap_es_aemet_alert.xml", SeverityLevel.YELLOW, "amarillo", VigilancePhenomenon.WIND),
        ("cap_es_aemet_update_1.xml", SeverityLevel.ORANGE, "naranja", VigilancePhenomenon.RAIN),
        ("cap_mx_smn_orange.xml", SeverityLevel.ORANGE, "Severe", VigilancePhenomenon.RAIN),
        ("cap_mx_smn_red.xml", SeverityLevel.RED, "Extreme", VigilancePhenomenon.RAIN),
        ("cap_ar_smn_update_1.xml", SeverityLevel.YELLOW, "Moderate", VigilancePhenomenon.RAIN),
        ("cap_dz_onm_rain.xml", SeverityLevel.YELLOW, "Moderate", VigilancePhenomenon.RAIN),
        ("cap_dz_onm_thunderstorm.xml", SeverityLevel.YELLOW, "Moderate", VigilancePhenomenon.THUNDERSTORM),
        # The colour an issuer writes in its own parameter is its level, and wins over the CAP severity ("Alerta Roja" with Moderate).
        ("cap_co_ungrd_test_1.xml", SeverityLevel.RED, "Alerta Roja", VigilancePhenomenon.FIRE),
        ("cap_no_met_update.xml", SeverityLevel.YELLOW, "2; yellow; Moderate", VigilancePhenomenon.WIND),
        # Minor is no level: the services do not agree on what it is (a tropical cyclone signal 1 is "Minor").
        ("cap_ph_pagasa_1.xml", None, "Minor", VigilancePhenomenon.WIND),
        ("cap_tt_ttms_cancel.xml", None, "Minor", None),
        # A word in no table is null, never a guess: "Low water", "Adverse Weather", and Russian words.
        ("cap_ru_en_yellow.xml", SeverityLevel.YELLOW, "Moderate", None),
        ("cap_tt_ttms_alert.xml", SeverityLevel.YELLOW, "Moderate", None),
        ("cap_ru_ru_orange.xml", SeverityLevel.ORANGE, "Severe", None),
        ("cap_ru_en_orange.xml", SeverityLevel.ORANGE, "Severe", VigilancePhenomenon.FIRE),
        ("cap_th_tmd_67_polygons.xml", SeverityLevel.RED, "Extreme", VigilancePhenomenon.RAIN),
        ("cap_in_ndma_geocodes.xml", SeverityLevel.YELLOW, "Moderate", VigilancePhenomenon.THUNDERSTORM),
        ("cap_kw_dom_arabic_english.xml", SeverityLevel.YELLOW, "Moderate", VigilancePhenomenon.FOG),
    ],
)
def test_level_and_phenomenon_of_a_recorded_document(file: str, level: SeverityLevel | None, original: str, phenomenon: VigilancePhenomenon | None) -> None:
    vigilance = warnings_of(file)[0]
    assert (vigilance.level, vigilance.level_original, vigilance.phenomenon) == (level, original, phenomenon)
    assert vigilance.source == "swic" and vigilance.country == "XX"
    # ``derived`` lists the computed fields that have a value, no more.
    expected = [name for name, value in (("status", vigilance.status), ("level", level), ("phenomenon", phenomenon)) if value is not None]
    assert vigilance.derived == expected


@pytest.mark.parametrize(
    ("severity", "level"),
    [("Extreme", SeverityLevel.RED), ("Severe", SeverityLevel.ORANGE), ("Moderate", SeverityLevel.YELLOW), ("Minor", None), ("Unknown", None), (None, None), ("", None)],
)
def test_the_severity_table(severity: str | None, level: SeverityLevel | None) -> None:
    assert cap.derive_level(severity, [])[0] is level


@pytest.mark.parametrize(
    ("events", "phenomenon"),
    [
        (["Heavy rain"], VigilancePhenomenon.RAIN),
        (["Thunderstorm with heavy rain"], VigilancePhenomenon.THUNDERSTORM),  # the first rule wins, as in the NWS table
        (["THUNDER STORM AND DUST"], VigilancePhenomenon.THUNDERSTORM),
        (["Aviso de vientos de nivel amarillo"], VigilancePhenomenon.WIND),
        (["Inundación"], VigilancePhenomenon.FLOOD),
        (["Tormenta de arena"], VigilancePhenomenon.OTHER),
        (["Storm surge", "Marejada"], VigilancePhenomenon.COASTAL),
        (["Ola de calor"], VigilancePhenomenon.HEAT),
        (["Chuva forte", "Heavy rain"], VigilancePhenomenon.RAIN),
        (["Trovoada"], VigilancePhenomenon.THUNDERSTORM),
        (["Avalanche warning"], VigilancePhenomenon.AVALANCHE),
        (["Tropical Cyclone Warning"], VigilancePhenomenon.WIND),
        (["Dense fog"], VigilancePhenomenon.FOG),
        # Nothing matches, or two languages disagree: null.
        (["Adverse Weather"], None),
        (["Маловодье"], None),
        ([None, ""], None),
        (["Heavy rain", "Strong wind"], None),
        ([], None),
    ],
)
def test_the_phenomenon_table(events: list[str | None], phenomenon: VigilancePhenomenon | None) -> None:
    assert cap.derive_phenomenon(events) is phenomenon


def test_a_keyword_matches_the_start_of_a_word_only() -> None:
    """"ice" is in "service" and "rain" in "terrain": neither makes snow or rain."""
    assert cap.derive_phenomenon(["Service notice"]) is None
    assert cap.derive_phenomenon(["Terrain closed"]) is None


def test_two_different_colours_in_one_parameter_give_no_level() -> None:
    from app.models.vigilance import VigilanceParameter

    both = [VigilanceParameter(name="awareness_level", value="yellow and orange")]
    assert cap.colour_of(both) is None
    assert cap.derive_level("Severe", both) == (SeverityLevel.ORANGE, "Severe")  # falls back on the CAP severity


# ---------------------------------------------------------------------------------------------------- what the issuer wrote stays


def test_the_texts_of_every_language_stay_as_written() -> None:
    vigilance = warnings_of("cap_dz_onm_rain.xml")[0]
    assert [text.language for text in vigilance.texts] == ["fr-FR", "en-US"]
    raw = samples.fixture("cap_dz_onm_rain.xml").decode("utf-8")
    for text in vigilance.texts:
        for value in (text.event, text.headline, text.description, text.instruction):
            if value:
                assert value in raw  # not translated, not shortened, not rewritten
    assert vigilance.phenomenon_original == "PLUIE" and vigilance.event == "PLUIE"
    assert vigilance.issuer == "Office National de la Météorologie - Algérie"


def test_arabic_and_english_are_both_kept() -> None:
    vigilance = warnings_of("cap_kw_dom_arabic_english.xml")[0]
    languages = {text.language for text in vigilance.texts}
    assert languages == {"en-US", "ar-KW"}
    arabic = next(text for text in vigilance.texts if text.language == "ar-KW")
    assert any(ord(ch) > 0x600 for ch in (arabic.headline or arabic.description or ""))


def test_the_original_identifier_the_codes_and_the_link_are_kept() -> None:
    vigilance = warnings_of("cap_es_aemet_alert.xml")[0]
    assert vigilance.identifier == "2.49.0.0.724.0.ES.20261020092533.712701VIRM22221792488333"
    assert [(g.scheme, g.value) for g in vigilance.area.geocodes] == [("AEMET-Meteoalerta zona", "712701")]
    assert vigilance.area.description == "A Mariña"
    assert vigilance.link == samples.document_url("cap_es_aemet_alert.xml")
    assert vigilance.id == f"swic:xx-test-en:{vigilance.identifier}:0.0"  # stable: the same document gives the same id


def test_the_attribution_names_the_issuer_and_the_registry() -> None:
    vigilance = warnings_of("cap_es_aemet_alert.xml")[0]
    assert vigilance.attribution == "Source: test | Issued by AEMET. Agencia Estatal de Meteorología"


def test_a_cancel_with_no_info_has_no_warning_and_names_what_it_replaces() -> None:
    alert = cap.parse_alert(samples.fixture("cap_by_cancel_1.xml"))
    assert alert.message_type == "Cancel" and alert.infos == ()
    assert alert.references == (("sin@hmc.by", "2.49.0.0.112.0-20261019-094701-0024440-00-EN"),)
    assert not cap.is_warning(alert)
    assert cap.superseded([alert]) == set(alert.references)


# ---------------------------------------------------------------------------------------------------- references, status, scope


def test_an_update_replaces_what_it_references_and_a_test_does_not_speak() -> None:
    update = cap.parse_alert(samples.fixture("cap_es_aemet_update_1.xml"))
    assert cap.is_warning(update) and update.message_type == "Update"
    assert len(update.references) == 1
    # The references of an alert with the status Test replace nothing (an exercise cancels no real warning).
    test_update = cap.parse_alert(samples.cap("t-1", status="Test", msg_type="Update", references="a@x.org,old-1,2026-10-20T10:00:00+00:00"))
    assert cap.superseded([test_update]) == set()
    assert not cap.is_warning(test_update)


def test_one_update_can_replace_several_alerts() -> None:
    alert = cap.parse_alert(samples.fixture("cap_no_met_update.xml"))
    assert len(alert.references) == 2
    assert all(identifier.startswith("2.49.0.1.578.0.") for _, identifier in alert.references)


def test_references_with_a_comma_in_the_identifier_and_junk() -> None:
    assert cap.parse_references("s@x,id,with,comma,2026-10-20T10:00:00+00:00 broken s2@x,id2,2026") == (("s@x", "id,with,comma"), ("s2@x", "id2"))
    assert cap.parse_references(None) == () and cap.parse_references("") == ()


@pytest.mark.parametrize(
    ("kwargs", "is_warning"),
    [
        ({}, True),
        ({"status": "Test"}, False),
        ({"status": "Exercise"}, False),
        ({"status": "Draft"}, False),
        ({"scope": "Restricted"}, False),
        ({"msg_type": "Cancel"}, False),
        ({"msg_type": "Ack"}, False),
        ({"msg_type": "Update"}, True),
    ],
)
def test_which_documents_are_warnings(kwargs: dict[str, str], is_warning: bool) -> None:
    """Synthetic documents."""
    assert cap.is_warning(cap.parse_alert(samples.cap("s-1", infos=[samples.info()], **kwargs))) is is_warning


# ---------------------------------------------------------------------------------------------------- in force


def _group(alert: cap.CapAlert) -> list[cap.CapInfo]:
    return cap.group_translations(alert.infos)[0]


@pytest.mark.parametrize(
    ("info_kwargs", "now", "in_force"),
    [
        ({}, "2026-10-20T20:00:00+00:00", True),
        ({}, "2026-10-21T16:00:01+00:00", False),  # expired
        ({"urgency": "Past"}, "2026-10-20T20:00:00+00:00", False),  # Macao's all-clear
        ({"certainty": "Unlikely"}, "2026-10-20T20:00:00+00:00", False),
        ({"expires": ""}, "2026-10-20T20:00:00+00:00", True),  # no expiry, sent 4 hours ago
        ({"expires": ""}, "2026-10-26T20:00:00+00:00", False),  # no expiry, sent six days ago: nothing says it is still in force
    ],
)
def test_when_a_warning_is_in_force(info_kwargs: dict[str, str], now: str, in_force: bool) -> None:
    """Synthetic documents."""
    document = samples.cap("f-1", infos=[samples.info(**info_kwargs)])
    alert = cap.parse_alert(document)
    assert cap.is_in_force(alert, _group(alert), cap.instant(now)) is in_force


# ---------------------------------------------------------------------------------------------------- languages and warnings in one document


def test_two_languages_of_one_warning_are_one_warning_and_two_different_warnings_are_two() -> None:
    one = warnings_of("cap_dz_onm_rain.xml")
    assert len(one) == 1 and len(one[0].texts) == 2
    two = warnings_of("cap_uy_inumet_two_infos.xml")
    assert len(two) == 2 and {w.level for w in two} == {SeverityLevel.ORANGE, SeverityLevel.YELLOW}
    assert two[0].id != two[1].id  # the variant is in the id


def test_the_same_alert_in_two_language_feeds_is_found_by_its_shape_not_by_the_name_of_its_codes() -> None:
    """The codes of Norway's NVE are named in the language of each info (KommuneId / MunicipalityId): only their values count."""
    infos = [
        samples.info(language="no", areas=["<area><areaDesc>A</areaDesc><geocode><valueName>KommuneId</valueName><value>1</value></geocode></area>"]),
        samples.info(language="en", areas=["<area><areaDesc>A</areaDesc><geocode><valueName>MunicipalityId</valueName><value>1</value></geocode></area>"]),
    ]
    alert = cap.parse_alert(samples.cap("n-1", infos=infos))
    assert len(cap.group_translations(alert.infos)) == 1


def test_the_same_language_twice_is_two_warnings_when_they_differ() -> None:
    infos = [samples.info(severity="Moderate"), samples.info(severity="Severe")]
    alert = cap.parse_alert(samples.cap("d-1", infos=infos))
    assert len(cap.group_translations(alert.infos)) == 2


# ---------------------------------------------------------------------------------------------------- the area


def test_a_polygon_is_a_shape_that_contains_its_points() -> None:
    vigilance = warnings_of("cap_es_aemet_alert.xml")[0]
    assert vigilance.area.has_polygon and vigilance.shape is not None
    centre = vigilance.shape.representative_point()
    assert vigilance.shape.contains(Point(centre.x, centre.y))


def test_sixty_seven_polygons_of_one_area_are_one_shape() -> None:
    vigilance = warnings_of("cap_th_tmd_67_polygons.xml")[0]
    assert vigilance.area.has_polygon and vigilance.shape is not None and vigilance.shape.geom_type in ("Polygon", "MultiPolygon")


def test_codes_only_is_not_placed_on_the_map() -> None:
    for file in ("cap_ph_pagasa_1.xml", "cap_in_ndma_geocodes.xml"):
        vigilance = warnings_of(file)[0]
        assert vigilance.shape is None and not vigilance.area.has_polygon, file
        assert vigilance.area.geocodes  # but the codes are given, for the site that wants to place them


def test_a_circle_is_a_polygon_around_its_centre() -> None:
    """Synthetic: no recorded document had a circle."""
    area = "<area><areaDesc>Round</areaDesc><circle>10.0,20.0 50</circle></area>"
    vigilance = _only(samples.cap("c-1", infos=[samples.info(areas=[area])]))
    assert vigilance.area.has_polygon and vigilance.shape is not None
    assert vigilance.shape.contains(Point(20.0, 10.0))  # lon, lat
    assert not vigilance.shape.contains(Point(20.0 + 2.0, 10.0))  # 2 degrees of longitude is about 220 km: outside 50 km
    assert vigilance.shape.contains(Point(20.0 + 0.4, 10.0))  # 0.4 degree is about 44 km: inside


def test_a_circle_across_the_antimeridian_is_cut_in_two() -> None:
    """Synthetic: a circle of 200 km centred 0.5 degree west of the 180th meridian."""
    area = "<area><areaDesc>Fiji</areaDesc><circle>-17.0,179.5 200</circle></area>"
    vigilance = _only(samples.cap("c-2", infos=[samples.info(areas=[area])]))
    assert vigilance.shape is not None
    assert vigilance.shape.contains(Point(179.9, -17.0)) and vigilance.shape.contains(Point(-179.9, -17.0))
    assert not vigilance.shape.contains(Point(0.0, -17.0))


def test_a_polygon_with_junk_in_it_is_no_polygon() -> None:
    """Synthetic: an area whose polygon is not coordinates is still a warning, with no shape."""
    area = "<area><areaDesc>Nowhere</areaDesc><polygon>not a polygon</polygon></area>"
    vigilance = _only(samples.cap("p-1", infos=[samples.info(areas=[area])]))
    assert vigilance.shape is None and not vigilance.area.has_polygon


def test_an_info_with_no_area_is_one_warning_with_no_place() -> None:
    vigilance = _only(samples.cap("a-1", infos=[samples.info(areas=[])]))
    assert vigilance.area.description is None and vigilance.shape is None


def test_more_areas_than_the_limit_are_counted_not_read() -> None:
    areas = [f"<area><areaDesc>Z{n}</areaDesc></area>" for n in range(cap.MAX_AREAS_PER_INFO + 5)]
    alert = cap.parse_alert(samples.cap("m-1", infos=[samples.info(areas=areas)]))
    assert len(alert.infos[0].areas) == cap.MAX_AREAS_PER_INFO and alert.infos[0].skipped_areas == 5


def _only(document: bytes):
    alert = cap.parse_alert(document)
    out = []
    for variant, group in enumerate(cap.group_translations(alert.infos)):
        out.extend(cap.to_vigilances(alert, group, variant, source_id="x", country="XX", credit="c"))
    assert len(out) == 1
    return out[0]


# ---------------------------------------------------------------------------------------------------- dates


@pytest.mark.parametrize(
    ("text", "epoch"),
    [
        ("2026-10-20T17:00:00+00:00", 1_792_515_600),
        ("2026-10-20T17:00:00Z", 1_792_515_600),
        ("2026-10-20T17:00:00-00:00", 1_792_515_600),  # Roshydromet's UTC
        ("2026-10-20T19:00:00+02:00", 1_792_515_600),
        ("2026-10-20T10:00:00-07:00", 1_792_515_600),
        ("2569-10-20T00:00:00+07:00", cap.instant("2026-10-20T00:00:00+07:00")),  # the Buddhist era: Gregorian + 543
        ("not a date", None),
        ("", None),
        (None, None),
    ],
)
def test_dates(text: str | None, epoch: int | None) -> None:
    assert cap.instant(text) == epoch


# ---------------------------------------------------------------------------------------------------- hostile and broken documents


@pytest.mark.parametrize(
    "content",
    [
        b'<?xml version="1.0"?><!DOCTYPE alert [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]><alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">&b;</alert>',
        b'<?xml version="1.0"?><!DOCTYPE foo SYSTEM "http://localhost/evil.dtd"><alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"/>',
        b"<alert xmlns='urn:oasis:names:tc:emergency:cap:1.2'><!ENTITY x 'y'></alert>",
    ],
)
def test_a_dtd_or_an_entity_is_refused(content: bytes) -> None:
    """Synthetic. The billion-laughs shape and an external DTD: refused before any parser sees them."""
    with pytest.raises(cap.CapError):
        cap.parse_alert(content)


@pytest.mark.parametrize(
    "content",
    [b"", b"<html><body>404</body></html>", b"<rss version='2.0'><channel/></rss>", b"<alert><identifier>x</identifier></alert>", b"<alert xmlns='urn:oasis:names:tc:emergency:cap:1.2'/>", b"not xml at all"],
)
def test_what_is_not_a_cap_alert_is_an_error_not_a_warning(content: bytes) -> None:
    with pytest.raises(cap.CapError):
        cap.parse_alert(content)


def test_a_feed_is_not_a_cap_alert() -> None:
    """Guyana's feed lists its own address as the link of its entry: the document read there is the feed."""
    with pytest.raises(cap.CapError):
        cap.parse_alert(samples.fixture("index_jm_no_active.xml"))


def test_the_cap_version_is_not_pinned_to_1_2() -> None:
    """The WMO's copy of the Chinese feed declares CAP 1.1; the namespace is read by its prefix."""
    assert cap.parse_alert(samples.cap("v-1", infos=[samples.info()]).replace(b"cap:1.2", b"cap:1.1")).identifier == "v-1"


def test_the_status_of_a_message_is_derived_from_its_type() -> None:
    assert warnings_of("cap_es_aemet_update_1.xml")[0].status is VigilanceStatus.UPDATE
    assert warnings_of("cap_es_aemet_alert.xml")[0].status is VigilanceStatus.NEW
