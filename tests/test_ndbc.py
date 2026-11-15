"""Tests of NOAA NDBC wave buoy collector and its integration with buoys service and route."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app import clock
from app.collectors.base import CollectorUnavailableError
from app.collectors.ndbc import (
    LATEST_OBS_URL,
    STATIONS_TTL_SECONDS,
    TTL_SECONDS,
    NdbcCollector,
    parse_active_stations,
    parse_latest_obs,
)
from app.main import aggregator, app
from app.services import buoys
from tests.conftest import World
from tests.helpers import SimClock
from tests.test_candhis import HOSSEGOR, candhis_host, collector_with

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ndbc"
OBS_BYTES = (FIXTURES / "latest_obs-2026-11-15.txt").read_bytes()
OBS_TEXT = OBS_BYTES.decode("latin-1")
XML_BYTES = (FIXTURES / "activestations-2026-11-15.xml").read_bytes()

# 2026-11-15 16:25 UTC: recorded fixtures were fetched at 16:25:16 GMT (rows are 15:00-15:40 UTC -> 45-85 min old).
NOW = int(datetime(2026, 11, 15, 16, 25, tzinfo=timezone.utc).timestamp())
BRETON_SPOT = "tremenac-h-greve-blanche"  # 48.55, -4.58 (~199 km from Sevenstones Lightship 62107)


def ndbc_host(
    *,
    obs_bytes: bytes = OBS_BYTES,
    xml_bytes: bytes = XML_BYTES,
    fail_obs: bool = False,
    fail_xml: bool = False,
    etag: str = '"18601-test"',
    last_modified: str = "Sun, 15 Nov 2026 16:20:38 GMT",
    calls: list[str] | None = None,
) -> httpx.AsyncClient:
    """Mock HTTP client answering NDBC ``latest_obs.txt`` and ``activestations.xml`` with conditional 304 support."""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request.url.path)
        if request.url.path.endswith(".xml"):
            if fail_xml:
                return httpx.Response(503, text="Service Unavailable")
            if request.headers.get("If-None-Match") == '"xml-test"':
                return httpx.Response(304, headers={"ETag": '"xml-test"'})
            return httpx.Response(200, content=xml_bytes, headers={"ETag": '"xml-test"', "Last-Modified": last_modified})
        if fail_obs:
            return httpx.Response(503, text="Service Unavailable")
        if request.headers.get("If-None-Match") == etag or request.headers.get("If-Modified-Since") == last_modified:
            return httpx.Response(304, headers={"ETag": etag, "Last-Modified": last_modified})
        return httpx.Response(200, content=obs_bytes, headers={"ETag": etag, "Last-Modified": last_modified})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_parse_recorded_fixtures_reads_wave_stations_without_inventing_missing_fields() -> None:
    meta = parse_active_stations(XML_BYTES)
    latest = parse_latest_obs(OBS_TEXT, station_meta=meta)

    assert latest.total_rows == 837
    assert latest.wave_rows == 194
    assert latest.skipped_rows == 0

    sevenstones = latest.stations["62107"]
    assert sevenstones.name == "Sevenstones Lightship"
    assert sevenstones.station_type == "fixed"
    assert sevenstones.owner == "UK Met Office"
    r62107 = latest.readings["62107"]
    assert r62107.height_m == 1.4
    assert r62107.height_kind == "H1/3"
    assert r62107.period_s == 6.0
    assert r62107.period_kind == "APD"
    assert r62107.peak_period_s is None
    assert r62107.direction_deg is None
    assert r62107.hmax_m is None
    assert r62107.spread_deg is None
    assert r62107.sea_temp_c == 13.5

    r44025 = latest.readings["44025"]
    assert latest.stations["44025"].name == "LONG ISLAND - 30 NM South of Islip, NY"
    assert r44025.height_m == 1.1
    assert r44025.peak_period_s == 5.0
    assert r44025.period_s == 4.0
    assert r44025.period_kind == "APD"
    assert r44025.direction_deg == 296.0
    assert r44025.sea_temp_c == 15.0

    # Weather-only stations (WVHT = MM) are ignored cleanly, not counted as malformed.
    assert "46042" not in latest.readings
    assert "41009" not in latest.readings

    # Calm wave height (0.0 m) is valid; KMA sentinel DPD=0 is kept unknown (None); omitted station metadata falls back to ID.
    assert latest.readings["46181"].height_m == 0.0
    assert latest.readings["22101"].peak_period_s is None
    assert latest.readings["22101"].sea_temp_c == 17.1
    assert parse_latest_obs(OBS_TEXT, station_meta=None).stations["62107"].name == "62107"


def test_parser_rejects_bad_headers_and_skips_malformed_rows_or_implausible_values() -> None:
    with pytest.raises(ValueError, match="empty"):
        parse_latest_obs("")
    with pytest.raises(ValueError, match="#STN"):
        parse_latest_obs("<html>error</html>")
    with pytest.raises(ValueError, match="missing required columns"):
        parse_latest_obs("#STN LAT LON\n44025 40.0 -73.0\n")
    with pytest.raises(ValueError, match="valid XML"):
        parse_active_stations(b"<stations>")

    sample = (
        "#STN LAT LON YYYY MM DD hh mm WVHT DPD APD MWD WTMP\n"
        "#text deg deg yr mo day hr mn m sec sec degT degC\n"
        "bad1 95.0 -10.0 2026 11 15 15 00 1.2 8 6.0 270 14.0\n"  # lat > 90 -> skipped
        "bad2 45.0 -10.0 2026 02 30 15 00 1.2 8 6.0 270 14.0\n"  # invalid date -> skipped
        "bad3 45.0 -10.0 2026 11 15 15 00 -0.5 8 6.0 270 14.0\n"  # negative WVHT -> skipped
        "short 45.0\n"  # truncated -> skipped
        "ok1 45.0 -10.0 2026 11 15 15 00 2.0 0 0 361 99.0\n"  # out-of-range optional fields -> None
    )
    parsed = parse_latest_obs(sample)
    assert parsed.total_rows == 5
    assert parsed.wave_rows == 1
    assert parsed.skipped_rows == 4
    r = parsed.readings["ok1"]
    assert r.height_m == 2.0
    assert r.peak_period_s is None
    assert r.period_s is None
    assert r.period_kind is None
    assert r.direction_deg is None
    assert r.sea_temp_c is None


@pytest.mark.asyncio
async def test_collector_uses_ttl_cache_and_conditional_304_requests(sim_clock: SimClock) -> None:
    calls: list[str] = []
    client = ndbc_host(calls=calls)
    collector = NdbcCollector(http_client=client)

    first = await collector.fetch_latest()
    assert first.stale is False and first.wave_rows == 194
    assert calls == ["/data/latest_obs/latest_obs.txt", "/activestations.xml"]

    # Within TTL (600 s): zero upstream HTTP calls.
    sim_clock.advance(TTL_SECONDS - 10)
    second = await collector.fetch_latest()
    assert second.wave_rows == 194
    assert len(calls) == 2

    # After TTL: conditional GET on latest_obs.txt -> 304 Not Modified, no second activestations.xml fetch.
    sim_clock.advance(20)
    third = await collector.fetch_latest()
    assert third.wave_rows == 194 and third.stale is False
    assert calls == [
        "/data/latest_obs/latest_obs.txt",
        "/activestations.xml",
        "/data/latest_obs/latest_obs.txt",
    ]

    # Over 24 h later, if a fresh 200 arrives, activestations.xml revalidates once.
    calls.clear()
    collector_2 = NdbcCollector(
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: (
                    calls.append(r.url.path)
                    or httpx.Response(
                        304 if r.url.path.endswith(".xml") and "If-None-Match" in r.headers else 200,
                        content=b"" if r.url.path.endswith(".xml") and "If-None-Match" in r.headers else (XML_BYTES if r.url.path.endswith(".xml") else OBS_BYTES),
                        headers={"ETag": f'"{len(calls)}"'},
                    )
                )
            )
        )
    )
    await collector_2.fetch_latest()
    sim_clock.advance(STATIONS_TTL_SECONDS + 1)
    await collector_2.fetch_latest()
    assert calls.count("/activestations.xml") == 2


@pytest.mark.asyncio
async def test_collector_gracefully_handles_station_xml_failure_and_stale_fallback(sim_clock: SimClock) -> None:
    # When activestations.xml returns 503, observations still load with station IDs as names.
    collector = NdbcCollector(http_client=ndbc_host(fail_xml=True))
    latest = await collector.fetch_latest()
    assert latest.stations["62107"].name == "62107"
    assert latest.readings["62107"].height_m == 1.4

    # When latest_obs.txt fails later, cached data is returned flagged stale.
    collector._external_client = ndbc_host(fail_obs=True)  # noqa: SLF001
    sim_clock.advance(TTL_SECONDS + 5)
    stale = await collector.fetch_latest()
    assert stale.stale is True and stale.readings["62107"].height_m == 1.4

    # With empty cache and failing upstream, CollectorUnavailableError is raised.
    broken = NdbcCollector(http_client=ndbc_host(fail_obs=True))
    with pytest.raises(CollectorUnavailableError):
        await broken.fetch_latest()


@pytest.mark.asyncio
async def test_collector_rejects_unsolicited_304() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(304)))
    collector = NdbcCollector(http_client=client)
    with pytest.raises(CollectorUnavailableError):
        await collector.fetch_latest()


def test_spot_reading_selects_ndbc_when_candhis_is_absent_and_prefers_closer_buoy(monkeypatch: pytest.MonkeyPatch) -> None:
    ndbc = NdbcCollector(http_client=ndbc_host())
    reading = asyncio.run(buoys.spot_reading(None, BRETON_SPOT, NOW, ndbc=ndbc))
    assert reading.status == "ok"
    assert reading.buoy is not None
    assert reading.buoy.id == "62107"
    assert reading.buoy.name == "Sevenstones Lightship"
    assert reading.buoy.source == "ndbc"
    assert reading.comparable is False
    assert reading.licence == buoys.NDBC_LICENCE
    assert "National Data Buoy Center" in reading.attribution
    assert "Not endorsed by NOAA or the NWS" in reading.attribution
    assert reading.measure is not None
    assert reading.measure.height_m == 1.4
    assert reading.measure.height_kind == "H1/3"
    assert reading.measure.period_s == 6.0
    assert reading.measure.period_kind == "APD"
    assert reading.measure.peak_period_s is None
    assert reading.measure.hmax_m is None
    assert reading.measure.spread_deg is None

    # When CANDHIS is also configured near Hossegor (~11 km vs NDBC ~790 km), CANDHIS wins on distance.
    candhis = collector_with(candhis_host([]))
    candhis_now = int(datetime(2026, 11, 9, 19, 8, tzinfo=timezone.utc).timestamp())
    both = asyncio.run(buoys.spot_reading(candhis, HOSSEGOR, candhis_now, ndbc=ndbc))
    assert both.status == "ok" and both.buoy is not None
    assert both.buoy.id == "06402" and both.buoy.source == "candhis"
    assert "Candhis" in both.attribution

    # Route integration with forecast comparison on an NDBC buoy reading.
    monkeypatch.setattr(clock, "_wall", lambda: NOW)
    monkeypatch.setattr(aggregator, "candhis", None)
    monkeypatch.setattr(aggregator, "ndbc", NdbcCollector(http_client=ndbc_host()))
    measured_hour = int(datetime(2026, 11, 15, 15, 0, tzinfo=timezone.utc).timestamp())
    swell_data = {
        "times": [measured_hour - 3600, measured_hour, measured_hour + 3600],
        "wave_height": [1.5, 1.6, 1.7],
        "wave_period": [7.0, 6.5, 6.0],
        "swell_wave_height": [1.2, 1.3, 1.4],
        "swell_wave_period": [10.0, 11.0, 10.5],
        "swell_wave_direction": [280, 285, 290],
    }
    monkeypatch.setattr(aggregator.surf, "fetch_swell", AsyncMock(return_value=(swell_data, False)))
    resp = TestClient(app).get(f"/v1/surf/spots/{BRETON_SPOT}/buoy", params={"compare": "true"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["buoy"]["id"] == "62107" and body["buoy"]["source"] == "ndbc"
    assert body["comparison"]["status"] == "ok" and body["comparison"]["height_diff_m"] == -0.2
    assert "National Data Buoy Center" in body["attribution"] and "Copernicus" in body["attribution"]


def test_ndbc_is_registered_in_providers_and_health(world: World) -> None:
    health = world.client.get("/v1/health").json()
    provider_ids = {p["provider_id"] for p in health["providers"]}
    assert "noaa_ndbc" in provider_ids
    assert "noaa_ndbc" not in {s["provider_id"] for s in health["disabled_sources"]}
    assert LATEST_OBS_URL.startswith("https://www.ndbc.noaa.gov/")
