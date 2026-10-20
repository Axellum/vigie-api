"""Cache lifetimes: one per source, from the source's own update rhythm (see docs/data-sources.md)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.collectors.base import BaseCollector
from app.collectors.disasters import GDACSCollector, NOAANWSCollector
from app.collectors.flood_air import (
    OpenMeteoAirQualityCollector,
    OpenMeteoFloodCollector,
    USGSVolcanoCollector,
)
from app.collectors.marine import OpenMeteoMarineCollector
from app.collectors.seismic import EMSCCollector, USGSCollector
from app.collectors.space_weather import NOAASWPCCollector
from app.collectors.weather import MetNorwayCollector, OpenMeteoCollector, RainViewerCollector
from tests.helpers import Provider, SimClock

# provider id -> TTL in seconds. Changing one means re-reading the provider's documentation and
# updating the "Cache (TTL)" column of docs/data-sources.md.
EXPECTED_TTL_S = {
    "open_meteo_hd": 900,  # 15-minute data of the models, which are updated hourly at best
    "open_meteo_aqi": 3600,  # hourly / 3-hourly values, model updated every 12 to 24 hours
    "open_meteo_flood": 21600,  # GloFAS updated once a day
    "open_meteo_marine": 3600,  # hourly / 3-hourly values, models updated every 6 to 24 hours
    "met_norway_fallback": 300,  # only when the answer has no Expires header
    "rainviewer_radar": 600,  # radar frames every 10 minutes
    "usgs_eq": 60,  # feed updated every minute
    "emsc_seismic": 60,  # not verified
    "gdacs_global": 180,  # not verified
    "noaa_nws": 120,  # not verified
    "usgs_volcano_gvp": 600,  # not verified
    "noaa_swpc": 300,  # chart updated every minute, index with 3-hour intervals
}


def all_collectors() -> list[BaseCollector[Any]]:
    return [
        OpenMeteoCollector(),
        OpenMeteoAirQualityCollector(),
        OpenMeteoFloodCollector(),
        OpenMeteoMarineCollector(),
        MetNorwayCollector(),
        RainViewerCollector(),
        USGSCollector(),
        EMSCCollector(),
        GDACSCollector(),
        NOAANWSCollector(),
        USGSVolcanoCollector(),
        NOAASWPCCollector(),
    ]


def test_each_source_has_the_ttl_of_its_update_rhythm() -> None:
    assert {c.provider_id: c.ttl_seconds for c in all_collectors()} == EXPECTED_TTL_S


class Echo(BaseCollector[dict[str, Any]]):
    def __init__(self, provider: Provider, ttl_seconds: int) -> None:
        super().__init__("echo", "Echo", "test", ttl_seconds=ttl_seconds, http_client=provider.client())

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> dict[str, Any]:
        resp = await client.get("https://echo.test/data")
        return resp.json()  # type: ignore[no-any-return]


@pytest.mark.asyncio
async def test_a_value_is_fresh_for_exactly_its_ttl(sim_clock: SimClock) -> None:
    provider = Provider({"v": 1})
    collector = Echo(provider, ttl_seconds=100)

    await collector.fetch("k")
    sim_clock.advance(100.0)
    await collector.fetch("k")
    assert provider.count == 1
    sim_clock.advance(0.5)
    await collector.fetch("k")
    assert provider.count == 2


@pytest.mark.asyncio
async def test_a_call_can_store_its_value_with_another_ttl(sim_clock: SimClock) -> None:
    provider = Provider({"v": 1})
    collector = Echo(provider, ttl_seconds=100)

    await collector.fetch("slow", ttl_seconds=1000)
    await collector.fetch("default")
    sim_clock.advance(500.0)
    await collector.fetch("slow")
    await collector.fetch("default")
    assert provider.count == 3  # "default" expired (second request), "slow" did not
