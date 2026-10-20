"""Call budget: count the requests really sent, never exceed a published quota, serve the cache past it."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.collectors.base import CollectorUnavailableError
from app.collectors.budget import (
    DEFAULT_BUDGET_FRACTION,
    QUOTAS,
    CallBudget,
    Limit,
    Quota,
    UsageWindow,
    budget_fraction_from_env,
    share_budgets,
)
from app.collectors.flood_air import OpenMeteoAirQualityCollector
from app.collectors.seismic import USGSCollector
from app.collectors.weather import OpenMeteoCollector
from app.models.schemas import CircuitState
from tests import samples
from tests.conftest import World
from tests.helpers import Provider, SimClock, forecast_provider, make_service

AQI_PAYLOAD = {"current": {"european_aqi": 31, "us_aqi": 42, "pm10": 14.0, "pm2_5": 8.0, "uv_index": 3.0, "dust": 1.0}}
USGS_PAYLOAD = {"features": []}


def tiny_quota(calls: float, window_s: int = 86_400) -> Quota:
    return Quota("test", (Limit(window_s, calls),), "https://example.test/quota")


def aqi_collector(provider: Provider, calls: float, **kwargs: float) -> OpenMeteoAirQualityCollector:
    """An air quality collector whose daily budget is ``calls`` (and not the real Open-Meteo quota)."""
    collector = OpenMeteoAirQualityCollector(http_client=provider.client())
    collector.budget = CallBudget(tiny_quota(calls), fraction=kwargs.get("fraction", 1.0))
    return collector


# ============================================================================
# The sliding window and the budget
# ============================================================================


def test_usage_window_slides() -> None:
    window = UsageWindow(seconds=100)
    window.add(0.0, 1.0)
    window.add(50.0, 1.5)
    assert (window.calls(60.0), window.weight(60.0)) == (2, 2.5)
    assert (window.calls(100.0), window.weight(100.0)) == (1, 1.5)  # the first call is exactly 100 s old: out
    assert (window.calls(150.0), window.weight(150.0)) == (0, 0.0)


def test_budget_refuses_what_would_exceed_a_limit_and_counts_nothing_then() -> None:
    budget = CallBudget(tiny_quota(3.0), fraction=1.0)
    assert [budget.try_spend(1.0, float(t)) for t in range(4)] == [True, True, True, False]
    assert budget.status(10.0).limits[0].used == 3.0  # type: ignore[union-attr]
    # A smaller request still fits where a bigger one does not.
    budget = CallBudget(tiny_quota(3.0), fraction=1.0)
    assert budget.try_spend(2.5, 0.0) and not budget.try_spend(1.1, 1.0) and budget.try_spend(0.5, 2.0)


def test_open_meteo_quota_is_checked_on_the_day_the_hour_and_the_minute() -> None:
    quota = QUOTAS["open_meteo_hd"]
    assert [(limit.window_s, limit.calls) for limit in quota.limits] == [(86_400, 10_000), (3_600, 5_000), (60, 600)]
    assert quota.source_url == "https://open-meteo.com/en/terms"

    budget = CallBudget(quota, fraction=1.0)
    assert all(budget.try_spend(1.0, 0.0) for _ in range(600))
    assert not budget.try_spend(1.0, 59.0)  # the 601st call of the minute
    assert budget.try_spend(1.0, 61.0)  # the minute has slid

    spent = 601  # one hour at the pace of 600 a minute: stopped by the hourly limit
    for minute in range(2, 60):
        for _ in range(600):
            if budget.try_spend(1.0, minute * 60.0 + 1.0):
                spent += 1
    assert spent == 5_000
    assert not budget.try_spend(1.0, 3_599.0)

    # The daily limit: 5000 an hour for two hours, never more than 10000 in 24 hours.
    daily = CallBudget(quota, fraction=1.0)
    total = 0
    for second in range(0, 86_400, 30):
        for _ in range(100):
            if daily.try_spend(1.0, float(second)):
                total += 1
    assert total == 10_000


def test_the_fraction_keeps_a_margin() -> None:
    budget = CallBudget(tiny_quota(10.0), fraction=0.9)
    assert sum(budget.try_spend(1.0, 0.0) for _ in range(20)) == 9
    status = budget.status(0.0)
    assert status is not None and status.limits[0].limit == 10.0 and status.limits[0].budget == 9.0


def test_a_provider_without_a_published_quota_has_no_budget() -> None:
    budget = CallBudget(None)
    assert all(budget.try_spend(1.0, float(t)) for t in range(100_000))
    assert budget.status(0.0) is None and not budget.is_exhausted(0.0)


def test_fraction_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIGIE_BUDGET_FRACTION", raising=False)
    assert budget_fraction_from_env() == DEFAULT_BUDGET_FRACTION == 0.9
    monkeypatch.setenv("VIGIE_BUDGET_FRACTION", "0.5")
    assert budget_fraction_from_env() == 0.5
    for raw in ("0", "1.5", "-1", "abc", "nan"):
        monkeypatch.setenv("VIGIE_BUDGET_FRACTION", raw)
        assert budget_fraction_from_env() == 0.9


def test_quotas_are_declared_in_one_place_with_their_source() -> None:
    assert set(QUOTAS) == {
        "open_meteo_hd",
        "open_meteo_aqi",
        "open_meteo_flood",
        "open_meteo_marine",
        "met_norway_fallback",
        "rainviewer_radar",
        "eccc_alerts",
        "wmo_swic",  # not published: a ceiling of our own (see budget.py)
        "nasa_firms",  # 5,000 transactions per 10 minutes per key, a world read counting 36
    }
    assert all(quota.source_url.startswith("https://") for quota in QUOTAS.values())
    assert len({id(QUOTAS[name]) for name in QUOTAS if name.startswith("open_meteo")}) == 1  # one shared quota


# ============================================================================
# In a collector
# ============================================================================


@pytest.mark.asyncio
async def test_requests_are_counted_when_they_are_sent_even_without_a_quota(sim_clock: SimClock) -> None:
    provider = Provider(USGS_PAYLOAD)
    collector = USGSCollector(http_client=provider.client())

    for _ in range(5):
        await collector.fetch(force_refresh=True)

    health = collector.get_health()
    assert (health.calls_24h, health.weight_24h, health.quota) == (5, 5.0, None)
    sim_clock.advance(86_401.0)
    assert collector.get_health().calls_24h == 0  # the window is 24 hours, sliding


@pytest.mark.asyncio
async def test_a_failed_request_is_counted_too(sim_clock: SimClock) -> None:
    provider = Provider(answer=lambda request: httpx.Response(500))
    collector = USGSCollector(http_client=provider.client())

    with pytest.raises(CollectorUnavailableError):
        await collector.fetch()
    assert collector.get_health().calls_24h == 1


@pytest.mark.asyncio
async def test_simultaneous_callers_are_one_counted_call() -> None:
    provider = Provider(USGS_PAYLOAD)
    collector = USGSCollector(http_client=provider.client())

    await asyncio.gather(*(collector.fetch() for _ in range(40)))

    assert collector.get_health().calls_24h == 1 == provider.count


@pytest.mark.asyncio
async def test_open_meteo_weight_is_counted(sim_clock: SimClock) -> None:
    provider = forecast_provider()
    collector = OpenMeteoCollector(http_client=provider.client())

    await collector.fetch_point(48.85, 2.35)

    health = collector.get_health()
    assert health.calls_24h == 2 and health.weight_24h == pytest.approx(2.1)  # "now" 1.1 + "daily" 1.0
    assert health.quota is not None
    assert health.quota.group == "open-meteo" and health.quota.source_url == "https://open-meteo.com/en/terms"
    by_window = {limit.window_s: limit for limit in health.quota.limits}
    assert by_window[86_400].used == pytest.approx(2.1)
    assert (by_window[86_400].limit, by_window[86_400].budget) == (10_000, 9_000)  # 90 % of the published limit
    assert health.quota.exhausted is False


@pytest.mark.asyncio
async def test_beyond_the_budget_the_request_is_not_sent_and_the_circuit_is_untouched(sim_clock: SimClock) -> None:
    provider = Provider(AQI_PAYLOAD)
    collector = aqi_collector(provider, calls=2)

    await collector.fetch_air_quality(10.0, 10.0)
    await collector.fetch_air_quality(20.0, 20.0)
    assert provider.count == 2

    with pytest.raises(CollectorUnavailableError, match="budget"):
        await collector.fetch_air_quality(30.0, 30.0)  # a third cell: nothing cached, nothing sent

    assert provider.count == 2  # never exceeds the budget
    assert collector.budget_refusals == 1
    assert collector.consecutive_failures == 0 and collector.circuit_state == CircuitState.CLOSED
    assert collector.get_health().quota.exhausted is True  # type: ignore[union-attr]
    assert collector.get_health().budget_refusals == 1


@pytest.mark.asyncio
async def test_beyond_the_budget_the_expired_cache_is_served_and_marked_stale(sim_clock: SimClock) -> None:
    provider = Provider(AQI_PAYLOAD)
    collector = aqi_collector(provider, calls=1)

    fresh = await collector.fetch_air_quality(10.0, 10.0)
    assert fresh.stale is False

    sim_clock.advance(7200.0)  # the entry expired (1 hour), the budget (1 call a day) is used up
    served = await collector.fetch_air_quality(10.0, 10.0)

    assert provider.count == 1
    assert served.stale is True
    assert served.european_aqi == fresh.european_aqi  # the same values, flagged

    sim_clock.advance(86_400.0)  # the day has slid: the provider is asked again
    renewed = await collector.fetch_air_quality(10.0, 10.0)
    assert provider.count == 2 and renewed.stale is False


@pytest.mark.asyncio
async def test_the_stale_cache_is_served_even_when_the_caller_refuses_stale(sim_clock: SimClock) -> None:
    provider = forecast_provider()
    collector = OpenMeteoCollector(http_client=provider.client())
    collector.budget = CallBudget(tiny_quota(3.0), fraction=1.0)  # the first refresh spends 2.1 of 3

    first = await collector.fetch_point(48.85, 2.35, allow_stale_on_error=False)
    assert "stale" not in first and first["provider_name"] == "Open-Meteo"

    sim_clock.advance(901.0)  # "now" must be refreshed (1.1), 2.1 + 1.1 > 3
    bundle = await collector.fetch_point(48.85, 2.35, allow_stale_on_error=False)

    assert provider.count == 2  # not sent
    assert bundle["stale"] is True and bundle["provider_name"] == "Open-Meteo (stale cache)"
    assert bundle["current"] == first["current"]


@pytest.mark.asyncio
async def test_a_stale_weather_bundle_counts_as_a_fallback_in_the_zone_weather(
    sim_clock: SimClock, tmp_path: Path
) -> None:
    provider = forecast_provider()
    om = OpenMeteoCollector(http_client=provider.client())
    om.budget = CallBudget(tiny_quota(13.7), fraction=1.0)  # the five zones fit once (10.5) and two more "now" requests
    service = make_service(tmp_path / "budget.db", open_meteo=om)

    first = await service.build_snapshot()
    assert all(zone.weather_source == "Open-Meteo" for zone in first.zones)
    assert first.global_status.weather_fallback_active is False

    sim_clock.advance(901.0)
    second = await service.build_snapshot()

    assert provider.count == 10 + 2  # 13.7 - 10.5 = 3.2 allows two more "now" requests (2 x 1.1), not a third
    assert {zone.weather_source for zone in second.zones} <= {"Open-Meteo", "Open-Meteo (stale cache)"}
    stale_zones = [zone for zone in second.zones if zone.weather_source == "Open-Meteo (stale cache)"]
    assert len(stale_zones) == 3
    assert second.global_status.weather_fallback_active is True
    assert all(zone.current is not None for zone in second.zones)  # the cached values, never a gap


def test_the_collectors_of_one_quota_share_one_budget(tmp_path: Path) -> None:
    service = make_service(tmp_path / "share.db")
    open_meteo = [service.open_meteo, service.air_quality, service.flood, service.marine]

    assert len({id(collector.budget) for collector in open_meteo}) == 1
    assert service.met_norway.budget is not service.open_meteo.budget
    assert service.rainviewer.budget is not service.met_norway.budget
    assert service.usgs.budget.quota is None

    # share_budgets can be run again on the same collectors without changing anything.
    shared = service.open_meteo.budget
    share_budgets(service.get_all_collectors())
    assert service.marine.budget is shared


@pytest.mark.asyncio
async def test_open_meteo_collectors_spend_from_the_same_budget(sim_clock: SimClock) -> None:
    forecast, air = forecast_provider(), Provider(AQI_PAYLOAD)
    om = OpenMeteoCollector(http_client=forecast.client())
    aqi = OpenMeteoAirQualityCollector(http_client=air.client())
    share_budgets([om, aqi])
    om.budget = CallBudget(tiny_quota(3.0), fraction=1.0)
    aqi.budget = om.budget

    await aqi.fetch_air_quality(10.0, 10.0)  # 1.0 of 3
    with pytest.raises(CollectorUnavailableError, match="budget"):
        await om.fetch_point(48.85, 2.35)  # "now" 1.1 fits (2.1), "daily" 1.0 does not (3.1): nothing cached to serve

    assert air.count == 1 and forecast.count == 1
    assert aqi.get_health().calls_24h == 1 and om.get_health().calls_24h == 1
    assert om.budget.status(0.0).limits[0].used == pytest.approx(2.1)  # type: ignore[union-attr]
    assert aqi.budget.status(0.0).limits[0].used == pytest.approx(2.1)  # type: ignore[union-attr]


# ============================================================================
# In the API
# ============================================================================


def test_providers_endpoint_exposes_calls_weight_and_quota(world: World) -> None:
    body = world.client.get("/v1/providers").json()
    by_id = {provider["provider_id"]: provider for provider in body}

    forecast = by_id["open_meteo_hd"]
    assert forecast["calls_24h"] == 0 and forecast["weight_24h"] == 0.0
    assert forecast["quota"]["group"] == "open-meteo"
    assert forecast["quota"]["source_url"] == "https://open-meteo.com/en/terms"
    assert [limit["window_s"] for limit in forecast["quota"]["limits"]] == [86_400, 3_600, 60]
    assert by_id["usgs_eq"]["quota"] is None  # no published quota: not verified, counted only
    assert "budget_refusals" in forecast


def test_health_endpoint_carries_the_same_fields(world: World) -> None:
    providers = TestClient(world.client.app).get("/v1/health").json()["providers"]
    assert all({"calls_24h", "weight_24h", "quota"} <= set(provider) for provider in providers)
    assert samples  # the sample module is part of the test world
