"""Weather of the overseas territories from Météo-France's AROME-OM, read from a vigie-meteo gateway.

The answers are made up in the shape the gateway gives (``GET /v1/overseas``, read on 2026-11-18): hourly steps with the model's values, the time
zone offset of the territory and the run. The numbers come from no real run (the gateway's own tests hold the real files' structure). No test touches
the network.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
import pytest

from app.attribution import credit_text
from app.collectors.base import CollectorUnavailableError
from app.collectors.meteofrance_overseas import (
    MeteoFranceOverseasCollector,
    current_weather,
    daily_forecasts,
    in_overseas_box,
    merge_overseas_bundle,
    meteo_overseas_enabled_from_env,
    parse_gateway_overseas,
    weather_code,
)
from app.collectors.weather import OpenMeteoCollector
from app.models.schemas import DailyForecast
from tests.helpers import Provider, SimClock, forecast_provider, make_service

FORT_DE_FRANCE = (14.61, -61.07)
SAINT_DENIS = (-20.88, 55.45)
VANNES = (47.66, -2.76)
OSLO = (59.91, 10.75)
OFFSET = -4 * 3600
NOW = int(SimClock.EPOCH) // 3600 * 3600  # an hour on the clock of the simulation


def iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hour(k: int, temp: float = 28.0, rain: Optional[float] = 0.0, cloud: Optional[float] = 40.0, gust: Optional[float] = 30.0) -> dict[str, Any]:
    """The hour that ends ``k`` hours after ``NOW``."""
    return {
        "time": iso(NOW + 3600 * k),
        "temperature_c": temp,
        "humidity_pct": 75.0,
        "wind_kmh": 18.0,
        "wind_direction_deg": 80.0,
        "gust_kmh": gust,
        "pressure_hpa": 1013.4,
        "cloud_cover_pct": cloud,
        "precip_mm": rain,
    }


def series(first: int = -30, last: int = 40, **kw: Any) -> list[dict[str, Any]]:
    return [hour(k, **kw) for k in range(first, last + 1)]


def payload(steps: Optional[list[dict[str, Any]]] = None, model: str = "meteofrance-arome-om", offset: int = OFFSET) -> dict[str, Any]:
    return {
        "source_model": model,
        "attribution": "x",
        "window": "guadeloupe-martinique",
        "utc_offset_s": offset,
        "run": iso(NOW - 3600 * 30),
        "nearest_node": {"lat": 14.6, "lon": -61.075},
        "steps": steps if steps is not None else series(),
    }


# ------------------------------------------------------------------------------------------------ the answer


def test_the_answer_is_read_and_its_hours_sorted() -> None:
    raw = parse_gateway_overseas(payload(list(reversed(series(0, 3)))))
    assert [h.time_utc for h in raw.hours] == [NOW + 3600 * k for k in range(4)]
    assert raw.utc_offset_s == OFFSET and raw.hours[0].temp_c == 28.0 and raw.hours[0].rain_mm == 0.0


@pytest.mark.parametrize(
    "bad",
    [
        payload(model="meteofrance-piaf"),
        payload(steps=[]),
        payload(offset=99999),
        payload(steps=[hour(0, temp=200.0)]),
        payload(steps=[{**hour(0), "temperature_c": None}]),
        payload(steps=[{**hour(0), "wind_kmh": -3.0}]),
        payload(steps=[{**hour(0), "humidity_pct": 150.0}]),
        "not an object",
    ],
)
def test_an_answer_that_is_not_the_models_or_is_impossible_is_refused(bad: Any) -> None:
    with pytest.raises(ValueError):
        parse_gateway_overseas(bad)


def test_the_first_hour_of_a_run_may_lack_the_gust_cloud_and_rain() -> None:
    raw = parse_gateway_overseas(payload([hour(0, rain=None, cloud=None, gust=None)]))
    current = current_weather(raw, NOW)
    assert current is not None and current.gust_kmh is None and current.rain_1h_mm is None and current.weather_code is None


# ------------------------------------------------------------------------------------------------ the weather code (ours)


@pytest.mark.parametrize(
    ("rain", "cloud", "code"),
    [
        (0.0, 5.0, 0), (0.0, 30.0, 1), (0.0, 60.0, 2), (0.0, 90.0, 3),  # no rain: by the cloud cover
        (0.1, 90.0, 51), (0.4, 10.0, 51), (0.5, 90.0, 61), (2.4, 90.0, 61), (2.5, 90.0, 63), (7.9, 90.0, 63), (8.0, 90.0, 65), (40.0, 100.0, 65),  # rain: whatever the cloud
        (0.0, None, None), (None, 50.0, None),  # a dry hour needs both values
    ],
)  # fmt: skip
def test_the_weather_code_follows_the_rain_then_the_cloud_cover(rain: Optional[float], cloud: Optional[float], code: Optional[int]) -> None:
    assert weather_code(rain, cloud) == code


def test_the_boxes_cover_the_territories_and_leave_the_rest_of_the_world() -> None:
    assert in_overseas_box(*FORT_DE_FRANCE) and in_overseas_box(*SAINT_DENIS)
    assert in_overseas_box(-17.55, -149.55) and in_overseas_box(-22.3, 166.45) and in_overseas_box(4.9, -52.3) and in_overseas_box(-12.8, 45.2)
    assert not in_overseas_box(*VANNES) and not in_overseas_box(*OSLO) and not in_overseas_box(0.0, 0.0)


def test_the_toggle_is_on_unless_set_to_off() -> None:
    assert meteo_overseas_enabled_from_env({}) is True
    assert meteo_overseas_enabled_from_env({"VIGIE_METEO_GATEWAY_OVERSEAS": "0"}) is False
    assert meteo_overseas_enabled_from_env({"VIGIE_METEO_GATEWAY_OVERSEAS": "Off"}) is False
    assert meteo_overseas_enabled_from_env({"VIGIE_METEO_GATEWAY_OVERSEAS": "1"}) is True


# ------------------------------------------------------------------------------------------------ the current hour and the days


def test_the_current_conditions_are_those_of_the_nearest_hour_and_none_when_it_is_far() -> None:
    raw = parse_gateway_overseas(payload([hour(0, temp=27.0), hour(1, temp=29.0, rain=3.0, cloud=100.0)]))
    near = current_weather(raw, NOW + 2400)  # 40 minutes past the first hour: the second is nearer
    assert near is not None and near.temp_c == 29.0 and near.rain_1h_mm == 3.0 and near.weather_code == 63
    assert (near.wind_kmh, near.gust_kmh, near.wind_deg, near.humidity_pct, near.pressure_hpa) == (18.0, 30.0, 80, 75, 1013.4)
    assert near.uv_index is None and near.feels_like_c is None  # not the model's
    assert current_weather(raw, NOW + 3600 * 5) is None


def local_midnight_hour(offset: int = OFFSET) -> int:
    """``k`` such that the hour ending ``NOW + k`` hours is the first hour of a local day (its local clock reads 01:00)."""
    for k in range(24):
        local = datetime.fromtimestamp(NOW + 3600 * k, tz=timezone.utc) + timedelta(seconds=offset)
        if local.hour == 1:
            return k
    raise AssertionError


def test_only_the_days_whose_24_hours_are_all_there_are_given_and_their_values_are_the_hours() -> None:
    start = local_midnight_hour()
    # one full local day and a few hours of the next: the full day is given, the partial one is not
    steps = [hour(start + i, temp=20.0 + i, rain=1.0 if i == 5 else 0.0, cloud=10.0) for i in range(24 + 6)]
    steps[7]["wind_kmh"] = 44.0
    raw = parse_gateway_overseas(payload(steps))
    days = daily_forecasts(raw)
    assert len(days) == 1
    (day,) = days
    assert day.tmin == 20.0 and day.tmax == 43.0 and day.rain_mm == 1.0 and day.wind_max == 44.0
    assert day.code == 61  # the most severe hour: 1 mm an hour is slight rain
    assert day.rain_probability_pct is None and day.uv_max is None
    first = datetime.fromtimestamp(NOW + 3600 * start - 1800, tz=timezone.utc) + timedelta(seconds=OFFSET)
    assert day.date == first.date().isoformat()


def test_a_day_with_a_missing_hour_is_not_given() -> None:
    start = local_midnight_hour()
    steps = [hour(start + i) for i in range(24) if i != 9]
    assert daily_forecasts(parse_gateway_overseas(payload(steps))) == []


# ------------------------------------------------------------------------------------------------ the bundle


def public_daily() -> dict[str, Any]:
    """Open-Meteo's daily part for the days around the full model day: every date has a probability and a UV maximum."""
    start = local_midnight_hour()
    first = (datetime.fromtimestamp(NOW + 3600 * start - 1800, tz=timezone.utc) + timedelta(seconds=OFFSET)).date()
    days = []
    for n in range(-1, 6):
        date = (first + timedelta(days=n)).isoformat()
        days.append(DailyForecast(date=date, tmax=31.0 + n, tmin=24.0, rain_probability_pct=60 + n, uv_max=9.0))
    return {"utc_offset_s": OFFSET, "daily_10d": days, "hourly_cloud_cover": [{"time": "x", "cloud_cover": 10}]}


def test_the_bundle_takes_the_models_days_and_completes_them_with_the_public_ones() -> None:
    start = local_midnight_hour()
    raw = parse_gateway_overseas(payload([hour(start + i, temp=20.0 + i) for i in range(24)]))
    bundle = merge_overseas_bundle(raw, public_daily(), NOW + 3600 * start)
    assert bundle is not None
    assert bundle["provider_name"] == "Météo-France (AROME Outre-mer)" and bundle["utc_offset_s"] == OFFSET and bundle["rain_next_60m"] is None
    days = bundle["daily_10d"]
    model_day = days[0]  # the public entry of the day before is older than the first day of the model: dropped
    assert model_day.tmax == 43.0 and model_day.tmin == 20.0  # the model's, not the public 31 °C
    assert model_day.rain_probability_pct == 60 and model_day.uv_max == 9.0  # the public day's probability and UV
    assert days[1].tmax == 32.0 and days[1].date > model_day.date and days[1].tmin == 24.0  # the next day is the public one
    assert credit_text("meteofrance_overseas") in bundle["attribution"] and credit_text("open_meteo") in bundle["attribution"]
    assert bundle["hourly_cloud_cover"]


def test_without_the_public_part_the_bundle_has_the_models_days_only_and_no_open_meteo_credit() -> None:
    start = local_midnight_hour()
    raw = parse_gateway_overseas(payload([hour(start + i) for i in range(24)]))
    bundle = merge_overseas_bundle(raw, None, NOW + 3600 * start)
    assert bundle is not None and len(bundle["daily_10d"]) == 1 and bundle["daily_10d"][0].rain_probability_pct is None
    assert credit_text("open_meteo") not in bundle["attribution"] and credit_text("meteofrance_overseas") in bundle["attribution"]
    assert bundle["hourly_cloud_cover"] == []


def test_the_bundle_is_none_when_no_hour_is_near_now() -> None:
    raw = parse_gateway_overseas(payload([hour(0)]))
    assert merge_overseas_bundle(raw, None, NOW + 3600 * 10) is None


# ------------------------------------------------------------------------------------------------ the collector


def gateway(status: int = 200, **kw: Any) -> Provider:
    def answer(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, json={"detail": "x"})
        return httpx.Response(200, json=payload(**kw))

    return Provider(answer=answer)


async def test_two_nearby_points_share_one_gateway_request(sim_clock: SimClock) -> None:
    provider = gateway()
    collector = MeteoFranceOverseasCollector("http://gateway.test/", http_client=provider.client())
    first = await collector.fetch_point(*FORT_DE_FRANCE)
    second = await collector.fetch_point(FORT_DE_FRANCE[0] + 0.004, FORT_DE_FRANCE[1] - 0.003)
    assert first.hours and second.hours and provider.count == 1
    assert provider.requests[0].url.path == "/v1/overseas"


@pytest.mark.parametrize("provider", [gateway(status=404), gateway(status=503), gateway(model="meteofrance-piaf"), gateway(steps=[])])
async def test_a_point_the_gateway_cannot_give_raises_and_never_returns_empty_hours(sim_clock: SimClock, provider: Provider) -> None:
    collector = MeteoFranceOverseasCollector("http://gateway.test", http_client=provider.client())
    with pytest.raises((ValueError, CollectorUnavailableError)):
        await collector.fetch_point(*FORT_DE_FRANCE)


async def test_a_404_does_not_open_the_circuit_and_is_remembered(sim_clock: SimClock) -> None:
    provider = gateway(status=404)
    collector = MeteoFranceOverseasCollector("http://gateway.test", http_client=provider.client())
    for k in range(6):
        with pytest.raises(ValueError):
            await collector.fetch_point(14.0 + 0.3 * k, -61.0)  # six cells outside the windows
    assert provider.count == 6
    with pytest.raises(ValueError):
        await collector.fetch_point(14.0, -61.0)
    assert provider.count == 6  # "outside the windows" is an answer


# ------------------------------------------------------------------------------------------------ the service


def _service(tmp_path: Path, provider: Optional[Provider], public: Optional[Provider] = None) -> Any:
    extra: dict[str, Any] = {"open_meteo": OpenMeteoCollector(http_client=(public or forecast_provider()).client())}
    if provider is not None:
        extra["meteofrance_overseas"] = MeteoFranceOverseasCollector("http://gateway.test", http_client=provider.client())
    return make_service(tmp_path / "om.db", **extra)


async def test_a_point_in_martinique_is_served_by_the_model_and_credited(sim_clock: SimClock, tmp_path: Path) -> None:
    provider = gateway()
    service = _service(tmp_path, provider)
    point = await service.build_point_response(*FORT_DE_FRANCE)
    assert point.weather_provider == "Météo-France (AROME Outre-mer)"
    assert point.current.temp_c == 28.0 and point.current.pressure_hpa == 1013.4 and point.current.weather_code == 1
    assert point.weather_attribution is not None and credit_text("meteofrance_overseas") in point.weather_attribution
    assert "worked out by Vigie" in point.weather_attribution
    assert provider.count == 1


async def test_a_point_in_metropolitan_france_or_the_world_never_asks_for_the_overseas_weather(sim_clock: SimClock, tmp_path: Path) -> None:
    provider = gateway()
    service = _service(tmp_path, provider)
    for lat, lon in (VANNES, OSLO):
        point = await service.build_point_response(lat, lon)
        assert point.weather_provider == "Open-Meteo"
    assert provider.count == 0


async def test_a_gateway_that_fails_or_does_not_know_the_point_leaves_the_public_api(sim_clock: SimClock, tmp_path: Path) -> None:
    for status in (503, 404):
        service = _service(tmp_path, gateway(status=status))
        point = await service.build_point_response(*FORT_DE_FRANCE)
        assert point.weather_provider == "Open-Meteo" and point.current.temp_c == 18.2  # tests/samples.py
        assert point.weather_attribution is not None and "AROME" not in point.weather_attribution


def test_the_source_is_listed_as_off_without_a_gateway(tmp_path: Path) -> None:
    service = _service(tmp_path, None)
    off = {item["provider_id"]: item["reason"] for item in service.disabled_sources()}
    assert "VIGIE_METEO_GATEWAY_URL" in off["meteofrance_overseas"]
    assert "meteofrance_overseas" not in {c.provider_id for c in service.get_all_collectors()}
    on = _service(tmp_path, gateway())
    assert "meteofrance_overseas" in {c.provider_id for c in on.get_all_collectors()}
    assert "meteofrance_overseas" not in {item["provider_id"] for item in on.disabled_sources()}
