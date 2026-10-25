"""The pilot of the pre-alert for Turkey (ADR 0011 and its amendment): which run is read, what is asked of the ECMWF mirror, what is
stored, and what ``GET /v1/experimental/prealert`` answers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

pytest.importorskip("numpy")
pytest.importorskip("eccodes")

import app.main as main  # noqa: E402
from app.prealert.shadow import (  # noqa: E402
    EcmwfOpenDataCollector,
    LevelRow,
    ShadowPilot,
    ShadowStore,
    candidate_runs,
    enabled,
    file_url,
    wanted_fields,
)
from app.prealert.view import NOTICE, OFFICIAL_WARNINGS_URL, OutsideTurkey, prealert_at  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ecmwf_opendata"
NOW = datetime(2026, 10, 25, 9, 30, tzinfo=timezone.utc)
RUN = datetime(2026, 10, 25, 0, tzinfo=timezone.utc)
ISTANBUL = (41.01, 28.98)


def test_the_pilot_is_off_unless_asked() -> None:
    assert not enabled({})
    assert not enabled({"VIGIE_PREALERT_TR": "0"})
    assert enabled({"VIGIE_PREALERT_TR": "1"}) and enabled({"VIGIE_PREALERT_TR": " On "})


def test_candidate_runs_are_00_and_12_utc_newest_first_over_30_hours() -> None:
    assert candidate_runs(NOW) == [RUN, datetime(2026, 10, 24, 12, tzinfo=timezone.utc)]


def test_file_url_on_the_mirror() -> None:
    assert file_url(RUN, 72) == "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com/20261025/00z/ifs/0p25/oper/20261025000000-72h-oper-fc"


def test_wanted_fields_are_the_five_surface_fields_and_one_missing_is_an_error() -> None:
    index = (FIXTURES / "20261025000000-3h-oper-fc.index").read_text(encoding="utf-8")
    assert sorted(name for name, _, _ in wanted_fields(index)) == ["10fg", "mn2t3", "mx2t3", "ptype", "tp"]
    without_tp = "\n".join(line for line in index.splitlines() if '"param": "tp"' not in line)
    with pytest.raises(ValueError, match="no tp"):
        wanted_fields(without_tp)


def mirror(asked: list[str], complete: tuple[str, ...] = ("/20261025/00z/",)):
    """The mirror as recorded on 2026-10-25 (leads 3 and 6 of the run of 00 UTC, cut to Turkey); ``complete`` lists the runs whose last
    lead answers."""

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        asked.append(f"{request.method} {name} {request.headers.get('Range', '')}".strip())
        if request.method == "HEAD":
            return httpx.Response(200 if any(run in request.url.path for run in complete) else 404)
        if name.endswith(".index"):
            return httpx.Response(200, content=(FIXTURES / name).read_bytes())
        first, last = (int(v) for v in request.headers["Range"].removeprefix("bytes=").split("-"))
        return httpx.Response(206, content=(FIXTURES / name).read_bytes()[first : last + 1])

    return handler


def _collector(tmp_path: Path, handler) -> EcmwfOpenDataCollector:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    collector = EcmwfOpenDataCollector(ShadowStore(tmp_path / "shadow.db"), http_client=client)
    collector.pause_s = 0.0
    collector.last_lead_h = 6  # the two recorded leads
    collector.now = lambda: NOW
    return collector


async def test_reads_the_newest_complete_run_and_stores_its_levels(tmp_path: Path) -> None:
    asked: list[str] = []
    collector = _collector(tmp_path, mirror(asked))
    result = await collector.fetch(force_refresh=True)
    assert result.run == RUN
    assert asked[0] == "HEAD 20261025000000-6h-oper-fc.index"
    # Per lead: its index, then one byte range per field (five fields), nothing else.
    assert [a for a in asked[1:] if a.startswith("GET") and a.endswith(".index")] == [
        "GET 20261025000000-3h-oper-fc.index",
        "GET 20261025000000-6h-oper-fc.index",
    ]
    assert len(asked) == 1 + 2 * (1 + 5)
    assert all("bytes=" in a for a in asked if ".grib2" in a)
    # 81 provinces x 5 phenomena for the one local day the two leads cover.
    assert len(result.rows) == 81 * 5
    assert {row.day for row in result.rows} == {"2026-10-25"}
    rain = [row for row in result.rows if row.phenomenon == "rain"]
    assert all(row.value is None and row.level is None for row in rain)  # 6 hours of 24: no daily total is made up
    assert collector.store.runs() == ["2026-10-25T00:00Z"]

    # The next check finds the run stored and reads nothing more.
    asked.clear()
    again = await collector.fetch(force_refresh=True)
    assert again.rows == () and asked == []


async def test_an_older_run_is_read_when_the_newest_is_not_complete(tmp_path: Path) -> None:
    asked: list[str] = []
    collector = _collector(tmp_path, mirror(asked, complete=()))
    collector.now = lambda: datetime(2026, 10, 25, 13, 0, tzinfo=timezone.utc)  # the run of 12 UTC is not out yet
    with pytest.raises(Exception, match="no complete ECMWF run"):
        await collector.fetch(force_refresh=True)
    assert asked == [
        "HEAD 20261025120000-6h-oper-fc.index",
        "HEAD 20261025000000-6h-oper-fc.index",
        "HEAD 20261024120000-6h-oper-fc.index",
    ]


async def test_a_slow_down_of_s3_is_asked_again(tmp_path: Path) -> None:
    asked: list[str] = []
    recorded = mirror(asked)
    slowed: set[str] = set()

    def handler(request: httpx.Request) -> httpx.Response:
        key = f"{request.method} {request.url.path} {request.headers.get('Range', '')}"
        if request.method == "GET" and key not in slowed:  # the first ask of each file or range: "503 Slow Down"
            slowed.add(key)
            return httpx.Response(503, text="<Error><Code>SlowDown</Code></Error>")
        return recorded(request)

    collector = _collector(tmp_path, handler)
    collector.retry_waits_s = (0.0,)
    result = await collector.fetch(force_refresh=True)
    assert len(result.rows) == 81 * 5 and len(slowed) == 2 * (1 + 5)


async def test_a_check_that_fails_half_way_goes_on_from_there_at_the_next_one(tmp_path: Path) -> None:
    asked: list[str] = []
    recorded = mirror(asked)
    throttled = {"6h": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if throttled["6h"] and request.method == "GET" and "-6h-" in request.url.path:
            return httpx.Response(503, text="<Error><Code>SlowDown</Code></Error>")
        return recorded(request)

    collector = _collector(tmp_path, handler)
    collector.retry_waits_s = ()
    with pytest.raises(Exception, match="503"):
        await collector.fetch(force_refresh=True)
    assert collector.store.runs() == []

    throttled["6h"] = False
    asked.clear()
    result = await collector.fetch(force_refresh=True)
    assert len(result.rows) == 81 * 5
    assert not any("-3h-" in a for a in asked)  # lead 3 was kept, not read again
    assert asked.count("GET 20261025000000-6h-oper-fc.index") == 1


async def test_an_answer_that_is_not_grib_is_refused(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200)
        if request.url.path.endswith(".index"):
            return httpx.Response(200, content=(FIXTURES / "20261025000000-3h-oper-fc.index").read_bytes())
        return httpx.Response(200, text="<?xml version='1.0'?><Error><Code>SlowDown</Code></Error>")

    collector = _collector(tmp_path, handler)
    with pytest.raises(Exception, match="not a GRIB2 answer"):
        await collector.fetch(force_refresh=True)
    assert collector.store.runs() == []


def _stored(tmp_path: Path) -> ShadowStore:
    store = ShadowStore(tmp_path / "shadow.db")
    store.save(
        (
            LevelRow("2026-10-24T12:00Z", "2026-10-25", "TR-34", "rain", 5.0, 0),
            LevelRow("2026-10-25T00:00Z", "2026-10-24", "TR-34", "rain", 1.0, 0),  # yesterday: not shown
            LevelRow("2026-10-25T00:00Z", "2026-10-25", "TR-34", "rain", 61.04, 2),
            LevelRow("2026-10-25T00:00Z", "2026-10-25", "TR-34", "wind", None, None),
            LevelRow("2026-10-25T00:00Z", "2026-10-26", "TR-34", "rain", 3.0, 0),
            LevelRow("2026-10-25T00:00Z", "2026-10-25", "TR-06", "rain", 90.0, 3),
        )
    )
    return store


def test_the_view_gives_the_newest_run_of_the_province_from_today_on(tmp_path: Path) -> None:
    answer = prealert_at(_stored(tmp_path), *ISTANBUL, now=NOW)
    assert answer.experimental is True and answer.official is False
    assert answer.notice == NOTICE and "UNOFFICIAL" in answer.notice and "not the warnings" in answer.notice
    assert answer.official_warnings_url == OFFICIAL_WARNINGS_URL
    assert "ECMWF" in answer.attribution and "CC BY 4.0" in answer.attribution
    assert (answer.province_code, answer.run_utc) == ("TR-34", "2026-10-25T00:00Z")
    assert [d.day for d in answer.days] == ["2026-10-25", "2026-10-26"]
    today = answer.days[0]
    assert today.max_level == 2
    assert today.phenomena["rain"].value == 61.0 and today.phenomena["rain"].level_word == "strong"
    assert today.phenomena["rain"].thresholds is not None and len(today.phenomena["rain"].thresholds) == 3
    wind = today.phenomena["wind"]
    assert wind.value is None and wind.level is None and wind.level_word is None  # unknown stays unknown


def test_the_view_before_the_first_run_and_outside_turkey(tmp_path: Path) -> None:
    empty = prealert_at(ShadowStore(tmp_path / "empty.db"), *ISTANBUL, now=NOW)
    assert empty.run_utc is None and empty.days == []
    with pytest.raises(OutsideTurkey):
        prealert_at(ShadowStore(tmp_path / "empty.db"), 48.85, 2.35, now=NOW)


def test_the_route_is_404_on_an_instance_without_the_pilot(world, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "prealert_pilot", None)
    answer = world.client.get("/v1/experimental/prealert", params={"lat": ISTANBUL[0], "lon": ISTANBUL[1]})
    assert answer.status_code == 404


def test_the_route_answers_with_the_pilot(world, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pilot = ShadowPilot(EcmwfOpenDataCollector(_stored(tmp_path)))
    monkeypatch.setattr(main, "prealert_pilot", pilot)
    answer = world.client.get("/v1/experimental/prealert", params={"lat": ISTANBUL[0], "lon": ISTANBUL[1]})
    assert answer.status_code == 200
    body = answer.json()
    assert body["experimental"] is True and body["official"] is False and body["province_code"] == "TR-34"
    assert world.client.get("/v1/experimental/prealert", params={"lat": 48.85, "lon": 2.35}).status_code == 422
