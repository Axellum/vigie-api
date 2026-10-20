"""Weight of the Open-Meteo requests under the published rule, and the two-request forecast."""

from __future__ import annotations

import httpx
import pytest

from app.collectors.base import CollectorUnavailableError
from app.collectors.flood_air import OpenMeteoAirQualityCollector, OpenMeteoFloodCollector
from app.collectors.marine import OpenMeteoMarineCollector
from app.collectors.weather import OpenMeteoCollector
from app.collectors.weights import open_meteo_weight
from tests import samples
from tests.helpers import Provider, SimClock, forecast_provider

# The variables the single forecast request asked for before it was split in two. The split must not
# lose one of them: a field the frame or a route uses would silently become unknown.
FORMER_CURRENT = [
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "pressure_msl",
    "wind_speed_10m",
    "wind_gusts_10m",
    "wind_direction_10m",
    "precipitation",
    "weather_code",
    "uv_index",
]
FORMER_MINUTELY_15 = ["precipitation"]
FORMER_DAILY = [
    "weather_code",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "precipitation_probability_max",
    "wind_speed_10m_max",
    "uv_index_max",
]


def hourly(count: int) -> str:
    return ",".join(f"v{i}" for i in range(count))


# ============================================================================
# The rule
# ============================================================================


def test_published_examples() -> None:
    # https://open-meteo.com/en/pricing: 2 weeks with 15 variables is 1.5 calls, 4 weeks 3.0 calls.
    assert open_meteo_weight({"latitude": "1", "hourly": hourly(15), "forecast_days": "14"}) == 1.5
    assert open_meteo_weight({"latitude": "1", "hourly": hourly(15), "forecast_days": "28"}) == 3.0
    # A request within 10 variables and 2 weeks is one call.
    assert open_meteo_weight({"latitude": "1", "hourly": hourly(10), "forecast_days": "14"}) == 1.0
    assert open_meteo_weight({"latitude": "1", "hourly": hourly(1), "forecast_days": "1"}) == 1.0


def test_variables_of_every_block_and_locations_are_counted() -> None:
    params = {"latitude": "1,2", "current": hourly(5), "daily": hourly(4), "minutely_15": "a", "hourly": hourly(1)}
    assert open_meteo_weight(params) == 2 * 1.1  # 11 variables, 2 locations
    assert open_meteo_weight({"latitude": "1", "hourly": hourly(10), "forecast_days": "7", "past_days": "14"}) == 1.5


def test_the_default_length_applies_when_the_request_names_none() -> None:
    params = {"latitude": "1", "daily": hourly(3)}
    assert open_meteo_weight(params, default_forecast_days=7) == 1.0
    assert open_meteo_weight(params, default_forecast_days=92) == pytest.approx(92 / 14, abs=1e-3)
    assert open_meteo_weight({**params, "forecast_days": "oops"}, default_forecast_days=7) == 1.0


# ============================================================================
# The requests of the collectors
# ============================================================================


def test_the_two_forecast_requests_carry_every_former_variable() -> None:
    now = OpenMeteoCollector.request_params("now", 48.85, 2.35)
    daily = OpenMeteoCollector.request_params("daily", 48.85, 2.35)

    assert now["current"].split(",") == FORMER_CURRENT
    assert now["minutely_15"].split(",") == FORMER_MINUTELY_15
    assert daily["daily"].split(",") == FORMER_DAILY
    assert "daily" not in now and "current" not in daily and "minutely_15" not in daily


def test_weight_of_the_forecast_before_and_after_the_split() -> None:
    collector = OpenMeteoCollector()
    now = OpenMeteoCollector.request_params("now", 48.85, 2.35)
    daily = OpenMeteoCollector.request_params("daily", 48.85, 2.35)
    single = {**now, **daily}  # the request as it was: 18 variables, 10 days

    assert collector.call_weight(single) == 1.8
    assert collector.call_weight(now) == 1.1
    assert collector.call_weight(daily) == 1.0


@pytest.mark.asyncio
async def test_weight_of_the_other_open_meteo_requests() -> None:
    air = Provider({"current": {"european_aqi": 31, "pm10": 14.0}})
    flood = Provider({"daily": {"river_discharge": [42.0], "river_discharge_mean": [40.0], "river_discharge_max": [46.0]}})
    marine = Provider(samples.marine_payload())
    air_collector = OpenMeteoAirQualityCollector(http_client=air.client())
    flood_collector = OpenMeteoFloodCollector(http_client=flood.client())
    marine_collector = OpenMeteoMarineCollector(http_client=marine.client())

    await air_collector.fetch_air_quality(48.85, 2.35)
    await flood_collector.fetch_flood_risk(48.85, 2.35)
    await marine_collector.fetch_marine(48.85, 2.35)

    assert air_collector.call_weight(air.query()) == 1.0  # 6 variables
    assert flood_collector.call_weight(flood.query()) == 1.0  # 3 variables, 7 days
    assert marine_collector.call_weight(marine.query()) == 1.0  # 8 variables


# ============================================================================
# The forecast in two requests
# ============================================================================


@pytest.mark.asyncio
async def test_the_two_parts_make_the_bundle_of_the_single_response() -> None:
    provider = forecast_provider()
    collector = OpenMeteoCollector(http_client=provider.client())

    bundle = await collector.fetch_point(48.8566, 2.3522)

    assert provider.count == 2
    assert bundle == OpenMeteoCollector.parse_response(samples.open_meteo_payload())


@pytest.mark.asyncio
async def test_each_part_is_refreshed_at_its_own_pace(sim_clock: SimClock) -> None:
    provider = forecast_provider()
    collector = OpenMeteoCollector(http_client=provider.client())
    await collector.fetch_point(48.85, 2.35)
    assert (len(provider.with_param("current")), len(provider.with_param("daily"))) == (1, 1)

    sim_clock.advance(900.0)
    await collector.fetch_point(48.85, 2.35)
    assert provider.count == 2  # 15 minutes: still fresh

    sim_clock.advance(1.0)
    await collector.fetch_point(48.85, 2.35)
    assert (len(provider.with_param("current")), len(provider.with_param("daily"))) == (2, 1)  # only "now"

    sim_clock.advance(2700.0)  # 3601 s after the start
    await collector.fetch_point(48.85, 2.35)
    assert (len(provider.with_param("current")), len(provider.with_param("daily"))) == (3, 2)


@pytest.mark.asyncio
async def test_the_split_costs_less_than_the_single_request_over_a_day(sim_clock: SimClock) -> None:
    provider = forecast_provider()
    collector = OpenMeteoCollector(http_client=provider.client())

    while sim_clock.now < 86_400:  # someone asks every 90 seconds, like the scheduler
        await collector.fetch_point(48.85, 2.35)
        sim_clock.advance(90.0)

    weight = sum(collector.call_weight(dict(r.url.params)) for r in provider.requests)
    now_calls = len(provider.with_param("current"))
    assert (now_calls, len(provider.with_param("daily"))) == (88, 24)  # a refresh every 990 s and every 3690 s
    assert weight == pytest.approx(88 * 1.1 + 24 * 1.0)
    assert weight < 0.8 * (now_calls * 1.8)  # the single request would have weighed 1.8 at each 15 minutes


@pytest.mark.asyncio
async def test_a_missing_part_fails_the_forecast_so_that_the_caller_falls_back() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if "daily" in request.url.params:
            return httpx.Response(500)
        return httpx.Response(200, json=samples.open_meteo_payload())

    provider = Provider(answer=answer)
    collector = OpenMeteoCollector(http_client=provider.client())

    with pytest.raises(CollectorUnavailableError):
        await collector.fetch_point(48.85, 2.35)
    # The current conditions that were fetched are kept for the next call.
    now_key, _ = collector.cache_keys_for(48.85, 2.35)
    assert now_key in collector._cache
