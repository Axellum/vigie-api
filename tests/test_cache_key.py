"""The number of days is not part of the cache key: the longest forecast is cached once and sliced."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.collectors.weather import MAX_FORECAST_DAYS, MetNorwayCollector, OpenMeteoCollector
from app.models.schemas import DeviceType
from tests import samples
from tests.helpers import Provider, forecast_provider, make_service


def ten_day_payload() -> dict[str, Any]:
    """The sample Open-Meteo response with ten daily forecasts."""
    payload = samples.open_meteo_payload()
    daily = payload["daily"]
    days = [f"2026-10-{18 + i}" for i in range(10)]
    payload["daily"] = {key: ([*values, *values][:10] if key != "time" else days) for key, values in daily.items()}
    return payload


@pytest.mark.asyncio
async def test_the_forecast_is_asked_for_ten_days_whatever_the_profile() -> None:
    provider = forecast_provider(ten_day_payload())
    collector = OpenMeteoCollector(http_client=provider.client())

    await collector.fetch_point(48.8566, 2.3522)

    assert MAX_FORECAST_DAYS == 10
    (daily_request,) = provider.with_param("daily")
    assert daily_request.url.params["forecast_days"] == "10"


@pytest.mark.asyncio
async def test_compact_and_full_snapshots_share_one_call_per_zone(tmp_path: Path) -> None:
    provider = forecast_provider(ten_day_payload())
    service = make_service(tmp_path / "cache-key.db", open_meteo=OpenMeteoCollector(http_client=provider.client()))
    zones = len(service.monitored_zones)
    assert zones == 5

    compact = await service.build_snapshot(device_type=DeviceType.WAVESHARE_7B, compact=True)
    full = await service.build_snapshot(device_type=DeviceType.WEB, compact=False)
    compact_again = await service.build_snapshot(device_type=DeviceType.TAB5, compact=True)

    # One "now" and one "daily" request per zone, not one more per zone and per number of days.
    assert len(provider.with_param("current")) == zones
    assert len(provider.with_param("daily")) == zones
    assert all(len(zone.daily_10d) == 5 for zone in compact.zones)
    assert all(len(zone.daily_10d) == 10 for zone in full.zones)
    assert all(len(zone.daily_10d) == 5 for zone in compact_again.zones)
    # The five days of the compact profile are the first five of the full one.
    assert [d.date for d in compact.zones[0].daily_10d] == [d.date for d in full.zones[0].daily_10d][:5]


@pytest.mark.asyncio
async def test_point_profiles_share_the_cache_entry(tmp_path: Path) -> None:
    provider = forecast_provider(ten_day_payload())
    service = make_service(
        tmp_path / "cache-key-point.db", open_meteo=OpenMeteoCollector(http_client=provider.client())
    )

    compact = await service.build_point_response(lat=45.0, lon=5.0, compact=True)
    full = await service.build_point_response(lat=45.0, lon=5.0, compact=False)

    assert len(provider.with_param("current")) == 1 and len(provider.with_param("daily")) == 1
    assert len(compact.daily) == 5 and len(full.daily) == 10


@pytest.mark.asyncio
async def test_met_norway_key_has_no_number_of_days() -> None:
    provider = Provider(samples.metno_payload())
    collector = MetNorwayCollector(http_client=provider.client())

    first = await collector.fetch_point(48.8566, 2.3522, utc_offset_s=3600)
    second = await collector.fetch_point(48.8566, 2.3522, utc_offset_s=3600)

    assert provider.count == 1
    assert first is second
