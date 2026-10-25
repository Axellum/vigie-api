"""Open-Meteo call budget used up: MET Norway relays the weather of a point, the expired cache comes last."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.attribution import MET_NORWAY_WEATHER, credit_text
from app.collectors.budget import CallBudget, Limit, Quota
from app.collectors.weather import MetNorwayCollector, OpenMeteoCollector
from app.services.aggregator import AggregatorService
from tests import samples
from tests.helpers import Provider, SimClock, forecast_provider, make_service

PARIS = (48.85, 2.35)


def no_budget() -> CallBudget:
    """A budget with nothing left: every request is refused before it is sent."""
    return CallBudget(Quota("test", (Limit(86_400, 0.0),), "https://example.test/quota"), fraction=1.0)


async def service_with_expired_open_meteo(
    tmp_path: Path, sim_clock: SimClock, met: Provider
) -> tuple[AggregatorService, Provider]:
    """A service whose Open-Meteo entry of Paris is cached, then expired, and whose budget is forced to zero."""
    forecast = forecast_provider()
    service = make_service(
        tmp_path / "relay.db",
        open_meteo=OpenMeteoCollector(http_client=forecast.client()),
        met_norway=MetNorwayCollector(http_client=met.client()),
    )
    first, fallback = await service.fetch_weather_with_fallback(*PARIS)
    assert first is not None and first["provider_name"] == "Open-Meteo" and fallback is False
    assert met.count == 0  # MET Norway is not asked while Open-Meteo answers

    sim_clock.advance(3_601.0)  # both parts expired
    service.open_meteo.budget = no_budget()
    return service, forecast


@pytest.mark.asyncio
async def test_with_the_budget_used_up_met_norway_serves_the_weather(sim_clock: SimClock, tmp_path: Path) -> None:
    met = Provider(samples.metno_payload())
    service, forecast = await service_with_expired_open_meteo(tmp_path, sim_clock, met)

    bundle, fallback = await service.fetch_weather_with_fallback(*PARIS)

    assert forecast.count == 2  # nothing more sent to Open-Meteo
    assert met.count == 1
    assert bundle is not None and fallback is True
    assert bundle["provider_name"] == "MET Norway (fallback)" and "stale" not in bundle
    assert bundle["attribution"] == credit_text(*MET_NORWAY_WEATHER)
    assert bundle["rain_next_60m"] is None  # MET Norway has no 15-minute rain: unknown, not the old Open-Meteo one
    assert service.last_weather_fallback_used is True


@pytest.mark.asyncio
async def test_the_point_says_met_norway_and_leaves_what_it_lacks_unknown(sim_clock: SimClock, tmp_path: Path) -> None:
    met = Provider(samples.metno_payload())
    service, _ = await service_with_expired_open_meteo(tmp_path, sim_clock, met)

    point = await service.build_point_response(*PARIS)

    assert point.weather_provider == "MET Norway (fallback)" and point.fallback_used is True
    assert point.weather_attribution == credit_text(*MET_NORWAY_WEATHER)
    assert point.current is not None and point.current.gust_kmh is None and point.current.uv_index is None
    assert point.rain_nowcast.mm_per_step is None and point.rain_nowcast.rain_expected_60m is None
    assert point.air_quality is None and point.flood_risk is None  # their providers are down in this test


@pytest.mark.asyncio
async def test_when_met_norway_fails_too_the_expired_cache_is_served_marked_stale(
    sim_clock: SimClock, tmp_path: Path
) -> None:
    met = Provider(answer=lambda request: httpx.Response(503))
    service, forecast = await service_with_expired_open_meteo(tmp_path, sim_clock, met)

    bundle, fallback = await service.fetch_weather_with_fallback(*PARIS)

    assert forecast.count == 2 and met.count == 1
    assert bundle is not None and fallback is True
    assert bundle["stale"] is True and bundle["provider_name"] == "Open-Meteo (stale cache)"
    assert bundle["current"] is not None
