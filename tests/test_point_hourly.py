"""Unit and API tests for hourly weather, 24h pressure history and hourly air quality on ``GET /v1/point``.

Zero network calls: every provider is mocked with ``httpx.MockTransport``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.collectors.flood_air import AIR_HOURLY_VARIABLES, OpenMeteoAirQualityCollector
from app.collectors.meteofrance_weather import (
    HOURLY_VARIABLES as MF_HOURLY_VARIABLES,
)
from app.collectors.meteofrance_weather import (
    MeteoFranceWeatherCollector,
    merge_france_bundle,
)
from app.collectors.weather import MetNorwayCollector, OpenMeteoCollector, split_hourly_series
from app.models.schemas import DeviceType, HourlyWeatherPoint
from app.services.embedded_frame import verify_embedded_frame_crc
from tests import samples
from tests.conftest import World
from tests.helpers import SNAPSHOT_BUDGET_BYTES, Provider, SimClock, forecast_provider, make_service
from tests.test_meteofrance_weather import VANNES, gateway_daily, gateway_now, now_epoch

FRAME_BUDGET_BYTES = 8192
BASE_UTC = int(SimClock.EPOCH) // 3600 * 3600  # aligned on the simulation clock's UTC hour


def _local_iso(epoch_utc: int, offset_s: int) -> str:
    return (datetime.fromtimestamp(epoch_utc, tz=timezone.utc) + timedelta(seconds=offset_s)).strftime("%Y-%m-%dT%H:%M")


def _open_meteo_hourly_payload(
    *,
    base_utc: int = BASE_UTC,
    past_hours: int = 24,
    forecast_hours: int = 72,
    offset_s: int = 3600,
    include_uv_and_prob: bool = True,
) -> dict[str, Any]:
    total = past_hours + forecast_hours
    epochs = [base_utc + (i - past_hours) * 3600 for i in range(total)]
    hourly: dict[str, Any] = {
        "time": [_local_iso(ep, offset_s) for ep in epochs],
        "temperature_2m": [round(10.0 + (i % 12) * 0.5, 1) for i in range(total)],
        "apparent_temperature": [round(8.5 + (i % 12) * 0.5, 1) for i in range(total)],
        "dew_point_2m": [round(6.0 + (i % 6) * 0.3, 1) for i in range(total)],
        "precipitation": [0.4 if i % 6 == 0 else 0.0 for i in range(total)],
        "wind_speed_10m": [18.0 + (i % 10) for i in range(total)],
        "wind_gusts_10m": [32.0 + (i % 10) for i in range(total)],
        "wind_direction_10m": [(220 + i * 5) % 360 for i in range(total)],
        "cloud_cover": [(i * 7) % 101 for i in range(total)],
        "cloud_cover_low": [(i * 5) % 101 for i in range(total)],
        "cloud_cover_mid": [(i * 3) % 101 for i in range(total)],
        "cloud_cover_high": [(i * 2) % 101 for i in range(total)],
        "pressure_msl": [round(1005.0 + i * 0.15, 1) for i in range(total)],
        "shortwave_radiation": [240.0 if 6 <= (i % 24) <= 18 else 0.0 for i in range(total)],
        "weather_code": [61 if i % 6 == 0 else 2 for i in range(total)],
        "is_day": [1 if 6 <= (i % 24) <= 18 else 0 for i in range(total)],
    }
    if include_uv_and_prob:
        hourly["precipitation_probability"] = [(i * 10) % 100 for i in range(total)]
        hourly["uv_index"] = [3.2 if 8 <= (i % 24) <= 16 else 0.0 for i in range(total)]
    return {"utc_offset_seconds": offset_s, "hourly": hourly}


def _air_hourly_payload(base_utc: int = BASE_UTC, hours: int = 96, current_offset_hours: int = 12) -> dict[str, Any]:
    epochs = [base_utc + (i - current_offset_hours) * 3600 for i in range(hours)]
    times = [datetime.fromtimestamp(ep, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M") for ep in epochs]
    return {
        "utc_offset_seconds": 0,
        "current": {
            "time": times[current_offset_hours],
            "interval": 3600,
            "us_aqi": 44,
            "uv_index": 2.6,
        },
        "hourly": {
            "time": times,
            "pm2_5": [round(6.0 + (i % 10) * 0.4, 1) for i in range(hours)],
            "pm10": [round(12.0 + (i % 10) * 0.7, 1) for i in range(hours)],
            "ozone": [round(55.0 + (i % 12) * 1.5, 1) for i in range(hours)],
            "nitrogen_dioxide": [round(9.0 + (i % 8) * 0.5, 1) for i in range(hours)],
            "dust": [round(1.5 + (i % 5) * 0.2, 1) for i in range(hours)],
            "european_aqi": [25 + (i % 20) for i in range(hours)],
        },
    }


def _open_meteo_router(hourly_payload: dict[str, Any] | None = None, *, fail_hourly: bool = False) -> Provider:
    base = samples.open_meteo_payload()
    h_payload = hourly_payload if hourly_payload is not None else _open_meteo_hourly_payload()

    def answer(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if "past_hours" in params:
            if fail_hourly:
                return httpx.Response(503)
            return httpx.Response(200, json=h_payload)
        if "daily" in params:
            return httpx.Response(200, json={"utc_offset_seconds": base["utc_offset_seconds"], "daily": base["daily"]})
        return httpx.Response(
            200,
            json={
                "utc_offset_seconds": base["utc_offset_seconds"],
                "current": base["current"],
                "minutely_15": base["minutely_15"],
            },
        )

    return Provider(answer=answer)


# ============================================================================
# Open-Meteo hourly weather & 24h pressure history
# ============================================================================


def test_open_meteo_hourly_request_params_and_weight() -> None:
    collector = OpenMeteoCollector()
    params = OpenMeteoCollector.request_params("hourly", 48.85, 2.35)
    assert len(params["hourly"].split(",")) == 17
    assert params["forecast_hours"] == "72"
    assert params["past_hours"] == "24"
    assert "current" not in params and "daily" not in params and "minutely_15" not in params
    assert collector.call_weight(params) == 1.7


def test_open_meteo_parse_hourly_and_split_at_now() -> None:
    payload = _open_meteo_hourly_payload(base_utc=BASE_UTC, past_hours=24, forecast_hours=72, offset_s=7200)
    # Append an all-null trailing row (past model horizon) that must be skipped, not fabricated.
    for key, arr in payload["hourly"].items():
        arr.append(_local_iso(BASE_UTC + 73 * 3600, 7200) if key == "time" else None)

    parsed = OpenMeteoCollector.parse_hourly(payload)
    assert parsed["utc_offset_s"] == 7200
    assert len(parsed["hourly_all"]) == 96

    hourly, history = split_hourly_series(parsed["hourly_all"], reference_utc=BASE_UTC + 1800)
    assert hourly is not None and history is not None
    assert len(history) == 24
    assert len(hourly) == 72
    assert history[0].time_utc == BASE_UTC - 24 * 3600
    assert history[-1].time_utc == BASE_UTC - 3600
    assert hourly[0].time_utc == BASE_UTC
    assert hourly[-1].time_utc == BASE_UTC + 71 * 3600
    assert history[-1].time_utc < hourly[0].time_utc  # no boundary duplicate
    first = hourly[0]
    assert first.temp_c is not None and first.feels_like_c is not None and first.dew_point_c is not None
    assert first.precipitation_probability_pct is not None and first.uv_index is not None
    assert first.cloud_low_pct is not None and first.cloud_mid_pct is not None and first.cloud_high_pct is not None
    assert first.shortwave_wm2 is not None and isinstance(first.is_day, bool)

    # Three hours later against the same cached series: "now" advances cleanly with no stale forecast hours.
    later_hourly, later_history = split_hourly_series(parsed["hourly_all"], reference_utc=BASE_UTC + 3 * 3600 + 600)
    assert later_hourly is not None and later_history is not None
    assert later_hourly[0].time_utc == BASE_UTC + 3 * 3600
    assert len(later_hourly) == 69
    assert later_history[-1].time_utc == BASE_UTC + 2 * 3600
    assert len(later_history) == 24


def test_split_hourly_series_omits_null_pressure_from_history() -> None:
    points = [
        HourlyWeatherPoint(time_utc=BASE_UTC - 3600, temp_c=11.0, pressure_hpa=None),
        HourlyWeatherPoint(time_utc=BASE_UTC, temp_c=12.0, pressure_hpa=1012.5),
    ]
    hourly, history = split_hourly_series(points, reference_utc=BASE_UTC)
    assert hourly is not None and len(hourly) == 1
    assert history is None


@pytest.mark.asyncio
async def test_open_meteo_hourly_world_point_compact_vs_full(sim_clock: SimClock, tmp_path: Path) -> None:
    om_provider = _open_meteo_router()
    aqi_provider = Provider(_air_hourly_payload())
    service = make_service(
        tmp_path / "world.db",
        open_meteo=OpenMeteoCollector(http_client=om_provider.client()),
        air_quality=OpenMeteoAirQualityCollector(http_client=aqi_provider.client()),
    )

    # Compact profile stays lean and never sends the 17-variable hourly request.
    compact_point = await service.build_point_response(40.71, -74.01, compact=True)
    assert compact_point.hourly is None
    assert compact_point.pressure_history is None
    assert compact_point.air_hourly is None
    assert len(om_provider.with_param("past_hours")) == 0

    # Full profile populates hourly (72h), pressure_history (24h), and air_hourly (72h).
    full_point = await service.build_point_response(40.71, -74.01, device_type=DeviceType.WEB, compact=False)
    assert full_point.hourly is not None and len(full_point.hourly) == 72
    assert full_point.pressure_history is not None and len(full_point.pressure_history) == 24
    assert full_point.air_hourly is not None and len(full_point.air_hourly) == 72
    assert full_point.hourly[0].time_utc == BASE_UTC
    assert full_point.pressure_history[-1].time_utc == BASE_UTC - 3600
    assert full_point.air_hourly[0].time_utc == BASE_UTC
    assert len(om_provider.with_param("past_hours")) == 1

    # Cached for 6 hours: polling again 3 hours later does not re-fetch om-hourly.
    sim_clock.advance(3 * 3600.0)
    cached_point = await service.build_point_response(40.71, -74.01, device_type=DeviceType.WEB, compact=False)
    assert cached_point.hourly is not None and len(om_provider.with_param("past_hours")) == 1


@pytest.mark.asyncio
async def test_open_meteo_hourly_failure_does_not_break_point(sim_clock: SimClock, tmp_path: Path) -> None:
    om_provider = _open_meteo_router(fail_hourly=True)
    service = make_service(tmp_path / "fail_hourly.db", open_meteo=OpenMeteoCollector(http_client=om_provider.client()))
    point = await service.build_point_response(40.71, -74.01, compact=False)
    assert point.current is not None and point.current.temp_c == 18.2
    assert point.hourly is None
    assert point.pressure_history is None


@pytest.mark.asyncio
async def test_open_meteo_hourly_uses_self_hosted_url_first(sim_clock: SimClock, monkeypatch: pytest.MonkeyPatch) -> None:
    hosts: list[str] = []
    payload = _open_meteo_hourly_payload(past_hours=2, forecast_hours=4)

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(200, json=payload)

    monkeypatch.setenv("VIGIE_OPEN_METEO_FORECAST_URL", "http://open-meteo.internal:8081/v1/forecast")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        collector = OpenMeteoCollector(http_client=client)
        result = await collector.fetch_hourly(40.71, -74.01)
        assert len(result["hourly_all"]) == 6
        assert hosts == ["open-meteo.internal"]
        assert collector.usage.calls(sim_clock.monotonic()) == 0
    finally:
        await client.aclose()


# ============================================================================
# France gateway (Météo-France AROME / ARPEGE)
# ============================================================================


@pytest.mark.asyncio
async def test_france_gateway_populates_hourly_and_pressure_history_without_fabricating_uv_or_prob(
    sim_clock: SimClock, tmp_path: Path
) -> None:
    mf_hourly = _open_meteo_hourly_payload(base_utc=BASE_UTC, past_hours=24, forecast_hours=72, offset_s=3600, include_uv_and_prob=False)
    daily_with_hourly = {**gateway_daily(), "hourly": mf_hourly["hourly"]}

    def gw_answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=gateway_now() if "current" in request.url.params else daily_with_hourly)

    gateway = Provider(answer=gw_answer)
    public = forecast_provider()
    service = make_service(
        tmp_path / "fr.db",
        open_meteo=OpenMeteoCollector(http_client=public.client()),
        meteofrance_weather=MeteoFranceWeatherCollector("http://gateway.test", http_client=gateway.client()),
        air_quality=OpenMeteoAirQualityCollector(http_client=Provider(_air_hourly_payload()).client()),
    )

    point = await service.build_point_response(*VANNES, device_type=DeviceType.WEB, compact=False)
    assert point.weather_provider == "Météo-France (AROME, ARPEGE)"
    assert point.hourly is not None and len(point.hourly) == 72
    assert point.pressure_history is not None and len(point.pressure_history) == 24
    assert point.air_hourly is not None and len(point.air_hourly) == 72
    # Météo-France models do not provide UV index or precipitation probability: stay null.
    assert all(h.uv_index is None and h.precipitation_probability_pct is None for h in point.hourly)
    assert all(h.temp_c is not None and h.pressure_hpa is not None and h.shortwave_wm2 is not None for h in point.hourly)
    # Gateway received only 2 requests (now + daily/hourly); public Open-Meteo was NOT asked for om-hourly!
    assert gateway.count == 2
    assert len(public.with_param("past_hours")) == 0
    daily_req = next(r for r in gateway.requests if "daily" in r.url.params)
    assert daily_req.url.params["hourly"] == MF_HOURLY_VARIABLES
    assert daily_req.url.params["forecast_hours"] == "72"
    assert daily_req.url.params["past_hours"] == "24"
    assert "uv_index" not in daily_req.url.params["hourly"] and "probability" not in daily_req.url.params["hourly"]


def test_merge_france_bundle_keeps_hourly_all_optional() -> None:
    now = OpenMeteoCollector.parse_now(gateway_now())
    days = [d for d in OpenMeteoCollector.parse_daily(gateway_daily())["daily_10d"] if d.tmax is not None]
    bundle = merge_france_bundle(
        {"utc_offset_s": 3600, "current": now["current"], "rain_next_60m": now["rain_next_60m"], "daily": days},
        None,
        None,
        *VANNES,
        now_epoch(),
    )
    assert bundle["hourly_all"] == []


# ============================================================================
# MET Norway fallback (honest nulls)
# ============================================================================


@pytest.mark.asyncio
async def test_met_norway_fallback_populates_available_hourly_fields_and_leaves_unknowns_null(
    sim_clock: SimClock, tmp_path: Path
) -> None:
    metno_payload = {
        "properties": {
            "timeseries": [
                {
                    "time": datetime.fromtimestamp(BASE_UTC, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "data": {
                        "instant": {
                            "details": {
                                "air_temperature": 14.8,
                                "relative_humidity": 81.0,
                                "air_pressure_at_sea_level": 1009.5,
                                "wind_speed": 6.0,
                                "wind_from_direction": 255.0,
                                "cloud_area_fraction": 75.4,
                            }
                        },
                        "next_1_hours": {
                            "summary": {"symbol_code": "lightrain_day"},
                            "details": {"precipitation_amount": 1.6},
                        },
                    },
                },
                {
                    "time": datetime.fromtimestamp(BASE_UTC + 3600, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "data": {
                        "instant": {
                            "details": {
                                "air_temperature": 12.0,
                                "air_pressure_at_sea_level": 1010.2,
                                "wind_speed": 5.0,
                                "wind_from_direction": 260.0,
                                "cloud_area_fraction": 30.0,
                            }
                        },
                        "next_1_hours": {
                            "summary": {"symbol_code": "partlycloudy_night"},
                            "details": {"precipitation_amount": 0.0},
                        },
                    },
                },
                {
                    "time": datetime.fromtimestamp(BASE_UTC + 7200, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "data": {
                        "instant": {"details": {"air_temperature": 10.5, "wind_speed": 4.0}},
                        # Only next_6_hours: hourly precipitation_mm and weather_code must stay None.
                        "next_6_hours": {
                            "summary": {"symbol_code": "rain"},
                            "details": {"precipitation_amount": 4.2},
                        },
                    },
                },
            ]
        }
    }
    failing_om = Provider(answer=lambda _req: httpx.Response(503))
    metno = Provider(metno_payload)
    service = make_service(
        tmp_path / "metno.db",
        open_meteo=OpenMeteoCollector(http_client=failing_om.client()),
        met_norway=MetNorwayCollector(http_client=metno.client()),
    )

    point = await service.build_point_response(59.91, 10.75, compact=False)
    assert point.fallback_used is True
    assert point.pressure_history is None
    assert point.hourly is not None and len(point.hourly) == 3

    h0, h1, h2 = point.hourly
    assert (h0.temp_c, h0.precipitation_mm, h0.wind_kmh, h0.wind_deg, h0.cloud_cover_pct, h0.pressure_hpa) == (
        14.8,
        1.6,
        21.6,
        255,
        75,
        1009.5,
    )
    assert h0.weather_code == 61 and h0.is_day is True
    assert h1.weather_code == 2 and h1.is_day is False
    # 6-hour aggregate must never be copied into an hourly precipitation slot.
    assert h2.precipitation_mm is None and h2.weather_code is None and h2.is_day is None
    for h in point.hourly:
        assert h.feels_like_c is None
        assert h.dew_point_c is None
        assert h.precipitation_probability_pct is None
        assert h.wind_gust_kmh is None
        assert h.cloud_low_pct is None and h.cloud_mid_pct is None and h.cloud_high_pct is None
        assert h.uv_index is None and h.shortwave_wm2 is None


# ============================================================================
# Air quality hourly (CAMS via Open-Meteo)
# ============================================================================


def test_air_quality_reads_current_from_hourly_and_builds_72h_series() -> None:
    payload = _air_hourly_payload(base_utc=BASE_UTC, hours=96, current_offset_hours=12)
    status = OpenMeteoAirQualityCollector.parse_response(payload)

    # Current values absent from ``current`` are picked from ``hourly`` at ``current.time`` (index 12).
    assert status.us_aqi == 44 and status.uv_index == 2.6
    assert status.european_aqi == payload["hourly"]["european_aqi"][12]
    assert status.pm2_5_ugm3 == payload["hourly"]["pm2_5"][12]
    assert status.pm10_ugm3 == payload["hourly"]["pm10"][12]
    assert status.dust_ugm3 == payload["hourly"]["dust"][12]

    series = status.hourly_series
    assert series is not None and len(series) == 72
    assert series[0].time_utc == BASE_UTC
    assert series[0].ozone == payload["hourly"]["ozone"][12]
    assert series[0].nitrogen_dioxide == payload["hourly"]["nitrogen_dioxide"][12]
    assert series[-1].time_utc == BASE_UTC + 71 * 3600

    # Private hourly series is not serialized inside AirQualityStatus JSON (keeps compact profile untouched).
    dumped = status.model_dump()
    assert "hourly_series" not in dumped and "_hourly" not in dumped


@pytest.mark.asyncio
async def test_air_quality_stale_cache_preserves_hourly_series(sim_clock: SimClock) -> None:
    fail = False
    payload = _air_hourly_payload()

    def answer(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503) if fail else httpx.Response(200, json=payload)

    collector = OpenMeteoAirQualityCollector(http_client=Provider(answer=answer).client())
    fresh = await collector.fetch_air_quality(48.85, 2.35)
    assert fresh.hourly_series is not None and len(fresh.hourly_series) == 72

    fail = True
    sim_clock.advance(3605.0)
    stale = await collector.fetch_air_quality(48.85, 2.35)
    assert stale.stale is True
    assert stale.hourly_series is not None and len(stale.hourly_series) == 72


def test_air_quality_request_params_include_hourly_variables() -> None:
    outside = OpenMeteoAirQualityCollector.request_params(40.71, -74.01)
    inside = OpenMeteoAirQualityCollector.request_params(48.85, 2.35)
    assert outside["current"] == "us_aqi,uv_index"
    assert outside["hourly"].split(",") == list(AIR_HOURLY_VARIABLES)
    assert inside["hourly"].split(",")[: len(AIR_HOURLY_VARIABLES)] == list(AIR_HOURLY_VARIABLES)
    assert OpenMeteoAirQualityCollector().call_weight(outside) == 1.0
    assert OpenMeteoAirQualityCollector().call_weight(inside) == 1.4


# ============================================================================
# Compact /v1/snapshot and /v1/embedded/frame remain untouched and under budget
# ============================================================================


def test_compact_point_snapshot_and_embedded_frame_stay_under_budget(world: World) -> None:
    point_compact = world.client.get("/v1/point?lat=47.75&lon=-3.36&compact=true").json()
    assert point_compact["hourly"] is None
    assert point_compact["pressure_history"] is None
    assert point_compact["air_hourly"] is None
    assert len(json.dumps(point_compact, separators=(",", ":")).encode("utf-8")) < SNAPSHOT_BUDGET_BYTES

    snap = world.client.get("/v1/snapshot?device_type=tab5&compact=true").json()
    assert len(json.dumps(snap, separators=(",", ":")).encode("utf-8")) < SNAPSHOT_BUDGET_BYTES
    for zone in snap["zones"]:
        assert "hourly" not in zone and "air_hourly" not in zone

    frame = world.client.get("/v1/embedded/frame?device_type=waveshare-7b&lat=47.75&lon=-3.36").text
    assert verify_embedded_frame_crc(frame) is True
    assert len(frame.encode("utf-8")) < FRAME_BUDGET_BYTES
