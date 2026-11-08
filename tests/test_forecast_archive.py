"""The archive of the surf forecasts: what the collector reads is kept with its issue time, and nothing more is asked."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app import clock
from app.collectors.surf import SurfForecastCollector, parse_swell, parse_wind
from app.services.forecast_archive import BUCKET_S, MAX_LEAD_S, STEP_S, SurfForecastArchive, rows_of
from app.services.storage_wal import SQLiteWALStorage
from tests.helpers import Provider

FIXTURES = Path(__file__).parent / "fixtures" / "surf"
SWELL = json.loads((FIXTURES / "mfwam-hossegor.json").read_text(encoding="utf-8"))
WIND = json.loads((FIXTURES / "wind-hossegor.json").read_text(encoding="utf-8"))
FIRST_HOUR = 1793145600
NOW = FIRST_HOUR + 10 * 3600 + 600


def test_only_the_three_hourly_steps_within_the_lead_are_kept_and_gaps_stay_null() -> None:
    parsed = parse_swell(SWELL)
    rows = rows_of("swell", {"s": parsed}, NOW)
    assert rows
    for spot, bucket, valid, issued, *values in rows:
        assert spot == "s" and issued == NOW and bucket == NOW // BUCKET_S * BUCKET_S
        assert valid % STEP_S == 0 and 0 <= valid - issued <= MAX_LEAD_S
        assert len(values) == 6
    # The values are the provider's, untouched.
    first = next(r for r in rows if r[2] == FIRST_HOUR + 12 * 3600)
    assert first[4] == parsed["wave_height"][12]


def test_a_forecast_of_the_same_bucket_keeps_the_first_and_fills_the_other_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = SQLiteWALStorage(tmp_path / "t.db")
    archive = SurfForecastArchive(lambda: storage, keep_days=90)
    swell, wind = parse_swell(SWELL), parse_wind(WIND)
    cols = ("wave_height", "wave_period", "wave_direction", "swell_height", "swell_period", "swell_direction")
    storage.archive_surf_forecast(rows_of("swell", {"s": swell}, NOW), cols)
    count = storage.surf_forecast_summary()[0]
    changed = dict(swell, wave_height=[99.0] * len(swell["times"]))
    storage.archive_surf_forecast(rows_of("swell", {"s": changed}, NOW + 3600), cols)  # same bucket: ignored
    assert storage.surf_forecast_summary()[0] == count
    storage.archive_surf_forecast(rows_of("wind", {"s": wind}, NOW), ("wind_speed", "wind_direction", "wind_gusts"))
    assert storage.surf_forecast_summary()[0] == count  # the wind joins the same rows
    with storage._connect() as conn:
        row = conn.execute("SELECT wave_height, wind_speed FROM surf_forecast_archive WHERE valid_utc = ?", (FIRST_HOUR + 12 * 3600,)).fetchone()
    assert row[0] == swell["wave_height"][12] and row[1] == wind["wind_speed_10m"][12]
    assert storage.prune_surf_forecast(NOW + 1) == count
    assert archive.health()["rows"] == 0


@pytest.mark.asyncio
async def test_the_collector_feeds_the_archive_without_one_more_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock, "_wall", lambda: float(NOW))
    storage = SQLiteWALStorage(tmp_path / "t.db")
    archive = SurfForecastArchive(lambda: storage)
    provider = Provider(answer=lambda request: httpx.Response(200, json=SWELL if "marine" in request.url.host else WIND))
    collector = SurfForecastCollector(http_client=provider.client())
    collector.on_fetched = archive.record
    for _ in range(3):
        await collector.fetch_swell("hossegor", 43.6644, -1.4432)
        await collector.fetch_wind("hossegor", 43.6644, -1.4432)
    await archive.settle()
    assert provider.count == 2  # the same two requests as without the archive
    health = archive.health()
    assert health["rows"] > 0 and health["error"] is None and health["newest_issued_utc"] == NOW


@pytest.mark.asyncio
async def test_a_failing_archive_never_fails_a_read(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = Provider(answer=lambda request: httpx.Response(200, json=SWELL))
    collector = SurfForecastCollector(http_client=provider.client())

    def boom(kind: str, answers: dict) -> None:
        raise RuntimeError("disk full")

    collector.on_fetched = boom
    value, _ = await collector.fetch_swell("hossegor", 43.6644, -1.4432)
    assert value["times"]
