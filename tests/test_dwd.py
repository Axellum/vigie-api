"""The DWD collector: the recorded status of 2026-10-20, the normalization tables, the shapes, and the conditional requests.

The recorded answers are real (``tests/fixtures/dwd/``). The documents built here are **synthetic** (written after the
CAP DWD profile 2.1.14, with the structure of the recorded ones) and exist only to reach what the recording of that day
does not hold: an orange or red level, a hole in an area, an all-clear, a document that is not a warning.
"""

from __future__ import annotations

import io
import zipfile
from typing import Any, Optional

import httpx
import pytest
from shapely.geometry import Point

from app.attribution import credit_text
from app.collectors import dwd
from app.collectors.base import CollectorUnavailableError, Fetched
from app.collectors.dwd import DWDCollector, derive_level, derive_phenomenon, parse_status
from app.models.schemas import SeverityLevel
from app.models.vigilance import VigilancePhenomenon, VigilanceStatus
from tests import vigilance_samples as samples
from tests.helpers import Provider, SimClock

RECORDED_ETAG = '"6ad75578-4005"'
RECORDED_LAST_MODIFIED = "Tue, 20 Oct 2026 11:50:16 GMT"


# ---------------------------------------------------------------- synthetic documents


def cap_document(
    *,
    code: str = "synthetic-1",
    severity: str = "Moderate",
    ii: str = "31",
    status: str = "Actual",
    msg_type: str = "Alert",
    response_type: str = "Prepare",
    areas: Optional[list[dict[str, Any]]] = None,
    event: str = "GEWITTER",
    languages: tuple[str, ...] = ("de-DE", "en"),
) -> bytes:
    """A CAP document in the shape of the DWD's (profile 2.1.14): one ``info`` per language, the areas in each."""
    areas = areas or [{"name": "Testkreis", "cell": "811000000", "polygons": [(50.0, 8.0, 51.0, 9.0)]}]

    def area_xml(area: dict[str, Any]) -> str:
        polygons = "".join(
            f"<polygon>{s},{w} {s},{e} {n},{e} {n},{w} {s},{w}</polygon>" for s, w, n, e in area["polygons"]
        )
        holes = "".join(
            "<geocode><valueName>EXCLUDE_POLYGON</valueName>"
            f"<value>{s},{w} {s},{e} {n},{e} {n},{w} {s},{w}</value></geocode>"
            for s, w, n, e in area.get("holes", [])
        )
        return (
            f"<area><areaDesc>{area['name']}</areaDesc>{polygons}"
            f"<geocode><valueName>WARNCELLID</valueName><value>{area['cell']}</value></geocode>{holes}</area>"
        )

    infos = ""
    for language in languages:
        infos += f"""<info><language>{language}</language><category>Met</category><event>{event} ({language})</event>
<responseType>{response_type}</responseType><urgency>Immediate</urgency><severity>{severity}</severity><certainty>Likely</certainty>
<eventCode><valueName>LICENSE</valueName><value>© GeoBasis-DE / BKG 2021 (Daten modifiziert)</value></eventCode>
<eventCode><valueName>II</valueName><value>{ii}</value></eventCode>
<effective>2026-10-20T13:19:00+02:00</effective><onset>2026-10-20T18:00:00+02:00</onset><expires>2026-10-21T00:00:00+02:00</expires>
<senderName>Deutscher Wetterdienst</senderName><headline>Headline ({language})</headline><description>Description ({language})</description>
<web>https://dwd.de/warnungen</web>{"".join(area_xml(a) for a in areas)}</info>"""
    return (
        '<?xml version="1.0" encoding="UTF-8"?><alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">'
        f"<identifier>2.49.0.0.276.0.DWD.PVW.{code}.MUL</identifier><sender>opendata@dwd.de</sender>"
        f"<sent>2026-10-20T13:19:00+02:00</sent><status>{status}</status><msgType>{msg_type}</msgType>"
        f"<source>PVW</source><scope>Public</scope><code>id:2.49.0.0.276.0.DWD.PVW.{code}</code>{infos}</alert>"
    ).encode("utf-8")


def archive(*documents: bytes, names: Optional[list[str]] = None) -> bytes:
    """A ZIP with one member per document."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as handle:
        for index, document in enumerate(documents):
            handle.writestr(names[index] if names else f"doc-{index}.xml", document)
    return buffer.getvalue()


# ---------------------------------------------------------------- the recorded status


def test_the_recorded_status_is_four_documents_and_seven_areas() -> None:
    status = samples.dwd_status()
    assert len(status.vigilances) == 7 and status.skipped == 0
    assert len({v.identifier for v in status.vigilances}) == 2 + 0 + 2  # four documents, one identifier each
    assert {v.area.description for v in status.vigilances} == {
        "Ostfriesische Küste",
        "Helgoland",
        "Insel Borkum",
        "Kreis Friesland - Küste",
        "Kreis Aurich - Küste",
        "Kreis Wittmund - Küste",
        "Insel Helgoland",
    }
    assert status.updated_utc == 1_792_497_016  # the Last-Modified of the answer


def test_a_recorded_warning_keeps_the_words_of_the_issuer_in_every_language() -> None:
    borkum = next(v for v in samples.dwd_status().vigilances if v.area.description == "Insel Borkum")
    assert [t.language for t in borkum.texts] == ["de-DE", "en", "fr", "es", "ar", "ru", "tr", "pl"]
    german, english, french = borkum.texts[0], borkum.texts[1], borkum.texts[2]
    assert (german.event, german.headline) == ("WINDBÖEN", "Amtliche WARNUNG vor WINDBÖEN")
    assert german.description is not None and german.description.startswith("Es treten Windböen mit Geschwindigkeiten bis 55 km/h")
    assert (english.event, french.event) == ("wind gusts", "Rafales de vent")
    assert german.sender_name == "Deutscher Wetterdienst" and german.web == "https://dwd.de/warnungen"
    assert {"name": "eventCode:II", "value": "51"} in [p.model_dump() for p in german.parameters]
    assert {"name": "gusts", "value": "<55 [km/h]"} in [p.model_dump() for p in german.parameters]


def test_a_recorded_warning_is_normalized_with_its_original_words_beside() -> None:
    borkum = next(v for v in samples.dwd_status().vigilances if v.area.description == "Insel Borkum")
    assert (borkum.level, borkum.level_original) == (SeverityLevel.YELLOW, "Minor")
    assert (borkum.phenomenon, borkum.phenomenon_original) == (VigilancePhenomenon.WIND, "WINDBÖEN")
    assert (borkum.status, borkum.message_type) == (VigilanceStatus.NEW, "Alert")
    assert (borkum.country, borkum.source, borkum.issuer) == ("DE", "dwd", "Deutscher Wetterdienst")
    # 2026-10-20 18:00 and 2026-10-21 00:00 in the summer time of Germany (UTC+2), as UTC epoch seconds.
    assert (borkum.sent_utc, borkum.onset_utc, borkum.expires_utc) == (1_792_495_140, 1_792_512_000, 1_792_533_600)
    assert borkum.ends_utc is None  # the DWD gives no end: it is not invented
    assert borkum.id == "dwd:2.49.0.0.276.0.DWD.PVW.1792495140000.00c1c319-2bdd-4cd2-a323-bc2892b1ba5b:903457002"
    assert [(g.scheme, g.value) for g in borkum.area.geocodes] == [("WARNCELLID", "903457002")]
    assert borkum.area.has_polygon is True and borkum.shape is not None
    # The derived fields are said to be derived; the issuer's own are not.
    assert {"level", "phenomenon", "status"} <= set(borkum.derived)


def test_a_recorded_warning_carries_the_credit_of_the_dwd_and_of_the_area_shapes() -> None:
    borkum = next(v for v in samples.dwd_status().vigilances if v.area.description == "Insel Borkum")
    assert borkum.attribution == (
        f"{credit_text('dwd_warnings')} | © GeoBasis-DE / BKG 2021 (Daten modifiziert) | Issued by Deutscher Wetterdienst"
    )
    assert credit_text("dwd_warnings") == "Source: Deutscher Wetterdienst"


@pytest.mark.parametrize(
    ("place", "point", "areas"),
    [
        ("Borkum town", samples.BORKUM, {"Insel Borkum"}),
        ("Norden", samples.NORDEN, {"Kreis Aurich - Küste"}),
        ("Wittmund", (53.5736, 7.7794), {"Kreis Wittmund - Küste"}),
        ("Wangerooge", (53.789, 7.9), {"Kreis Friesland - Küste"}),
        ("Helgoland island", (54.18, 7.89), {"Insel Helgoland"}),
        ("Berlin", samples.BERLIN, set()),
        ("Hamburg", (53.55, 9.99), set()),
    ],
)
def test_the_polygons_of_the_recording_tell_where_a_point_is(place: str, point: tuple[float, float], areas: set[str]) -> None:
    lat, lon = point
    inside = {v.area.description for v in samples.dwd_status().vigilances if v.shape is not None and v.shape.covers(Point(lon, lat))}
    assert inside == areas, place


def test_the_recorded_empty_difference_product_is_a_valid_answer() -> None:
    status = parse_status(samples.national_fixture("dwd", "difference_district_mul.zip"))
    assert (status.vigilances, status.skipped) == ((), 0)


# ---------------------------------------------------------------- the tables


@pytest.mark.parametrize(
    ("severity", "level"),
    [
        ("Minor", SeverityLevel.YELLOW),
        ("minor", SeverityLevel.YELLOW),
        ("Moderate", SeverityLevel.ORANGE),
        ("Severe", SeverityLevel.RED),
        ("Extreme", SeverityLevel.RED),  # violet in the DWD's scale, red at the top of ours; the word stays in level_original
        ("Unknown", None),
        ("", None),
        (None, None),
    ],
)
def test_the_severity_table(severity: Optional[str], level: Optional[SeverityLevel]) -> None:
    assert derive_level(severity) == level


@pytest.mark.parametrize(
    ("ii", "phenomenon"),
    [
        ("11", VigilancePhenomenon.WIND),
        ("51", VigilancePhenomenon.WIND),
        ("58", VigilancePhenomenon.WIND),
        ("31", VigilancePhenomenon.THUNDERSTORM),
        ("96", VigilancePhenomenon.THUNDERSTORM),
        ("61", VigilancePhenomenon.RAIN),
        ("71", VigilancePhenomenon.SNOW_ICE),
        ("85", VigilancePhenomenon.SNOW_ICE),
        ("22", VigilancePhenomenon.COLD),
        ("247", VigilancePhenomenon.HEAT),
        ("59", VigilancePhenomenon.FOG),
        ("88", VigilancePhenomenon.OTHER),
        ("999", None),  # not in the table: nothing is guessed
        ("WIND", None),
        ("", None),
        (None, None),
    ],
)
def test_the_event_number_table(ii: Optional[str], phenomenon: Optional[VigilancePhenomenon]) -> None:
    assert derive_phenomenon(ii) == phenomenon


def test_an_event_number_belongs_to_one_phenomenon_only() -> None:
    listed = [number for numbers in dwd._PHENOMENA_BY_II.values() for number in numbers]
    assert len(listed) == len(set(listed)) == len(dwd.PHENOMENA)


# ---------------------------------------------------------------- synthetic documents


def test_an_orange_thunderstorm_over_two_areas_is_two_warnings() -> None:
    document = cap_document(
        areas=[
            {"name": "Kreis A", "cell": "811000001", "polygons": [(50.0, 8.0, 51.0, 9.0)]},
            {"name": "Kreis B", "cell": "811000002", "polygons": [(52.0, 8.0, 53.0, 9.0)]},
        ]
    )
    warnings = parse_status(archive(document)).vigilances
    assert [(w.area.description, w.level, w.phenomenon) for w in warnings] == [
        ("Kreis A", SeverityLevel.ORANGE, VigilancePhenomenon.THUNDERSTORM),
        ("Kreis B", SeverityLevel.ORANGE, VigilancePhenomenon.THUNDERSTORM),
    ]
    assert warnings[0].id.endswith(":811000001") and warnings[1].id.endswith(":811000002")
    assert [t.language for t in warnings[0].texts] == ["de-DE", "en"]


def test_an_area_in_several_pieces_covers_each_of_them() -> None:
    document = cap_document(areas=[{"name": "Zwei Inseln", "cell": "811000003", "polygons": [(50.0, 8.0, 51.0, 9.0), (54.0, 8.0, 55.0, 9.0)]}])
    [warning] = parse_status(archive(document)).vigilances
    assert warning.shape is not None
    assert warning.shape.covers(Point(8.5, 50.5)) and warning.shape.covers(Point(8.5, 54.5))
    assert not warning.shape.covers(Point(8.5, 52.5))


def test_a_hole_of_an_area_is_cut_out() -> None:
    """The profile (2.2.3.3) cuts every EXCLUDE_POLYGON out of the area: a point in the hole is not warned."""
    document = cap_document(
        areas=[{"name": "Kreis mit Loch", "cell": "811000004", "polygons": [(50.0, 8.0, 51.0, 9.0)], "holes": [(50.4, 8.4, 50.6, 8.6)]}]
    )
    [warning] = parse_status(archive(document)).vigilances
    assert warning.shape is not None
    assert warning.shape.covers(Point(8.1, 50.1))
    assert not warning.shape.covers(Point(8.5, 50.5))  # in the hole
    assert [(g.scheme, g.value) for g in warning.area.geocodes] == [("WARNCELLID", "811000004")]  # the hole is no geocode


def test_an_area_without_polygon_says_so() -> None:
    document = cap_document(areas=[{"name": "Ohne Form", "cell": "811000005", "polygons": []}])
    [warning] = parse_status(archive(document)).vigilances
    assert warning.area.has_polygon is False and warning.shape is None


def test_extreme_is_red_and_keeps_its_word() -> None:
    [warning] = parse_status(archive(cap_document(severity="Extreme", ii="51"))).vigilances
    assert (warning.level, warning.level_original) == (SeverityLevel.RED, "Extreme")


def test_an_unknown_event_number_gives_no_phenomenon_but_keeps_the_word() -> None:
    [warning] = parse_status(archive(cap_document(ii="999", event="NEUES"))).vigilances
    assert warning.phenomenon is None and warning.phenomenon_original == "NEUES (de-DE)"


@pytest.mark.parametrize(
    "document",
    [
        cap_document(status="Test"),
        cap_document(status="Exercise"),
        cap_document(msg_type="Update", response_type="AllClear"),
    ],
    ids=["test", "exercise", "all-clear"],
)
def test_a_test_or_an_all_clear_is_not_a_warning(document: bytes) -> None:
    status = parse_status(archive(document))
    assert status.vigilances == () and status.skipped == 1


def test_a_cancellation_keeps_its_status() -> None:
    [warning] = parse_status(archive(cap_document(msg_type="Cancel"))).vigilances
    assert warning.status == VigilanceStatus.CANCEL


def test_a_member_that_is_not_xml_is_set_aside_and_the_others_are_read() -> None:
    content = archive(cap_document(), b"not xml at all", b"hello", names=["a.xml", "b.xml", "readme.txt"])
    status = parse_status(content)
    assert len(status.vigilances) == 1 and status.skipped == 2


def test_a_document_with_a_dtd_is_refused() -> None:
    evil = b'<?xml version="1.0"?><!DOCTYPE alert [<!ENTITY a "aaaa">]><alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"/>'
    with pytest.raises(ValueError, match="none could be read"):
        parse_status(archive(evil))
    assert len(parse_status(archive(evil, cap_document())).vigilances) == 1  # one bad document does not hide the others


def test_what_is_not_a_zip_is_an_error_and_an_empty_zip_is_not() -> None:
    with pytest.raises(ValueError, match="not a ZIP"):
        parse_status(b"<html>maintenance</html>")
    assert parse_status(archive()).vigilances == ()


def test_an_archive_beyond_the_limits_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    content = archive(cap_document(), cap_document(code="b"))
    monkeypatch.setattr(dwd, "MAX_MEMBERS", 1)
    with pytest.raises(ValueError, match="larger than any status"):
        parse_status(content)
    monkeypatch.setattr(dwd, "MAX_MEMBERS", 5_000)
    monkeypatch.setattr(dwd, "MAX_MEMBER_BYTES", 100)  # every member is then too large: set aside, not read
    assert parse_status(content).vigilances == ()


# ---------------------------------------------------------------- the requests


def recorded_provider(seen: Optional[list[httpx.Request]] = None) -> Provider:
    """The DWD server as it was recorded: a ZIP with an ETag and a Last-Modified, a 304 for the request that repeats them."""
    body = samples.national_fixture("dwd", "status_district_mul.zip")

    def answer(request: httpx.Request) -> httpx.Response:
        if request.headers.get("If-None-Match") == RECORDED_ETAG:
            return httpx.Response(304)
        return httpx.Response(200, content=body, headers={"ETag": RECORDED_ETAG, "Last-Modified": RECORDED_LAST_MODIFIED})

    return Provider(answer=answer, latency_ticks=0)


@pytest.mark.asyncio
async def test_the_status_is_read_from_the_cache_for_two_minutes_then_asked_again_conditionally(sim_clock: SimClock) -> None:
    provider = recorded_provider()
    collector = DWDCollector(http_client=provider.client())

    first = await collector.fetch_status()
    assert provider.count == 1 and len(first.value.vigilances) == 7
    assert provider.requests[0].url.path.endswith("Z_CAP_C_EDZW_LATEST_PVW_STATUS_PREMIUMDWD_DISTRICT_MUL.zip")
    assert "If-None-Match" not in provider.requests[0].headers  # nothing to repeat yet
    assert first.value.updated_utc == 1_792_497_016  # the Last-Modified of the answer

    sim_clock.advance(119)
    await collector.fetch_status()
    assert provider.count == 1  # fresh: no request

    sim_clock.advance(2)
    second = await collector.fetch_status()
    assert provider.count == 2
    assert provider.requests[1].headers["If-None-Match"] == RECORDED_ETAG
    assert provider.requests[1].headers["If-Modified-Since"] == RECORDED_LAST_MODIFIED
    assert second.value is first.value and second.stale is False  # a 304 keeps what was read: nothing parsed again

    sim_clock.advance(121)  # and it goes on: the validators are kept through the 304
    await collector.fetch_status()
    assert provider.requests[2].headers["If-None-Match"] == RECORDED_ETAG


@pytest.mark.asyncio
async def test_a_new_production_replaces_the_status(sim_clock: SimClock) -> None:
    quiet = archive()
    served = {"body": samples.national_fixture("dwd", "status_district_mul.zip"), "etag": RECORDED_ETAG}

    def answer(request: httpx.Request) -> httpx.Response:
        if request.headers.get("If-None-Match") == served["etag"]:
            return httpx.Response(304)
        return httpx.Response(200, content=served["body"], headers={"ETag": served["etag"]})

    provider = Provider(answer=answer, latency_ticks=0)
    collector = DWDCollector(http_client=provider.client())
    assert len((await collector.fetch_status()).value.vigilances) == 7
    served.update(body=quiet, etag='"next"')
    sim_clock.advance(121)
    assert (await collector.fetch_status()).value.vigilances == ()  # the warnings ended: the new status no longer lists them


@pytest.mark.asyncio
async def test_a_failing_server_falls_back_on_the_last_status_and_says_so(sim_clock: SimClock) -> None:
    state = {"down": False}
    ok = recorded_provider()

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503) if state["down"] else ok.answer(request)  # type: ignore[misc]

    provider = Provider(answer=answer, latency_ticks=0)
    collector = DWDCollector(http_client=provider.client())
    await collector.fetch_status()
    state["down"] = True
    sim_clock.advance(121)
    fetched: Fetched[Any] = await collector.fetch_status()
    assert fetched.stale is True and len(fetched.value.vigilances) == 7


@pytest.mark.asyncio
async def test_a_304_to_a_request_that_was_not_conditional_is_an_error(sim_clock: SimClock) -> None:
    provider = Provider(answer=lambda request: httpx.Response(304), latency_ticks=0)
    collector = DWDCollector(http_client=provider.client())
    with pytest.raises(CollectorUnavailableError):
        await collector.fetch_status(allow_stale_on_error=False)


def test_the_collector_is_a_warnings_provider_with_the_published_cadence() -> None:
    collector = DWDCollector()
    assert collector.provider_id == "dwd_cap" and collector.domain == "warnings" and collector.ttl_seconds == 120
