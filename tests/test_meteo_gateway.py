"""Météo-France PIAF rain through a vigie-meteo gateway (``app/collectors/meteo_gateway.py``).

The answers are shaped as the gateway's ``GET /v1/rain-nowcast`` answered on 2026-11-16 (a rainy evening in Brittany): twelve
slices dated by the end of their 5 minutes, ``source_model`` ``meteofrance-piaf``. No test touches the network.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
import pytest

from app.attribution import RAIN_PIAF, credit_text
from app.collectors.meteo_gateway import (
    PIAF_SOURCE_MODEL,
    HourOfRain,
    MeteoGatewayRainCollector,
    RainSlices,
    in_piaf_box,
    meteo_gateway_url_from_env,
    next_hour_rates,
    parse_gateway_rain,
    quarter_hour_amounts,
)
from app.collectors.weather import OpenMeteoCollector
from app.services.embedded_frame import _rain_eta_min
from tests.helpers import Provider, SimClock, forecast_provider, make_service

PARIS = (48.8566, 2.3522)
OSLO = (59.91, 10.75)
# The rates of the gateway's answer read on 2026-11-16 (Vannes, run of 20:55 UTC), in mm/h
RECORDED_RATES = [11.28, 10.8, 12.72, 3.0, 0.0, 0.0, 0.36, 7.8, 3.36, 2.4, 2.28, 2.04]


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def gateway_answer(first_end: int, rates: list[float], model: str = PIAF_SOURCE_MODEL) -> dict[str, Any]:
    """An answer of the gateway: one slice per rate, the first one ending at ``first_end``."""
    return {
        "status": "fresh",
        "slices": [
            {"time": _iso(first_end + 300 * i), "rain_level": 1, "rain_label": "Temps sec", "rain_mm_h": rate}
            for i, rate in enumerate(rates)
        ],
        "updated_at": _iso(first_end),
        "source_model": model,
    }


def boundary(now_s: float) -> int:
    return int(now_s) // 300 * 300


def test_the_url_comes_from_the_environment_and_a_wrong_one_is_not_used() -> None:
    assert meteo_gateway_url_from_env({}) == (None, None)
    assert meteo_gateway_url_from_env({"VIGIE_METEO_GATEWAY_URL": " http://gateway.test:8000/ "}) == (
        "http://gateway.test:8000",
        None,
    )
    url, problem = meteo_gateway_url_from_env({"VIGIE_METEO_GATEWAY_URL": "gateway.test:8000"})
    assert url is None and problem is not None and "not an http(s) address" in problem


def test_only_points_around_france_ask_the_gateway() -> None:
    assert in_piaf_box(*PARIS)
    assert in_piaf_box(42.0, 9.0)  # Corsica
    assert not in_piaf_box(*OSLO)
    assert not in_piaf_box(16.2, -61.5)  # Guadeloupe: PIAF covers the mainland only


def test_a_recorded_answer_is_read_slice_by_slice() -> None:
    slices = parse_gateway_rain(gateway_answer(1_700_000_100, RECORDED_RATES))
    assert slices.mm_h == tuple(RECORDED_RATES)
    assert slices.ends_utc == tuple(1_700_000_100 + 300 * i for i in range(12))


@pytest.mark.parametrize(
    "payload",
    [
        gateway_answer(1_700_000_100, RECORDED_RATES, model="meteofrance_arome_france_hd_15min"),  # the gateway fell back
        {"status": "unavailable", "slices": [], "source_model": PIAF_SOURCE_MODEL},
        gateway_answer(1_700_000_100, [1.0, -0.5]),
        gateway_answer(1_700_000_100, [1.0, float("nan")]),
        {**gateway_answer(1_700_000_100, [1.0]), "slices": [{"time": "2026-11-16T21:00:00", "rain_mm_h": 1.0}]},
        [],
    ],
)
def test_an_answer_that_is_not_plain_piaf_is_refused(payload: Any) -> None:
    with pytest.raises(ValueError):
        parse_gateway_rain(payload)


def test_the_hour_starts_at_the_current_five_minute_boundary() -> None:
    now = 1_700_000_130.0  # 2 min 10 s after a boundary
    start = boundary(now)
    # A run 20 minutes old: four slices are already past, sixteen are ahead
    slices = parse_gateway_rain(gateway_answer(start - 15 * 60, [float(i) for i in range(20)]))
    assert next_hour_rates(slices, now) == [float(i) for i in range(4, 16)]


def test_an_hour_with_a_missing_slice_is_unknown_never_completed() -> None:
    now = 1_700_000_130.0
    start = boundary(now)
    # A run so old that only eleven slices are ahead
    old = parse_gateway_rain(gateway_answer(start - 25 * 60, [1.0] * 17))
    assert next_hour_rates(old, now) is None
    # A gap in the middle
    gap = RainSlices(
        ends_utc=tuple(start + 300 * (i + 1) for i in range(13) if i != 6),
        mm_h=tuple(1.0 for i in range(13) if i != 6),
    )
    assert next_hour_rates(gap, now) is None


def test_the_quarter_hour_amounts_are_the_sums_of_three_five_minute_steps() -> None:
    assert quarter_hour_amounts(RECORDED_RATES) == [2.9, 0.25, 0.96, 0.56]
    assert quarter_hour_amounts([0.0] * 12) == [0.0, 0.0, 0.0, 0.0]


async def test_the_collector_snaps_the_point_and_caches_the_run(sim_clock: SimClock) -> None:
    start = boundary(sim_clock.time())
    gateway = Provider(payload=gateway_answer(start - 10 * 60, [0.6] * 18))
    collector = MeteoGatewayRainCollector("http://gateway.test", http_client=gateway.client())

    assert await collector.rain_next_hour(*PARIS) == HourOfRain(start, [0.6] * 12)
    assert await collector.rain_next_hour(PARIS[0] + 0.001, PARIS[1]) == HourOfRain(start, [0.6] * 12)  # the same cell: from the cache
    assert gateway.count == 1
    assert gateway.requests[0].url.path == "/v1/rain-nowcast"
    assert gateway.query(0) == {"lat": "48.86", "lon": "2.35"}  # the PIAF grid, 0.01 degree
    # 2 km away is another PIAF cell (it was the same 5 km forecast cell before): asked of the gateway, not served from the cache
    assert await collector.rain_next_hour(PARIS[0] + 0.02, PARIS[1]) == HourOfRain(start, [0.6] * 12)
    assert gateway.count == 2 and gateway.query(1) == {"lat": "48.88", "lon": "2.35"}

    assert await collector.rain_next_hour(*OSLO) is None  # outside the box: not asked
    assert gateway.count == 2


async def test_an_expired_run_still_serves_while_it_covers_the_hour_then_the_hour_is_unknown(sim_clock: SimClock) -> None:
    start = boundary(sim_clock.time())
    answers = [httpx.Response(200, json=gateway_answer(start - 10 * 60, [0.6] * 18))]

    def answer(request: httpx.Request) -> httpx.Response:
        return answers.pop(0) if answers else httpx.Response(503)

    collector = MeteoGatewayRainCollector("http://gateway.test", http_client=Provider(answer=answer).client())
    assert await collector.rain_next_hour(*PARIS) == HourOfRain(start, [0.6] * 12)
    sim_clock.advance(301)  # expired, the gateway is down: the old run still covers the next hour, from the new boundary
    assert await collector.rain_next_hour(*PARIS) == HourOfRain(boundary(sim_clock.time()), [0.6] * 12)
    sim_clock.advance(1200)  # half an hour after the run: not twelve slices ahead any more
    assert await collector.rain_next_hour(*PARIS) is None


async def test_a_gateway_that_falls_back_to_a_model_leaves_the_hour_unknown(sim_clock: SimClock) -> None:
    start = boundary(sim_clock.time())
    gateway = Provider(payload=gateway_answer(start, [0.6] * 18, model="meteofrance_arome_france_hd_15min"))
    collector = MeteoGatewayRainCollector("http://gateway.test", http_client=gateway.client())
    assert await collector.rain_next_hour(*PARIS) is None
    assert collector.last_error is not None and "not PIAF" in collector.last_error


def _service(tmp_path: Path, gateway: Optional[MeteoGatewayRainCollector]) -> Any:
    extra: dict[str, Any] = {"open_meteo": OpenMeteoCollector(http_client=forecast_provider().client())}
    if gateway is not None:
        extra["meteo_gateway"] = gateway
    return make_service(tmp_path / "piaf.db", **extra)


async def test_the_point_takes_the_piaf_rain_and_credits_it(sim_clock: SimClock, tmp_path: Path) -> None:
    start = boundary(sim_clock.time())
    gateway = Provider(payload=gateway_answer(start - 10 * 60, [0.0, 0.0, 0.0] + RECORDED_RATES + [9.0] * 3))
    service = _service(tmp_path, MeteoGatewayRainCollector("http://gateway.test", http_client=gateway.client()))

    point = await service.build_point_response(*PARIS)
    rain = point.rain_nowcast
    assert rain.rate_5m_mm_h == RECORDED_RATES
    assert rain.mm_per_step == [2.9, 0.25, 0.96, 0.56]
    assert rain.start_utc == start  # the 5-minute boundary at which the rain was read: also the start of the first rate
    assert rain.interval_minutes == 15
    assert rain.max_mm_h == 12.72
    assert rain.rain_expected_60m is True
    assert rain.attribution is not None and credit_text(*RAIN_PIAF) in rain.attribution
    assert "Open-Meteo" in rain.attribution  # the current conditions still come from Open-Meteo

    frame = await service.build_embedded_frame(lat=PARIS[0], lon=PARIS[1])
    rain_line = next(line for line in frame.splitlines() if line.startswith("RAIN5M|GPS|"))
    assert rain_line == "RAIN5M|GPS|" + ",".join(f"{v:.2f}" for v in RECORDED_RATES)
    zone = next(line for line in frame.splitlines() if line.startswith("ZONE|GPS|")).split("|")
    assert zone[17:19] == ["11.6", "2.90,0.25,0.96,0.56"]
    # Field 16 counts from the time of the frame (``HEAD`` field 1, the real clock here, not the simulated one of the rain)
    frame_utc = int(next(line for line in frame.splitlines() if line.startswith("HEAD|")).split("|")[1])
    assert zone[16] == str(_rain_eta_min([2.9, 0.25, 0.96, 0.56], start, frame_utc))


async def test_without_piaf_the_rain_stays_open_meteo_s(sim_clock: SimClock, tmp_path: Path) -> None:
    down = Provider(answer=lambda request: httpx.Response(503))
    for gateway in (None, MeteoGatewayRainCollector("http://gateway.test", http_client=down.client())):
        service = _service(tmp_path, gateway)
        point = await service.build_point_response(*PARIS)
        assert point.rain_nowcast.rate_5m_mm_h is None
        assert point.rain_nowcast.mm_per_step == [0.0, 0.2, 0.5, 0.1]  # tests/samples.py
        assert point.rain_nowcast.start_utc == 1792332900  # 14:15 UTC: 15 minutes before Open-Meteo's first time
        assert point.rain_nowcast.attribution is not None
        assert credit_text(*RAIN_PIAF) not in point.rain_nowcast.attribution
        frame = await service.build_embedded_frame(lat=PARIS[0], lon=PARIS[1])
        assert "RAIN5M|GPS|0.00,0.00,0.00,0.80,0.80,0.80,2.00,2.00,2.00,0.40,0.40,0.40" in frame.splitlines()


def test_the_source_is_listed_as_off_without_a_gateway(tmp_path: Path) -> None:
    service = _service(tmp_path, None)
    off = {item["provider_id"]: item["reason"] for item in service.disabled_sources()}
    assert "VIGIE_METEO_GATEWAY_URL" in off["meteofrance_piaf"]
    assert all(c.provider_id != "meteofrance_piaf" for c in service.get_all_collectors())
