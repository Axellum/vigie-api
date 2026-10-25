"""The shadow pilot of the pre-alert for Turkey (ADR 0011): which run is read, what is asked of NOMADS, what is stored."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

pytest.importorskip("numpy")
pytest.importorskip("eccodes")

from app.prealert.shadow import (  # noqa: E402
    GfsNomadsCollector,
    ShadowStore,
    candidate_runs,
    enabled,
    filter_params,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "gfs_nomads"
NOW = datetime(2026, 10, 25, 9, 30, tzinfo=timezone.utc)


def test_the_pilot_is_off_unless_asked() -> None:
    assert not enabled({})
    assert not enabled({"VIGIE_PREALERT_TR": "0"})
    assert enabled({"VIGIE_PREALERT_TR": "1"}) and enabled({"VIGIE_PREALERT_TR": " On "})


def test_candidate_runs_newest_first_over_30_hours() -> None:
    runs = candidate_runs(NOW)
    assert runs[0] == datetime(2026, 10, 25, 6, tzinfo=timezone.utc)
    assert runs[-1] == datetime(2026, 10, 24, 6, tzinfo=timezone.utc)
    assert len(runs) == 5


def test_filter_params_cut_to_turkey_and_six_fields() -> None:
    params = filter_params(datetime(2026, 10, 25, 0, tzinfo=timezone.utc), 6)
    assert params["dir"] == "/gfs.20261025/00/atmos" and params["file"] == "gfs.t00z.pgrb2.0p25.f006"
    assert {k for k in params if k.startswith("var_")} == {"var_APCP", "var_GUST", "var_TMAX", "var_TMIN", "var_TMP", "var_CSNOW"}
    assert (params["toplat"], params["bottomlat"], params["leftlon"], params["rightlon"]) == ("42.5", "35.5", "25.5", "45")


def _collector(tmp_path: Path, handler) -> GfsNomadsCollector:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    collector = GfsNomadsCollector(ShadowStore(tmp_path / "shadow.db"), http_client=client)
    collector.pause_s = 0.0
    collector.last_lead_h = 6  # the two recorded leads
    collector.now = lambda: NOW
    return collector


async def test_reads_the_newest_complete_run_and_stores_its_levels(tmp_path: Path) -> None:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(f"{request.method} {request.url.path} {request.url.params.get('file', '')}")
        if request.method == "HEAD":
            # The run of 06 UTC is not complete yet; the run of 00 UTC is.
            return httpx.Response(200 if "/gfs.20261025/00/" in request.url.path else 404)
        lead = request.url.params["file"][-3:]
        return httpx.Response(200, content=(FIXTURES / f"gfs.t00z.20261025.f{lead}.grib2").read_bytes())

    collector = _collector(tmp_path, handler)
    result = await collector.fetch(force_refresh=True)
    assert result.run == datetime(2026, 10, 25, 0, tzinfo=timezone.utc)
    assert asked[:2] == [
        "HEAD /pub/data/nccf/com/gfs/prod/gfs.20261025/06/atmos/gfs.t06z.pgrb2.0p25.f006.idx ",
        "HEAD /pub/data/nccf/com/gfs/prod/gfs.20261025/00/atmos/gfs.t00z.pgrb2.0p25.f006.idx ",
    ]
    assert asked[2:] == [
        "GET /cgi-bin/filter_gfs_0p25.pl gfs.t00z.pgrb2.0p25.f003",
        "GET /cgi-bin/filter_gfs_0p25.pl gfs.t00z.pgrb2.0p25.f006",
    ]
    # 81 provinces x 5 phenomena for the one local day the two leads cover.
    assert len(result.rows) == 81 * 5
    assert {row.day for row in result.rows} == {"2026-10-25"}
    rain = [row for row in result.rows if row.phenomenon == "rain"]
    assert all(row.value is None and row.level is None for row in rain)  # 6 hours of 24: no daily total is made up
    wind = [row for row in result.rows if row.phenomenon == "wind"]
    assert all(row.value is not None and row.level is not None for row in wind)
    assert collector.store.runs() == ["2026-10-25T00:00Z"]

    # The next check finds the run stored and reads nothing more.
    asked.clear()
    again = await collector.fetch(force_refresh=True)
    assert again.rows == () and asked == ["HEAD /pub/data/nccf/com/gfs/prod/gfs.20261025/06/atmos/gfs.t06z.pgrb2.0p25.f006.idx "]


async def test_an_answer_that_is_not_grib_is_refused(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200)
        return httpx.Response(200, text="<html>The data you requested is not available</html>")

    collector = _collector(tmp_path, handler)
    with pytest.raises(Exception, match="not a GRIB2 answer"):
        await collector.fetch(force_refresh=True)
    assert collector.store.runs() == []
