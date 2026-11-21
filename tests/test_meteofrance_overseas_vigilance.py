"""Tests for the official Météo-France vigilance of the overseas territories (``DPVigilance/v1``, ADR 0036).

The fixtures are real answers read on 2026-11-21 (Guadeloupe in yellow for rain and thunderstorms, French Polynesia in yellow
for waves, the others without a level), cut down to the files the parser reads.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from app.collectors.meteofrance_overseas_vigilance import (
    COUNTRY_FEEDS,
    FEED_URLS,
    MeteoFranceOverseasVigilanceCollector,
    overseas_vigilance_enabled_from_env,
    parse_feed,
    parse_zip,
)
from app.models.schemas import SeverityLevel
from app.models.vigilance import VigilancePhenomenon
from app.services.geoindex import default_geometries
from tests.vigilance_upstream import Upstream, make_service

FIXTURES = Path(__file__).parent / "fixtures" / "meteofrance_vigilance_overseas"
KEY = "mf_secret_token_1234567890abcdef=="
POINTS = {  # a point on land of each territory
    "GP": (16.2, -61.55),
    "MQ": (14.64, -61.02),
    "RE": (-21.12, 55.53),
    "YT": (-12.78, 45.23),
    "GF": (4.0, -53.0),
    "PM": (46.78, -56.18),
    "PF": (-17.55, -149.55),
    "NC": (-22.27, 166.45),
}


def zip_bytes(extra: dict[str, bytes] | None = None, skip: tuple[str, ...] = ()) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in sorted(FIXTURES.iterdir()):
            if path.suffix in (".txt", ".xml") and path.name not in skip:
                archive.writestr(path.name, path.read_bytes())
        for name, content in (extra or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def collector(handler) -> tuple[MeteoFranceOverseasVigilanceCollector, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return MeteoFranceOverseasVigilanceCollector(KEY, http_client=client), client


def feeds_handler(requests: list[str], zip_content: bytes | None = None, down: tuple[str, ...] = ()):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("apikey") == KEY
        url = str(request.url)
        requests.append(url)
        feed = next((name for name, address in FEED_URLS.items() if address == url), None)
        if feed is None or feed in down:
            return httpx.Response(503)
        if feed == "zip":
            return httpx.Response(200, content=zip_content if zip_content is not None else zip_bytes())
        name = "polynesie.json" if feed == "polynesia" else "new-caledonia.json"
        return httpx.Response(200, content=(FIXTURES / name).read_bytes())

    return handler


def test_the_real_zip_gives_the_yellow_warning_of_guadeloupe_only() -> None:
    status = parse_zip(zip_bytes())
    assert [(v.country, v.level, v.phenomenon, v.phenomenon_original) for v in status.vigilances] == [
        ("GP", SeverityLevel.YELLOW, VigilancePhenomenon.RAIN, "Pluie-orages")
    ]
    guadeloupe = status.vigilances[0]
    assert guadeloupe.source == "meteofrance"
    assert [(g.scheme, g.value) for g in guadeloupe.area.geocodes] == [("MF_OM", "971")]
    assert status.updated_utc is not None


def test_polynesia_and_new_caledonia_maps() -> None:
    polynesia = parse_feed("polynesia", (FIXTURES / "polynesie.json").read_bytes())
    assert [(v.country, v.level, v.phenomenon) for v in polynesia.vigilances] == [
        ("PF", SeverityLevel.YELLOW, VigilancePhenomenon.COASTAL)
    ]
    assert polynesia.updated_utc is not None
    assert parse_feed("new-caledonia", (FIXTURES / "new-caledonia.json").read_bytes()).vigilances == ()


def test_orange_red_and_cyclone_colours_of_a_map() -> None:
    payload = json.loads((FIXTURES / "polynesie.json").read_text(encoding="utf-8"))

    def with_color(color: int) -> bytes:
        text = json.dumps(payload)
        # The real yellow vagues-submersion item (colour 2 in the feed) takes the colour under test.
        return (
            text.replace('"color_id": 2', f'"color_id": {color}')
            .replace('"phenomenon_max_color_id": 2', f'"phenomenon_max_color_id": {color}')
            .replace('"max_color_id": 2', f'"max_color_id": {color}')
            .encode()
        )

    levels = {color: [v.level for v in parse_feed("polynesia", with_color(color)).vigilances] for color in (3, 4, 5, 6)}
    assert levels[3] == [SeverityLevel.ORANGE]
    assert levels[4] == [SeverityLevel.RED]
    assert levels[5] == [None] and levels[6] == [None]  # the cyclone phases are listed without a Vigie level (ADR 0036, not verified)


def test_a_bad_archive_is_refused_and_an_unreadable_file_leaves_its_territory_out() -> None:
    with pytest.raises(ValueError):
        parse_zip(b"not a zip")
    with pytest.raises(ValueError):
        parse_zip(
            zip_bytes(
                skip=tuple(p.name for p in FIXTURES.glob("CDPV8[45]*")) + ("CDPV95_FMEE_.txt", "NXFR33_LFVP_.xml")
            )
        )
    broken = zip_bytes(skip=("CDPV85_TFFR_.txt",), extra={"CDPV85_TFFR_.txt": b"{not json"})
    status = parse_zip(broken)  # the other maps still read
    assert all(v.country != "GP" for v in status.vigilances)


def test_the_toggle() -> None:
    assert overseas_vigilance_enabled_from_env({}) is True
    assert overseas_vigilance_enabled_from_env({"VIGIE_METEOFRANCE_VIGILANCE_OVERSEAS": "0"}) is False
    assert overseas_vigilance_enabled_from_env({"VIGIE_METEOFRANCE_VIGILANCE_OVERSEAS": "off"}) is False
    assert overseas_vigilance_enabled_from_env({"VIGIE_METEOFRANCE_VIGILANCE_OVERSEAS": "1"}) is True


def test_every_territory_has_a_country_and_a_feed() -> None:
    assert set(COUNTRY_FEEDS) == {"GP", "MQ", "GF", "RE", "YT", "MF", "BL", "PM", "PF", "NC"}
    assert set(COUNTRY_FEEDS.values()) == {"zip", "polynesia", "new-caledonia"}


@pytest.mark.asyncio
async def test_the_service_serves_each_territory_from_its_feed_and_asks_each_feed_once() -> None:
    requests: list[str] = []
    mf, client = collector(feeds_handler(requests))
    service = make_service(Upstream(), national=True)
    service.meteofrance_overseas = mf

    assert {"GP", "MQ", "RE", "YT", "GF", "PM", "PF", "NC", "MF", "BL"} <= set(service.supported_countries())
    assert service._plan("GP") == ["meteofrance"]

    guadeloupe = await service.for_point(*POINTS["GP"])
    assert [(v.source, v.level, v.phenomenon) for v in guadeloupe.vigilances] == [
        ("meteofrance", SeverityLevel.YELLOW, VigilancePhenomenon.RAIN)
    ]
    assert [(c.source, c.status) for c in guadeloupe.coverage] == [("meteofrance", "ok")]

    martinique = await service.for_point(*POINTS["MQ"])
    assert martinique.vigilances == []
    assert [c.status for c in martinique.coverage] == ["ok"]
    reunion = await service.for_country("RE")
    assert reunion.vigilances == []
    assert requests == [FEED_URLS["zip"]]  # one ZIP for the three questions

    polynesia = await service.for_point(*POINTS["PF"])
    assert [(v.level, v.phenomenon) for v in polynesia.vigilances] == [
        (SeverityLevel.YELLOW, VigilancePhenomenon.COASTAL)
    ]
    caledonia = await service.for_point(*POINTS["NC"])
    assert caledonia.vigilances == [] and [c.status for c in caledonia.coverage] == ["ok"]
    assert requests == [FEED_URLS["zip"], FEED_URLS["polynesia"], FEED_URLS["new-caledonia"]]
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("country", sorted(COUNTRY_FEEDS))
async def test_for_country_answers_for_every_overseas_territory(country: str) -> None:
    mf, client = collector(feeds_handler([]))
    service = make_service(Upstream(), national=True)
    service.meteofrance_overseas = mf
    response = await service.for_country(country)
    assert [c.status for c in response.coverage] == ["ok"]
    await client.aclose()


@pytest.mark.asyncio
async def test_a_feed_that_fails_makes_only_its_territories_unavailable() -> None:
    mf, client = collector(feeds_handler([], down=("polynesia",)))
    service = make_service(Upstream(), national=True)
    service.meteofrance_overseas = mf
    assert [c.status for c in (await service.for_country("PF")).coverage] == ["unavailable"]
    assert [c.status for c in (await service.for_country("GP")).coverage] == ["ok"]
    await client.aclose()


@pytest.mark.asyncio
async def test_without_the_collector_the_territories_are_not_served_and_france_is_untouched() -> None:
    service = make_service(Upstream(), national=True)
    assert service.meteofrance_overseas is None
    assert "GP" not in service.supported_countries()
    with pytest.raises(KeyError):
        await service.for_country("GP")


def test_the_outlines_hold_their_territory() -> None:
    geometries = default_geometries()
    expected = {"GP": "971", "MQ": "972", "RE": "974", "YT": "976", "GF": "973", "PM": "975", "PF": "987", "NC": "988"}
    for country, (lat, lon) in POINTS.items():
        assert ("MF_OM", expected[country]) in geometries.codes_at(lat, lon), country


@pytest.mark.asyncio
async def test_an_overseas_warning_is_archived_like_the_others(tmp_path: Path) -> None:
    from app.models.vigilance import VigilanceArchiveQuery
    from app.services.storage_wal import SQLiteWALStorage
    from app.services.vigilance_archive import VigilanceArchive

    store = SQLiteWALStorage(db_path=tmp_path / "archive.db")
    mf, client = collector(feeds_handler([]))
    service = make_service(Upstream(), national=True)
    service.meteofrance_overseas = mf
    service.archive = VigilanceArchive(lambda: store, keep_days=0)
    await service.for_country("GP")
    items = (await service.archive.query(VigilanceArchiveQuery(limit=50))).items
    assert [(item.vigilance.source, item.vigilance.country, item.vigilance.level) for item in items] == [
        ("meteofrance", "GP", SeverityLevel.YELLOW)
    ]
    await client.aclose()
