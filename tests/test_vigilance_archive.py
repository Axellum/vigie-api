"""The archive of the official warnings (``app/services/vigilance_archive.py``, ``GET /v1/vigilances/archive``).

Every warning a source lists is kept once per message and area, as the API serves it, with when it was first and last seen; the
sources read are noted hour by hour; the archive sends no request of its own. Nothing reaches the network.
"""

from __future__ import annotations

import zlib
from collections import Counter
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock

import pytest
from shapely.geometry import Polygon

from app.collectors.base import Fetched
from app.collectors.meteoalarm import CapDocument, with_cap
from app.models.schemas import SeverityLevel
from app.models.vigilance import Vigilance, VigilanceArchiveQuery, VigilancePhenomenon, VigilanceStatus
from app.services.storage_wal import SQLiteWALStorage
from app.services.vigilance import FollowedPoint
from app.services.vigilance_archive import ARCHIVE_TOUCH_S, VigilanceArchive, keep_days_from_env
from tests import vigilance_samples as samples
from tests.conftest import World
from tests.helpers import SimClock
from tests.vigilance_upstream import Upstream, make_service

DAY_S = 86_400


def as_served(vigilance: Vigilance, extra: Any) -> Vigilance:
    """What ``VigilanceService`` gives the archive: the CAP words of a MeteoAlarm warning when the document is held."""
    return with_cap(vigilance, extra, None) if extra is not None else vigilance


@pytest.fixture()
def store(tmp_path: Path) -> SQLiteWALStorage:
    return SQLiteWALStorage(db_path=tmp_path / "archive.db")


@pytest.fixture()
def archive(store: SQLiteWALStorage, sim_clock: SimClock) -> VigilanceArchive:
    sim_clock.advance(samples.RECORDED_AT - SimClock.EPOCH)
    return VigilanceArchive(lambda: store, keep_days=0)


async def everything(archive: VigilanceArchive, **filters: Any) -> list[Any]:
    return (await archive.query(VigilanceArchiveQuery(limit=500, **filters))).items


async def test_a_warning_seen_again_is_one_row_whose_last_sighting_moves(archive: VigilanceArchive, sim_clock: SimClock) -> None:
    warning = samples.warning()
    first = int(sim_clock.time())
    await archive.observe([("meteoalarm", "FR")], [(warning, None)], as_served)
    sim_clock.advance(ARCHIVE_TOUCH_S - 1)
    await archive.observe([("meteoalarm", "FR")], [(warning, None)], as_served)
    items = await everything(archive)
    assert [(item.first_seen_utc, item.last_seen_utc) for item in items] == [(first, first)]  # not written again so soon
    sim_clock.advance(2)
    await archive.observe([("meteoalarm", "FR")], [(warning, None)], as_served)
    items = await everything(archive)
    assert [(item.first_seen_utc, item.last_seen_utc) for item in items] == [(first, first + ARCHIVE_TOUCH_S + 1)]
    assert items[0].vigilance == warning  # kept as served


async def test_an_update_of_the_source_is_a_new_row(archive: VigilanceArchive) -> None:
    first = samples.warning(identifier="msg.1")
    update = samples.warning(identifier="msg.2", level=SeverityLevel.ORANGE, status=VigilanceStatus.UPDATE)
    await archive.observe([("meteoalarm", "FR")], [(first, None)], as_served)
    await archive.observe([("meteoalarm", "FR")], [(update, None), (first, None)], as_served)
    items = await everything(archive)
    assert sorted(item.vigilance.identifier for item in items) == ["msg.1", "msg.2"]


def test_a_cancellation_and_an_ended_warning_are_kept_too(recorded: World) -> None:
    """The feeds list warnings that no answer returns (cancelled, past their end): the archive keeps what the source listed."""
    recorded.client.get("/v1/vigilances", params={"country": "FR"})
    answer = recorded.client.get("/v1/vigilances", params={"country": "FR"}).json()
    archived = recorded.client.get("/v1/vigilances/archive", params={"country": "FR", "limit": 500}).json()
    listed = samples.feed("FR").vigilances
    assert archived["total"] == len({(v.id, v.identifier) for v in listed})
    assert archived["total"] > len(answer["vigilances"])  # the Hérault orange of the day before is over: archived, not served


async def test_the_words_of_a_meteoalarm_warning_come_when_the_document_is_held(archive: VigilanceArchive, sim_clock: SimClock) -> None:
    document = samples.cap()
    warning = next(v for v in samples.feed("FR").vigilances if v.link and v.link.endswith("b77d3bc0-76a2-4886-8da8-bac8d99f57fc"))
    first = int(sim_clock.time())
    await archive.observe([("meteoalarm", "FR")], [(warning, None)], as_served)
    assert (await everything(archive))[0].vigilance.texts == []
    sim_clock.advance(60)
    await archive.observe([("meteoalarm", "FR")], [(warning, document)], as_served)
    item = (await everything(archive))[0]
    assert item.first_seen_utc == first
    assert item.vigilance.texts == list(document.texts)  # every language, untouched
    assert item.vigilance.issuer == document.issuer


async def test_the_filters(archive: VigilanceArchive) -> None:
    seen = [
        (samples.warning("FR713", identifier="a", onset=1_000, expires=2_000), None),
        (samples.warning("FR813", SeverityLevel.ORANGE, VigilancePhenomenon.WIND, identifier="b", onset=3_000, expires=4_000), None),
        (samples.warning("CH012", SeverityLevel.RED, VigilancePhenomenon.RAIN, identifier="c", country="CH", onset=5_000, expires=None), None),
    ]
    await archive.observe([("meteoalarm", "FR"), ("meteoalarm", "CH")], seen, as_served)

    async def ids(**filters: Any) -> list[str]:
        return sorted(item.vigilance.identifier for item in await everything(archive, **filters))

    assert await ids() == ["a", "b", "c"]
    assert await ids(country="CH") == ["c"]
    assert await ids(phenomenon=VigilancePhenomenon.WIND) == ["b"]
    assert await ids(level=SeverityLevel.YELLOW) == ["a"]
    assert await ids(min_level=SeverityLevel.ORANGE) == ["b", "c"]
    assert await ids(area="FR813") == ["b"]
    assert await ids(area="NUTS3:FR713") == ["a"]
    assert await ids(area="fr8") == ["b"]  # a part of the name, case ignored
    assert await ids(area="FR8") == ["b"]  # not a code: no row has the code "FR8"
    assert await ids(area="100%") == []  # the wildcards of LIKE are escaped
    assert await ids(since_utc=2_500, until_utc=3_500) == ["b"]  # the periods that overlap
    assert await ids(since_utc=4_500) == ["c"]  # no end and no expiry: it lasts until its last sighting
    assert await ids(source="meteoalarm") == ["a", "b", "c"]
    assert await ids(source="dwd") == []


async def test_a_page_and_the_total(archive: VigilanceArchive, sim_clock: SimClock) -> None:
    for index in range(5):
        await archive.observe([("meteoalarm", "FR")], [(samples.warning(identifier=f"m{index}"), None)], as_served)
        sim_clock.advance(1)
    page = await archive.query(VigilanceArchiveQuery(limit=2, offset=1))
    assert page.total == 5
    assert [item.vigilance.identifier for item in page.items] == ["m3", "m2"]  # the newest first seen first


async def test_a_shape_shared_by_several_warnings_is_kept_once(archive: VigilanceArchive, store: SQLiteWALStorage) -> None:
    square = Polygon([(6.0, 53.0), (7.0, 53.0), (7.0, 54.0), (6.0, 54.0)])
    one = samples.warning("cell1", identifier="x", source="dwd", country="DE").with_shape(square)
    two = samples.warning("cell1", identifier="y", source="dwd", country="DE").with_shape(square)
    await archive.observe([("dwd", "DE")], [(one, None), (two, None)], as_served)
    stats = await archive.stats(1)
    assert stats.warnings == 2
    assert stats.shape_bytes == len(zlib.compress(square.wkb, 6))
    with store._connect() as conn:
        boxes = conn.execute("SELECT DISTINCT shape_hash, min_lon, min_lat, max_lon, max_lat FROM vigilance_archive").fetchall()
    assert [tuple(row)[1:] for row in boxes] == [(6.0, 53.0, 7.0, 54.0)]


async def test_the_hours_each_source_was_read(archive: VigilanceArchive, sim_clock: SimClock) -> None:
    """A source that answered with nothing is a read: the archive can then say that nothing was in force."""
    start = int(sim_clock.time())
    await archive.observe([("meteoalarm", "DK")], [], as_served)
    sim_clock.advance(3600)
    await archive.observe([("meteoalarm", "DK"), ("swic:mx-smn-es", "MX")], [], as_served)
    answer = await archive.query(VigilanceArchiveQuery(country="DK"))
    assert answer.items == []
    assert (start // 3600) % 24 == 10  # 2026-10-20 10:05 UTC, then 11:05 the same day
    assert [(read.source, read.country, read.day, read.hours) for read in answer.reads] == [("meteoalarm", "DK", "2026-10-20", [10, 11])]
    assert (answer.reads[0].first_utc, answer.reads[0].last_utc) == (start, start + 3600)


async def test_keep_days_forgets_the_old_warnings(store: SQLiteWALStorage, sim_clock: SimClock) -> None:
    sim_clock.advance(samples.RECORDED_AT - SimClock.EPOCH)
    archive = VigilanceArchive(lambda: store, keep_days=30)
    await archive.observe([("meteoalarm", "FR")], [(samples.warning(identifier="old"), None)], as_served)
    sim_clock.advance(31 * DAY_S)
    await archive.observe([("meteoalarm", "FR")], [(samples.warning(identifier="new"), None)], as_served)
    assert [item.vigilance.identifier for item in await everything(archive)] == ["new"]
    assert (await archive.query(VigilanceArchiveQuery())).keep_days == 30


def test_keep_days_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for raw, expected in (("", 0), ("400", 400), ("-3", 0), ("ten", 0)):
        monkeypatch.setenv("VIGIE_ARCHIVE_KEEP_DAYS", raw)
        assert keep_days_from_env() == expected


def test_a_broken_archive_never_fails_an_answer(recorded: World, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_: Any, **__: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(recorded.aggregator.storage, "archive_vigilances", broken)
    response = recorded.client.get("/v1/vigilances", params={"country": "FR"})
    assert response.status_code == 200 and response.json()["vigilances"]
    health = recorded.client.get("/v1/health").json()["vigilance_archive"]
    assert health["last_error"] == "OSError: disk full"


# ---------------------------------------------------------------------------------------------- the routes


@pytest.fixture()
def recorded(world: World, sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch) -> World:
    """The application with the recorded feeds of 2026-10-20 (MeteoAlarm with one CAP document, the DWD) in place of the providers."""
    sim_clock.advance(samples.RECORDED_AT - SimClock.EPOCH)

    async def feed(country: str, **_: Any) -> Fetched[Any]:
        if country in samples.FEED_FILES:
            return Fetched(samples.feed(country))
        return Fetched(samples.feed("DK").__class__(country=country, updated_utc=None, vigilances=()))

    async def document(url: str) -> Fetched[Any]:
        if url.endswith("b77d3bc0-76a2-4886-8da8-bac8d99f57fc"):
            return Fetched(samples.cap())
        return Fetched(CapDocument(available=False))

    monkeypatch.setattr(world.aggregator.meteoalarm, "fetch_country", AsyncMock(side_effect=feed))
    monkeypatch.setattr(world.aggregator.meteoalarm_cap, "fetch_document", AsyncMock(side_effect=document))
    monkeypatch.setattr(world.aggregator.dwd, "fetch_status", AsyncMock(return_value=Fetched(samples.dwd_status())))
    monkeypatch.setattr(world.aggregator.vigicrues, "fetch_flow", AsyncMock(return_value=Fetched(samples.all_green_flow())))
    return world


def test_the_route_returns_what_an_answer_served(recorded: World) -> None:
    served = recorded.client.get("/v1/vigilances", params={"country": "FR"}).json()["vigilances"]
    answer = recorded.client.get("/v1/vigilances/archive", params={"country": "FR", "limit": 500}).json()
    archived = {item["vigilance"]["id"]: item["vigilance"] for item in answer["items"]}
    for vigilance in served:  # the same warning, with the same words
        assert archived[vigilance["id"]] == vigilance
    with_texts = [v for v in archived.values() if v["texts"]]
    assert with_texts and all(v["link"].endswith("b77d3bc0-76a2-4886-8da8-bac8d99f57fc") for v in with_texts)
    assert answer["attribution"] and "MeteoAlarm" in answer["attribution"]
    assert answer["disclaimer"]
    assert answer["note"] and "only if `reads`" in answer["note"]
    assert {(read["source"], read["country"]) for read in answer["reads"]} >= {("meteoalarm", "FR"), ("vigicrues", "FR")}


def test_the_route_asks_no_provider(recorded: World) -> None:
    recorded.client.get("/v1/vigilances", params={"country": "DE"})
    calls = recorded.aggregator.dwd.fetch_status.call_count  # type: ignore[attr-defined]
    answer = recorded.client.get("/v1/vigilances/archive", params={"country": "DE", "source": "dwd"}).json()
    assert answer["total"] == len(samples.dwd_status().vigilances)
    assert recorded.aggregator.dwd.fetch_status.call_count == calls  # type: ignore[attr-defined]
    assert recorded.aggregator.meteoalarm.fetch_country.call_count == 0  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "params",
    [
        {"country": "FRA"},
        {"source": "met_office"},
        {"level": "PURPLE"},
        {"phenomenon": "meteor"},
        {"since": 10, "until": 5},
        {"limit": 501},
        {"area": "x"},
    ],
)
def test_the_route_refuses_a_bad_query(recorded: World, params: dict[str, Any]) -> None:
    assert recorded.client.get("/v1/vigilances/archive", params=params).status_code == 422


def test_the_stats_route(recorded: World) -> None:
    recorded.client.get("/v1/vigilances", params={"country": "FR"})
    stats = recorded.client.get("/v1/vigilances/archive/stats", params={"days": 3}).json()
    today = stats["days"][0]
    assert stats["warnings"] == today["warnings_first_seen"] > 0
    assert today["payload_bytes"] == stats["payload_bytes"] > 0
    assert today["sources_read"] >= 2 and today["countries_read"] == 1
    assert stats["database_bytes"] > 0
    assert stats["keep_days"] is None
    health = recorded.client.get("/v1/health").json()["vigilance_archive"]
    assert health["warnings"] == stats["warnings"] and health["last_error"] is None


# ---------------------------------------------------------------------------------------------- what it costs


async def test_a_day_of_the_scheduler_with_the_archive_asks_nothing_more(tmp_path: Path, sim_clock: SimClock) -> None:
    """The five default zones for a day, with and without the archive: the same requests, and the archive writes little."""
    from app.services.storage_wal import DEFAULT_WATCHLIST_SEED

    sim_clock.advance(samples.RECORDED_AT - SimClock.EPOCH)
    zones = [dict(zone) for zone in DEFAULT_WATCHLIST_SEED]
    points = [FollowedPoint(key=f"zone:{z['id']}", lat=float(z["lat"]), lon=float(z["lon"]), label={}, audience="all") for z in zones]  # type: ignore[arg-type]

    async def day(store: Optional[SQLiteWALStorage]) -> tuple[Counter[str], int, int]:
        upstream = Upstream()
        upstream.default_cap = samples.fixture("cap_france_b77d3bc0.xml")
        service = make_service(upstream, zones)
        writes = 0
        if store is not None:
            service.archive = VigilanceArchive(lambda: store, keep_days=0)
            real = store.archive_vigilances

            def counted(*args: Any) -> None:
                nonlocal writes
                writes += 1
                real(*args)

            store.archive_vigilances = counted  # type: ignore[method-assign]
        start = sim_clock.now
        while sim_clock.now < start + DAY_S:
            await service.refresh(points)
            await service.poll_changes(points)
            sim_clock.advance(90)
        return Counter(upstream.feed_requests), len(upstream.cap_requests), writes

    without, documents_without, _ = await day(None)
    store = SQLiteWALStorage(db_path=tmp_path / "day.db")
    with_archive, documents_with, writes = await day(store)
    assert with_archive == without and documents_with == documents_without == 0
    warnings, _, _ = store.vigilance_archive_summary()
    print(f"\na day of the 5 default zones: {warnings} warnings archived in {writes} writes, {store.database_bytes()} bytes")
    assert warnings > 0
    # 960 cycles look at the five zones (5,760 passes); the archive writes when a country's warnings are due for a touch (every ten
    # minutes, France and Iceland apart) and once an hour for the reads of each source.
    assert writes <= 2 * (DAY_S // ARCHIVE_TOUCH_S + 1) + 3 * 24
