"""Offline tests for the Météo-France avalanche-risk bulletins (BRA, ``DPBRA/v1``) as the third avalanche source."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.collectors.avalanches import resolve_avalanche_coverage
from app.collectors.base import Fetched
from app.collectors.meteofrance_bra import (
    BRA_URL,
    SAT_PROBLEMS,
    BRAMassifBulletin,
    MeteoFranceBRACollector,
    clean_text,
    get_bra_index,
    massif_code,
    meteofrance_bra_key_from_env,
    paris_to_utc,
    parse_bra_xml,
)
from app.models.avalanches import AvalancheRegion
from app.models.schemas import SeverityLevel
from tests.conftest import World
from tests.helpers import SimClock, make_service

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "avalanches"
MONT_BLANC_XML = (FIXTURES / "meteofrance_bra_mont_blanc.xml").read_bytes()
HAUTE_BIGORRE_XML = (FIXTURES / "meteofrance_bra_haute_bigorre.xml").read_bytes()
SAMPLE_KEY = "mf_bra_secret_token_1234567890abcdef=="

# 2026-11-16 18:00 UTC: the day of both recordings, after the bulletins were written (16:00 Paris) and before they end (17 Nov 18:00 Paris).
NOW = datetime(2026, 11, 16, 18, 0, tzinfo=timezone.utc)
AFTER_END = datetime(2026, 11, 17, 17, 30, tzinfo=timezone.utc)

CHAMONIX = (45.9237, 6.8694)  # massif 3, Mont-Blanc
GAVARNIE = (42.7336, -0.0107)  # massif 66, Haute-Bigorre
BAREGES = (42.8997, 0.0722)  # massif 66, Haute-Bigorre
PARIS = (48.8566, 2.3522)
DAVOS = (46.8043, 9.8374)  # SLF wins over any other source
INNSBRUCK = (47.2692, 11.4041)  # Euregio wins over BRA


def _set_now(sim_clock: SimClock, when: datetime) -> None:
    sim_clock.now = when.timestamp() - SimClock.EPOCH


# --------------------------------------------------------------------------------------------------------------------- parser


def test_mont_blanc_bulletin_is_normalised() -> None:
    b = parse_bra_xml(MONT_BLANC_XML, NOW)
    assert b is not None
    assert b.bulletin_id == "FR-BRA-3-2026-11-16T16:00:00"
    assert b.source == "meteofrance_bra" and b.lang == "fr"
    # The XML dates are Paris time (UTC+1 in November): 16:00 -> 15:00Z, 17 Nov 18:00 -> 17:00Z, diffusion 16:07 -> 15:07Z.
    assert b.valid_from == datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    assert b.valid_until == datetime(2026, 11, 17, 17, 0, tzinfo=timezone.utc)
    assert b.publication_time == datetime(2026, 11, 16, 15, 7, tzinfo=timezone.utc)
    assert b.unscheduled is False and b.is_expired is False
    assert b.max_danger_level == 3 and b.max_danger_label == "considerable"
    assert [(r.region_id, r.name) for r in b.regions] == [("FR-BRA-3", "Mont-Blanc")]
    assert b.bulletin_url == "https://meteofrance.com/meteo-montagne"
    assert "Licence Ouverte 2.0" in b.attribution and "Météo-France" in b.attribution

    # Two elevation bands around 2700 m: level 2 below, level 3 above.
    low, high = b.danger_ratings
    assert (low.level, low.main_value, low.elevation.upper_bound, low.elevation.lower_bound) == (2, "moderate", 2700, None)
    assert (high.level, high.main_value, high.elevation.lower_bound, high.elevation.upper_bound) == (3, "considerable", 2700, None)

    # SAT1=2 (wind slab) and SAT2=4 (wet snow); every aspect of PENTE is "true" in this bulletin.
    assert [p.problem_type for p in b.avalanche_problems] == ["wind_slab", "wet_snow"]
    assert all(p.aspects == ["N", "NE", "E", "SE", "S", "SW", "W", "NW"] and p.comment is None for p in b.avalanche_problems)

    assert b.avalanche_activity_highlights == "Au-dessus de 2700m indice de risque marqué, plus bas indice limité."
    assert "Départs spontanés" in (b.avalanche_activity_comment or "")
    assert b.snowpack_highlights and b.snowpack_highlights.startswith("Nombreuses instabilités")
    # The body is TEXTESANSTITRE (the title is already in snowpack_highlights), then the snow-quality text as its own paragraph.
    assert not b.snowpack_comment.startswith(b.snowpack_highlights)
    assert "\n\n" in b.snowpack_comment

    (tendency,) = b.tendency
    assert tendency.valid_from == datetime(2026, 11, 17, 23, 0, tzinfo=timezone.utc)  # 18 Nov 00:00 Paris
    assert tendency.highlights and tendency.tendency_type is None


def test_haute_bigorre_has_one_band_no_aspects_and_ignores_sat_6() -> None:
    b = parse_bra_xml(HAUTE_BIGORRE_XML, NOW, region_name="x")
    assert b is not None
    assert b.bulletin_id == "FR-BRA-66-2026-11-16T16:00:00"
    assert b.max_danger_level == 1 and b.max_danger_label == "low"
    (rating,) = b.danger_ratings
    assert rating.level == 1 and rating.elevation is None
    assert b.avalanche_problems == []  # SAT1="6" is "pas de situation prédominante": no problem is invented
    assert b.regions[0].name == "Haute-Bigorre"
    assert b.valid_until == datetime(2026, 11, 17, 17, 0, tzinfo=timezone.utc)


def test_an_expired_bulletin_is_flagged() -> None:
    assert parse_bra_xml(MONT_BLANC_XML, AFTER_END).is_expired is True
    assert parse_bra_xml(MONT_BLANC_XML, NOW).is_expired is False


def test_an_empty_or_out_of_range_maximum_is_unknown_not_computed() -> None:
    for replacement in (b'RISQUEMAXI="" COMMENTAIRE', b'RISQUEMAXI="9" COMMENTAIRE', b'RISQUEMAXI="0" COMMENTAIRE'):
        data = HAUTE_BIGORRE_XML.replace(b'RISQUEMAXI="1" COMMENTAIRE', replacement, 1)
        assert data != HAUTE_BIGORRE_XML
        b = parse_bra_xml(data, NOW)
        assert b is not None and b.max_danger_level is None and b.max_danger_label is None


def test_sat_codes_are_mapped_deduplicated_and_unknown_ones_ignored() -> None:
    assert SAT_PROBLEMS == {
        "1": "new_snow",
        "2": "wind_slab",
        "3": "persistent_weak_layers",
        "4": "wet_snow",
        "5": "gliding_snow",
    }

    def problems(sat1: str, sat2: str) -> list[str]:
        data = MONT_BLANC_XML.replace(b'SAT1="2" SAT2="4"', f'SAT1="{sat1}" SAT2="{sat2}"'.encode(), 1)
        assert data != MONT_BLANC_XML or (sat1, sat2) == ("2", "4")
        return [p.problem_type for p in parse_bra_xml(data, NOW).avalanche_problems]

    assert problems("1", "1") == ["new_snow"]
    assert problems("3", "5") == ["persistent_weak_layers", "gliding_snow"]
    assert problems("6", "") == []
    assert problems("0", "9") == []


def test_aspects_come_from_the_slope_rose() -> None:
    data = MONT_BLANC_XML.replace(b'<PENTE NE="true" E="true" SE="true" S="true" SW="true" W="true" NW="true" N="true"',
                                  b'<PENTE NE="true" E="false" SE="false" S="false" SW="true" W="false" NW="true" N="true"', 1)
    b = parse_bra_xml(data, NOW)
    assert b.avalanche_problems[0].aspects == ["N", "NE", "SW", "NW"]


def test_not_a_bra_document_is_refused_and_an_empty_answer_is_no_bulletin() -> None:
    assert parse_bra_xml(b"", NOW) is None and parse_bra_xml(b"  \n", NOW) is None
    with pytest.raises(ValueError):
        parse_bra_xml(b"<html><body>maintenance</body></html>", NOW)
    with pytest.raises(ValueError):
        parse_bra_xml(b"not xml at all", NOW)
    with pytest.raises(ValueError):
        parse_bra_xml(b'<!DOCTYPE x [<!ENTITY a "b">]><BULLETINS_NEIGE_AVALANCHE ID="3"/>', NOW)
    assert parse_bra_xml(b'<BULLETINS_NEIGE_AVALANCHE ID="3"/>', NOW) is None  # no date: nothing to say


def test_paris_time_is_converted_with_the_european_summer_time_rule() -> None:
    assert paris_to_utc(datetime(2026, 11, 16, 16, 0)) == datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    assert paris_to_utc(datetime(2026, 7, 1, 12, 0)) == datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
    assert paris_to_utc(datetime(2026, 3, 29, 1, 59)) == datetime(2026, 3, 29, 0, 59, tzinfo=timezone.utc)
    assert paris_to_utc(datetime(2026, 3, 29, 3, 0)) == datetime(2026, 3, 29, 1, 0, tzinfo=timezone.utc)
    assert paris_to_utc(datetime(2026, 10, 25, 2, 30)) == datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)
    assert paris_to_utc(datetime(2026, 10, 25, 3, 0)) == datetime(2026, 10, 25, 2, 0, tzinfo=timezone.utc)
    assert paris_to_utc(datetime(2026, 12, 31, 23, 0)) == datetime(2026, 12, 31, 22, 0, tzinfo=timezone.utc)


def test_clean_text_keeps_the_line_breaks() -> None:
    assert clean_text("a  b\r\nc\n\n\n\nd <b>e</b> \n") == "a b\nc\n\nd e"
    assert clean_text("   ") is None and clean_text(None) is None


def test_key_shape_and_redaction() -> None:
    assert meteofrance_bra_key_from_env({}) == (None, None)
    key, problem = meteofrance_bra_key_from_env({"VIGIE_METEOFRANCE_API_KEY": "short"})
    assert key is None and "short" not in (problem or "")
    assert meteofrance_bra_key_from_env({"VIGIE_METEOFRANCE_API_KEY": SAMPLE_KEY}) == (SAMPLE_KEY, None)
    assert SAMPLE_KEY not in MeteoFranceBRACollector(SAMPLE_KEY).redact(f"401 for {SAMPLE_KEY}")


# ------------------------------------------------------------------------------------------------------------------ coverage


def test_the_massifs_are_found_offline() -> None:
    index = get_bra_index()
    assert len(index.codes()) == 35 and 71 not in index.codes()
    assert resolve_avalanche_coverage(*CHAMONIX) == ("meteofrance_bra", [AvalancheRegion(region_id="FR-BRA-3", name="Mont-Blanc")])
    for point in (GAVARNIE, BAREGES):
        source, regions = resolve_avalanche_coverage(*point)
        assert source == "meteofrance_bra" and [r.region_id for r in regions] == ["FR-BRA-66"]
    assert massif_code(AvalancheRegion(region_id="FR-BRA-66", name=None)) == 66
    assert massif_code(AvalancheRegion(region_id="CH-3311", name=None)) is None


def test_the_other_sources_keep_their_territory_and_paris_is_outside() -> None:
    assert resolve_avalanche_coverage(*PARIS) == (None, [])
    assert resolve_avalanche_coverage(*DAVOS) == ("slf_avalanche", [])
    assert resolve_avalanche_coverage(*INNSBRUCK)[0] == "euregio_avalanche"
    # An explicit choice is honoured only where the source covers the point.
    assert resolve_avalanche_coverage(*CHAMONIX, preferred_source="meteofrance")[0] == "meteofrance_bra"
    assert resolve_avalanche_coverage(*PARIS, preferred_source="meteofrance") == (None, [])
    assert resolve_avalanche_coverage(*CHAMONIX, preferred_source="slf") == (None, [])
    assert resolve_avalanche_coverage(*CHAMONIX, preferred_source="euregio") == (None, [])


# ----------------------------------------------------------------------------------------------------------------- collector


def _bra_client(seen: list[httpx.Request], *, answers: dict[str, httpx.Response] | None = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        code = request.url.params.get("id-massif", "")
        if answers and code in answers:
            return answers[code]
        return {"3": httpx.Response(200, content=MONT_BLANC_XML), "66": httpx.Response(200, content=HAUTE_BIGORRE_XML)}.get(
            code, httpx.Response(404)
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_one_cached_request_per_massif_with_the_key_in_the_header(sim_clock: SimClock) -> None:
    _set_now(sim_clock, NOW)
    seen: list[httpx.Request] = []
    collector = MeteoFranceBRACollector(SAMPLE_KEY, http_client=_bra_client(seen))

    first = await collector.fetch_massif(3)
    assert first.value.bulletin is not None and first.value.bulletin.bulletin_id.startswith("FR-BRA-3-")
    request = seen[0]
    assert str(request.url).split("?")[0] == BRA_URL
    assert dict(request.url.params) == {"id-massif": "3", "format": "xml"}
    assert request.headers["apikey"] == SAMPLE_KEY and SAMPLE_KEY not in str(request.url)

    await collector.fetch_massif(3)  # inside the 30 minutes: no new request
    assert len(seen) == 1
    await collector.fetch_massif(66)  # another massif, its own entry
    assert [r.url.params["id-massif"] for r in seen] == ["3", "66"]
    sim_clock.advance(1801)
    await collector.fetch_massif(3)
    assert len(seen) == 3


@pytest.mark.asyncio
async def test_a_404_is_no_bulletin_and_an_error_never_shows_the_key(sim_clock: SimClock) -> None:
    _set_now(sim_clock, NOW)
    seen: list[httpx.Request] = []
    collector = MeteoFranceBRACollector(
        SAMPLE_KEY, http_client=_bra_client(seen, answers={"5": httpx.Response(500, text=f"boom {SAMPLE_KEY}")})
    )
    none = await collector.fetch_massif(64)  # the stub answers 404
    assert none.value.bulletin is None and none.value.code == 64
    with pytest.raises(Exception) as err:
        await collector.fetch_massif(5)
    assert SAMPLE_KEY not in str(err.value) and SAMPLE_KEY not in (collector.last_error or "")


# ------------------------------------------------------------------------------------------------------------------ service


def _service(tmp_path: Path, seen: list[httpx.Request], **kwargs: Any):  # type: ignore[no-untyped-def]
    collector = MeteoFranceBRACollector(SAMPLE_KEY, http_client=_bra_client(seen, **kwargs))
    return make_service(tmp_path / "bra.db", meteofrance_bra=collector), collector


@pytest.mark.asyncio
async def test_chamonix_answers_with_the_massif_bulletin(tmp_path: Path, sim_clock: SimClock) -> None:
    _set_now(sim_clock, NOW)
    seen: list[httpx.Request] = []
    service, _ = _service(tmp_path, seen)

    res = await service.get_avalanches(lat=CHAMONIX[0], lon=CHAMONIX[1])
    assert res.status == "active" and res.covered_by == "meteofrance_bra"
    assert (res.region_id, res.region_name) == ("FR-BRA-3", "Mont-Blanc")
    assert (res.max_danger_level, res.max_danger_label) == (3, "considerable")
    assert len(res.bulletins) == 1 and res.bulletins[0].source == "meteofrance_bra"
    assert "Météo-France" in (res.attribution or "") and "Licence Ouverte 2.0" in (res.attribution or "")
    assert "Mont-Blanc" in res.note

    summary = await service.get_avalanche_safe(*CHAMONIX)
    assert summary is not None
    assert (summary.status, summary.covered_by, summary.region_id) == ("active", "meteofrance_bra", "FR-BRA-3")
    assert summary.problem_types == ["wind_slab", "wet_snow"] and summary.max_danger_level == 3
    assert summary.bulletin_url == "https://meteofrance.com/meteo-montagne"
    assert len(seen) == 1  # the second call came from the cache


@pytest.mark.asyncio
async def test_after_the_end_the_bulletin_is_expired_and_off_season_is_confirmed_none(tmp_path: Path, sim_clock: SimClock) -> None:
    _set_now(sim_clock, AFTER_END)
    service, _ = _service(tmp_path, [])
    res = await service.get_avalanches(lat=CHAMONIX[0], lon=CHAMONIX[1])
    assert res.status == "confirmed_none" and res.max_danger_level is None and res.bulletins == []
    assert "expired" in res.note
    with_expired = await service.get_avalanches(lat=CHAMONIX[0], lon=CHAMONIX[1], include_expired=True)
    assert with_expired.max_danger_level is None and with_expired.bulletins[0].is_expired is True

    # A massif the API has no bulletin for (404): confirmed_none, no invented level.
    off = await service.get_avalanches(lat=42.79, lon=0.59)  # Luchonnais (68): the stub answers 404
    assert off.status == "confirmed_none" and off.covered_by == "meteofrance_bra" and off.bulletins == []
    assert off.region_id == "FR-BRA-68"
    assert (await service.get_avalanche_safe(42.79, 0.59)).status == "confirmed_none"


@pytest.mark.asyncio
async def test_without_a_key_the_source_is_off_and_the_answer_says_so(
    tmp_path: Path, sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VIGIE_METEOFRANCE_API_KEY", raising=False)
    _set_now(sim_clock, NOW)
    service = make_service(tmp_path / "nokey.db")
    assert service.meteofrance_bra is None
    assert "meteofrance_bra" not in {c.provider_id for c in service.get_all_collectors()}
    off = {s["provider_id"]: s for s in service.disabled_sources()}
    assert off["meteofrance_bra"]["domain"] == "avalanches" and "VIGIE_METEOFRANCE_API_KEY" in off["meteofrance_bra"]["reason"]

    res = await service.get_avalanches(lat=CHAMONIX[0], lon=CHAMONIX[1])
    assert res.status == "unavailable" and res.covered_by == "meteofrance_bra"
    assert res.region_id == "FR-BRA-3" and res.bulletins == [] and res.attribution is None
    assert res.max_danger_level is None and "API key" in res.note
    assert await service.get_avalanche_safe(*CHAMONIX) is None
    # Outside France nothing changes.
    assert (await service.get_avalanches(lat=PARIS[0], lon=PARIS[1])).status == "outside_coverage"


def test_with_a_key_the_collector_is_listed_and_not_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIGIE_METEOFRANCE_API_KEY", SAMPLE_KEY)
    service = make_service(tmp_path / "key.db")
    assert isinstance(service.meteofrance_bra, MeteoFranceBRACollector)
    assert "meteofrance_bra" in {c.provider_id for c in service.get_all_collectors()}
    assert "meteofrance_bra" not in {s["provider_id"] for s in service.disabled_sources()}
    assert SAMPLE_KEY not in str(service.get_providers_health())


@pytest.mark.asyncio
async def test_an_upstream_error_without_cache_is_a_503_error_and_stale_is_flagged(tmp_path: Path, sim_clock: SimClock) -> None:
    from app.collectors.base import CollectorUnavailableError

    _set_now(sim_clock, NOW)
    mode = {"ok": True}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=MONT_BLANC_XML) if mode["ok"] else httpx.Response(502, text="bad gateway")

    collector = MeteoFranceBRACollector(SAMPLE_KEY, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    service = make_service(tmp_path / "err.db", meteofrance_bra=collector)
    assert (await service.get_avalanches(lat=CHAMONIX[0], lon=CHAMONIX[1])).stale is False
    mode["ok"] = False
    sim_clock.advance(1900)
    stale = await service.get_avalanches(lat=CHAMONIX[0], lon=CHAMONIX[1])
    assert stale.stale is True and stale.status == "active"
    # A massif never read and an upstream that fails: the route turns it into a 503.
    with pytest.raises(CollectorUnavailableError):
        await service.get_avalanches(lat=GAVARNIE[0], lon=GAVARNIE[1])


# ----------------------------------------------------------------------------------------------------------------- HTTP route


def test_http_route_and_point_summary(world: World, sim_clock: SimClock) -> None:
    _set_now(sim_clock, NOW)
    seen: list[httpx.Request] = []
    world.aggregator.meteofrance_bra = MeteoFranceBRACollector(SAMPLE_KEY, http_client=_bra_client(seen))

    r = world.client.get(f"/v1/avalanches?lat={CHAMONIX[0]}&lon={CHAMONIX[1]}")
    assert r.status_code == 200
    body = r.json()
    assert (body["status"], body["covered_by"], body["region_id"], body["region_name"]) == ("active", "meteofrance_bra", "FR-BRA-3", "Mont-Blanc")
    assert (body["max_danger_level"], body["max_danger_label"]) == (3, "considerable")
    assert body["bulletins"][0]["danger_ratings"][1]["elevation"] == {"lower_bound": 2700, "upper_bound": None}

    forced = world.client.get(f"/v1/avalanches?lat={GAVARNIE[0]}&lon={GAVARNIE[1]}&source=meteofrance")
    assert forced.status_code == 200 and forced.json()["region_id"] == "FR-BRA-66" and forced.json()["max_danger_level"] == 1
    # `source=meteofrance` outside a massif is outside coverage, and the old values still validate.
    assert world.client.get(f"/v1/avalanches?lat={PARIS[0]}&lon={PARIS[1]}&source=meteofrance").json()["status"] == "outside_coverage"
    assert world.client.get(f"/v1/avalanches?lat={CHAMONIX[0]}&lon={CHAMONIX[1]}&source=bogus").status_code == 422

    point = world.client.get(f"/v1/point?lat={CHAMONIX[0]}&lon={CHAMONIX[1]}")
    assert point.status_code == 200
    block = point.json()["avalanche"]
    assert (block["status"], block["covered_by"], block["max_danger_level"], block["region_name"]) == ("active", "meteofrance_bra", 3, "Mont-Blanc")
    assert point.json()["local_alert_level"] == SeverityLevel.GREEN.value  # informative only

    # The health answer lists the collector among the providers.
    health = world.client.get("/v1/health").json()
    assert "meteofrance_bra" in {p["provider_id"] for p in health["providers"]}
    assert "meteofrance_bra" not in {s["provider_id"] for s in health["disabled_sources"]}


def test_http_route_without_a_key_answers_unavailable_with_200(world: World, sim_clock: SimClock) -> None:
    _set_now(sim_clock, NOW)
    world.aggregator.meteofrance_bra = None
    r = world.client.get(f"/v1/avalanches?lat={CHAMONIX[0]}&lon={CHAMONIX[1]}")
    assert r.status_code == 200 and r.json()["status"] == "unavailable" and r.json()["covered_by"] == "meteofrance_bra"
    assert world.client.get(f"/v1/point?lat={CHAMONIX[0]}&lon={CHAMONIX[1]}").json()["avalanche"] is None
    health = world.client.get("/v1/health").json()
    assert "meteofrance_bra" in {s["provider_id"] for s in health["disabled_sources"]}


def test_the_cached_value_type_is_what_the_service_reads() -> None:
    value = BRAMassifBulletin(3, parse_bra_xml(MONT_BLANC_XML, NOW), NOW)
    assert Fetched(value).value.bulletin.max_danger_level == 3


# ------------------------------------------------------------------------------------------------------------- build tool


def test_the_build_tool_keeps_codes_and_names_and_writes_a_deterministic_file(tmp_path: Path) -> None:
    import gzip
    import json

    from tools import build_bra_massifs as tool

    square = {"type": "MultiPolygon", "coordinates": [[[[6.0, 45.0], [7.0, 45.0], [7.0, 46.0], [6.0, 46.0], [6.0, 45.0]]]]}
    payload = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"code": 3, "title": "Mont-Blanc", "mountain": "Alpes du Nord", "Departement": "Haute-Savoie"}, "geometry": square},
            {"type": "Feature", "properties": {"code": "x", "title": "bad code"}, "geometry": square},
            {"type": "Feature", "properties": {"code": 4, "title": "no shape"}, "geometry": None},
        ],
    }
    collection = tool.convert(payload)
    assert [f["properties"]["code"] for f in collection["features"]] == [3]
    assert collection["features"][0]["properties"]["name"] == "Mont-Blanc" and collection["license"].startswith("Licence Ouverte")
    first, second = tmp_path / "a.geojson.gz", tmp_path / "b.geojson.gz"
    tool.write_collection(first, collection)
    tool.write_collection(second, collection)
    assert first.read_bytes() == second.read_bytes() and first.read_bytes()[4:8] == b"\x00\x00\x00\x00"
    assert json.loads(gzip.decompress(first.read_bytes()))["features"][0]["geometry"]["type"] in {"Polygon", "MultiPolygon"}  # a lone polygon may collapse
    with pytest.raises(ValueError):
        tool.convert({"type": "nope"})
