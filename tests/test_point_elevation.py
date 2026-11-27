"""The weather of a point at an altitude asked (``GET /v1/point?elevation=``) and the freezing level (iso 0) of its hours.

Zero network calls: every provider is simulated with ``httpx.MockTransport``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from app.attribution import credit_text
from app.collectors.grid import elevation_key
from app.collectors.meteofrance_overseas import MeteoFranceOverseasCollector
from app.collectors.meteofrance_weather import MeteoFranceWeatherCollector, with_freezing_level
from app.collectors.weather import MetNorwayCollector, OpenMeteoCollector
from app.models.schemas import DeviceType, HourlyWeatherPoint
from tests import samples
from tests.conftest import World
from tests.helpers import Provider, SimClock, forecast_provider, make_service
from tests.test_meteofrance_overseas import FORT_DE_FRANCE
from tests.test_meteofrance_overseas import gateway as overseas_gateway
from tests.test_meteofrance_weather import VANNES, gateway_daily, gateway_now
from tests.test_point_hourly import BASE_UTC, _open_meteo_hourly_payload

CHAMONIX = (45.92, 6.87)  # a world point for the Open-Meteo path (outside nothing: Météo-France is not configured here)


def hourly_with_freezing(levels: list[Any] | None = None, **kw: Any) -> dict[str, Any]:
    """An Open-Meteo hourly answer whose freezing level is 1 500 m plus 10 m an hour, or ``levels``."""
    payload = _open_meteo_hourly_payload(**kw)
    count = len(payload["hourly"]["time"])
    payload["hourly"]["freezing_level_height"] = levels if levels is not None else [1500.0 + 10 * i for i in range(count)]
    return payload


def open_meteo_router(hourly: dict[str, Any] | None = None) -> Provider:
    """Open-Meteo: the "now", "daily" and "hourly" parts, each answered from its block."""
    base = samples.open_meteo_payload()
    h_payload = hourly if hourly is not None else hourly_with_freezing()

    def answer(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if "past_hours" in params:
            return httpx.Response(200, json=h_payload)
        body: dict[str, Any] = {"utc_offset_seconds": base["utc_offset_seconds"]}
        body.update({block: base[block] for block in ("current", "minutely_15", "daily") if block in params})
        return httpx.Response(200, json=body)

    return Provider(answer=answer)


# ------------------------------------------------------------------------------------------------ the requests


def test_every_open_meteo_part_carries_the_elevation_asked_and_keeps_its_weight() -> None:
    collector = OpenMeteoCollector()
    for part in ("now", "daily", "hourly"):
        plain = OpenMeteoCollector.request_params(part, 45.9, 6.85)
        high = OpenMeteoCollector.request_params(part, 45.9, 6.85, 2400)
        assert "elevation" not in plain
        assert high["elevation"] == "2400"
        assert {k: v for k, v in high.items() if k != "elevation"} == plain
        assert collector.call_weight(high) == collector.call_weight(plain)


def test_the_hourly_part_asks_for_the_freezing_level_and_weighs_1_8() -> None:
    params = OpenMeteoCollector.request_params("hourly", 45.9, 6.85)
    variables = params["hourly"].split(",")
    assert len(variables) == 18 and variables[-1] == "freezing_level_height"
    assert OpenMeteoCollector().call_weight(params) == 1.8


def test_the_gateway_of_france_is_asked_at_the_elevation_too() -> None:
    for part in ("now", "daily"):
        plain = MeteoFranceWeatherCollector.request_params(part, 45.9, 6.85)
        assert "elevation" not in plain
        assert MeteoFranceWeatherCollector.request_params(part, 45.9, 6.85, 1350)["elevation"] == "1350"


def test_an_elevation_has_its_own_cache_entries_and_none_keeps_the_old_keys() -> None:
    om = OpenMeteoCollector(grid_deg=0.05)
    assert om.cache_keys_for(*CHAMONIX) == ("om-now:45.9,6.85", "om-daily:45.9,6.85")
    assert om.cache_keys_for(*CHAMONIX, 2400) == ("om-now:45.9,6.85@2400m", "om-daily:45.9,6.85@2400m")
    assert om.hourly_cache_key_for(*CHAMONIX) == "om-hourly:45.9,6.85"
    assert om.hourly_cache_key_for(*CHAMONIX, 1035) == "om-hourly:45.9,6.85@1035m"
    mf = MeteoFranceWeatherCollector("http://gateway.test")
    assert mf.cache_keys_for(*CHAMONIX, 2400) == ("mf-now:45.9,6.85@2400m", "mf-daily:45.9,6.85@2400m")
    assert elevation_key(None) == "" and elevation_key(-12) == "@-12m"


async def test_met_norway_is_asked_for_the_altitude_in_whole_metres(sim_clock: SimClock) -> None:
    provider = Provider(samples.metno_payload())
    collector = MetNorwayCollector(http_client=provider.client(), grid_deg=0.05)
    await collector.fetch_point(*CHAMONIX)
    await collector.fetch_point(*CHAMONIX, elevation_m=2400)
    await collector.fetch_point(*CHAMONIX, elevation_m=2400)  # cached
    assert provider.count == 2
    assert "altitude" not in provider.requests[0].url.params
    assert provider.requests[1].url.params["altitude"] == "2400"


# ------------------------------------------------------------------------------------------------ the freezing level


def test_the_freezing_level_of_each_hour_is_read_in_whole_metres_and_null_stays_null() -> None:
    payload = hourly_with_freezing(levels=[1890.4, None, 0.0, 2010.6], past_hours=0, forecast_hours=4)
    hours = OpenMeteoCollector.parse_hourly(payload)["hourly_all"]
    assert [h.freezing_level_m for h in hours] == [1890, None, 0, 2011]


def test_the_hours_of_the_models_of_france_take_the_freezing_level_of_open_meteo_and_its_credit() -> None:
    hours = [HourlyWeatherPoint(time_utc=BASE_UTC + 3600 * k, temp_c=1.0) for k in range(3)]
    public = {"hourly_all": [HourlyWeatherPoint(time_utc=BASE_UTC + 3600 * k, freezing_level_m=1600 + k) for k in (1, 2, 3)]}
    bundle = {"attribution": credit_text("meteofrance_weather"), "hourly_all": hours}
    merged = with_freezing_level(bundle, public)
    assert merged is not None
    assert [h.freezing_level_m for h in merged["hourly_all"]] == [None, 1601, 1602]
    assert [h.temp_c for h in merged["hourly_all"]] == [1.0, 1.0, 1.0]
    assert credit_text("open_meteo") in merged["attribution"] and credit_text("meteofrance_weather") in merged["attribution"]
    # Nothing to add: the bundle is the same object, its credits unchanged.
    assert with_freezing_level(bundle, None) is bundle
    assert with_freezing_level(bundle, {"hourly_all": []}) is bundle
    assert with_freezing_level(None, public) is None


# ------------------------------------------------------------------------------------------------ the point


async def test_a_world_point_at_an_elevation_asks_every_part_there_and_says_so(sim_clock: SimClock, tmp_path: Path) -> None:
    provider = open_meteo_router()
    service = make_service(tmp_path / "world.db", open_meteo=OpenMeteoCollector(http_client=provider.client()))

    high = await service.build_point_response(*CHAMONIX, device_type=DeviceType.GENERIC, compact=False, elevation_m=2400)
    assert high.coordinates.elevation_m == 2400
    assert high.current is not None and high.daily and high.hourly
    assert len(provider.requests) == 3  # now, daily, hourly
    assert all(r.url.params.get("elevation") == "2400" for r in provider.requests)
    assert [h.freezing_level_m for h in high.hourly[:2]] == [1740, 1750]  # 24 past hours, then the forecast

    plain = await service.build_point_response(*CHAMONIX, device_type=DeviceType.GENERIC, compact=False)
    assert plain.coordinates.elevation_m is None
    assert len(provider.requests) == 6  # its own cache entries: a point at the cell's altitude is asked again
    assert all("elevation" not in r.url.params for r in provider.requests[3:])


async def test_a_point_in_france_at_an_elevation_asks_the_gateway_there_and_takes_the_iso_0_of_open_meteo(
    sim_clock: SimClock, tmp_path: Path
) -> None:
    mf_hourly = _open_meteo_hourly_payload(past_hours=24, forecast_hours=72, offset_s=3600, include_uv_and_prob=False)
    daily_with_hourly = {**gateway_daily(), "hourly": mf_hourly["hourly"]}

    def gw_answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=gateway_now() if "current" in request.url.params else daily_with_hourly)

    gateway = Provider(answer=gw_answer)
    public = open_meteo_router(hourly_with_freezing(offset_s=3600))
    service = make_service(
        tmp_path / "fr.db",
        open_meteo=OpenMeteoCollector(http_client=public.client()),
        meteofrance_weather=MeteoFranceWeatherCollector("http://gateway.test", http_client=gateway.client()),
    )

    point = await service.build_point_response(*VANNES, device_type=DeviceType.GENERIC, compact=False, elevation_m=1350)
    assert point.weather_provider == "Météo-France (AROME, ARPEGE)"
    assert gateway.count == 2 and all(r.url.params["elevation"] == "1350" for r in gateway.requests)
    assert {r.url.params["elevation"] for r in public.requests} == {"1350"}
    assert len(public.with_param("past_hours")) == 1
    assert point.hourly is not None and len(point.hourly) == 72
    assert all(h.freezing_level_m is not None for h in point.hourly)
    assert point.hourly[0].freezing_level_m == 1740  # the hour of now: 24 past hours of 10 m each above 1 500 m
    assert point.weather_attribution is not None and credit_text("open_meteo") in point.weather_attribution

    # The compact profile has no hours: Open-Meteo's hourly part is not asked for the iso 0.
    await service.build_point_response(*VANNES, compact=True)
    assert len(public.with_param("past_hours")) == 1


async def test_an_overseas_point_at_an_elevation_leaves_arome_om_which_takes_no_altitude(sim_clock: SimClock, tmp_path: Path) -> None:
    provider = overseas_gateway()
    service = make_service(
        tmp_path / "om.db",
        open_meteo=OpenMeteoCollector(http_client=forecast_provider().client()),
        meteofrance_overseas=MeteoFranceOverseasCollector("http://gateway.test", http_client=provider.client()),
    )
    high = await service.build_point_response(*FORT_DE_FRANCE, elevation_m=1200)
    assert high.weather_provider == "Open-Meteo" and provider.count == 0
    plain = await service.build_point_response(*FORT_DE_FRANCE)
    assert plain.weather_provider == "Météo-France (AROME Outre-mer)" and provider.count == 1


# ------------------------------------------------------------------------------------------------ the route


def test_the_route_rounds_the_elevation_to_the_metre_and_refuses_an_impossible_one(world: World) -> None:
    body = world.client.get("/v1/point?lat=45.92&lon=6.87&elevation=2400.4").json()
    assert body["coordinates"]["elevation_m"] == 2400
    assert world.client.get("/v1/point?lat=45.92&lon=6.87").json()["coordinates"]["elevation_m"] is None
    for bad in (-501, 9001, "haut"):
        assert world.client.get(f"/v1/point?lat=45.92&lon=6.87&elevation={bad}").status_code == 422
