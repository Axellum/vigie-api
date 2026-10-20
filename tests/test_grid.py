"""Point grids: the upstream request and the cache key use a snapped point; the client keeps its own."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.collectors.flood_air import OpenMeteoAirQualityCollector, OpenMeteoFloodCollector
from app.collectors.grid import coord_str, grid_step_from_env, snap_to_grid
from app.collectors.marine import OpenMeteoMarineCollector
from app.collectors.seismic import USGSCollector
from app.collectors.weather import MetNorwayCollector, OpenMeteoCollector
from app.services.geodesy import haversine_km
from tests import samples
from tests.helpers import Provider, make_service

PARIS = (48.8566, 2.3522)


# ============================================================================
# The grid itself
# ============================================================================


def test_snap_to_grid_picks_the_nearest_multiple() -> None:
    assert snap_to_grid(48.8566, 2.3522, 0.05) == (48.85, 2.35)
    assert snap_to_grid(48.8566, 2.3522, 0.1) == (48.9, 2.4)
    assert snap_to_grid(-33.8688, 151.2093, 0.05) == (-33.85, 151.2)
    # 0.0249 is closer to 0.00 than to 0.05
    assert snap_to_grid(0.0249, -0.0249, 0.05) == (0.0, 0.0)


def test_snap_to_grid_has_no_negative_zero_and_stays_on_the_globe() -> None:
    lat, lon = snap_to_grid(-0.001, -0.001, 0.05)
    assert (lat, lon) == (0.0, 0.0)
    assert coord_str(lat) == "0" and coord_str(lon) == "0"
    assert snap_to_grid(89.99, 0.0, 0.07)[0] <= 90.0
    assert snap_to_grid(-89.99, 0.0, 0.07)[0] >= -90.0
    assert -180.0 <= snap_to_grid(0.0, 179.99, 0.07)[1] <= 180.0
    assert -180.0 <= snap_to_grid(0.0, -179.99, 0.07)[1] <= 180.0


def test_two_points_a_few_kilometres_apart_share_a_cell() -> None:
    a = snap_to_grid(48.8566, 2.3522, 0.05)
    b = snap_to_grid(48.8710, 2.3300, 0.05)  # about 2.2 km north-west
    assert a == b
    assert snap_to_grid(48.95, 2.35, 0.05) != a  # about 10 km north


def test_coord_str_has_at_most_four_decimals() -> None:
    assert coord_str(48.85) == "48.85"
    assert coord_str(2.0) == "2"
    assert coord_str(-61.5) == "-61.5"
    assert coord_str(snap_to_grid(48.8566, 2.3522, 0.05)[0]) == "48.85"
    assert coord_str(0.1 + 0.2) == "0.3"  # no floating point noise in a URL


@pytest.mark.parametrize("raw", ["", "abc", "0", "-0.1", "nan", "inf", "0.00001", "11"])
def test_unusable_grid_setting_falls_back_to_the_default(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("VIGIE_GRID_TEST_DEG", raw)
    assert grid_step_from_env("VIGIE_GRID_TEST_DEG", 0.05) == 0.05


def test_usable_grid_setting_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIGIE_GRID_TEST_DEG", " 0.25 ")
    assert grid_step_from_env("VIGIE_GRID_TEST_DEG", 0.05) == 0.25
    monkeypatch.delenv("VIGIE_GRID_TEST_DEG")
    assert grid_step_from_env("VIGIE_GRID_TEST_DEG", 0.05) == 0.05


# ============================================================================
# Defaults and settings of the collectors
# ============================================================================


def test_default_grids(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "VIGIE_GRID_FORECAST_DEG",
        "VIGIE_GRID_AIR_QUALITY_DEG",
        "VIGIE_GRID_FLOOD_DEG",
        "VIGIE_GRID_MARINE_DEG",
    ):
        monkeypatch.delenv(name, raising=False)
    assert OpenMeteoCollector().grid_deg == 0.05
    assert MetNorwayCollector().grid_deg == 0.05
    assert OpenMeteoAirQualityCollector().grid_deg == 0.1
    assert OpenMeteoFloodCollector().grid_deg == 0.1
    assert OpenMeteoMarineCollector().grid_deg == 0.1


def test_grids_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIGIE_GRID_FORECAST_DEG", "0.1")
    monkeypatch.setenv("VIGIE_GRID_AIR_QUALITY_DEG", "0.25")
    monkeypatch.setenv("VIGIE_GRID_FLOOD_DEG", "0.2")
    monkeypatch.setenv("VIGIE_GRID_MARINE_DEG", "0.5")
    assert OpenMeteoCollector().grid_deg == 0.1
    assert MetNorwayCollector().grid_deg == 0.1
    assert OpenMeteoAirQualityCollector().grid_deg == 0.25
    assert OpenMeteoFloodCollector().grid_deg == 0.2
    assert OpenMeteoMarineCollector().grid_deg == 0.5
    assert OpenMeteoCollector(grid_deg=0.02).grid_deg == 0.02  # the constructor wins


# ============================================================================
# What goes upstream, what the client gets
# ============================================================================


@pytest.mark.asyncio
async def test_forecast_request_and_cache_use_the_snapped_point() -> None:
    provider = Provider(samples.open_meteo_payload())
    collector = OpenMeteoCollector(http_client=provider.client(), grid_deg=0.05)

    await collector.fetch_point(48.8566, 2.3522)
    assert provider.count == 1
    assert provider.query()["latitude"] == "48.85"
    assert provider.query()["longitude"] == "2.35"

    # A client 2 km away, and one 400 m away, are served by the same cache entry.
    await collector.fetch_point(48.8710, 2.3300)
    await collector.fetch_point(48.8590, 2.3560)
    assert provider.count == 1

    # A client in the next cell (about 10 km) costs a call.
    await collector.fetch_point(48.95, 2.35)
    assert provider.count == 2


@pytest.mark.asyncio
async def test_each_data_type_uses_its_own_grid() -> None:
    forecast = Provider(samples.open_meteo_payload())
    air = Provider({"current": {"european_aqi": 31, "pm10": 14.0}})
    flood = Provider({"daily": {"river_discharge": [42.0], "river_discharge_mean": [40.0], "river_discharge_max": [46.0]}})
    marine = Provider(samples.marine_payload())

    forecast_collector = OpenMeteoCollector(http_client=forecast.client())
    air_collector = OpenMeteoAirQualityCollector(http_client=air.client())
    flood_collector = OpenMeteoFloodCollector(http_client=flood.client())
    marine_collector = OpenMeteoMarineCollector(http_client=marine.client())

    lat, lon = PARIS
    await forecast_collector.fetch_point(lat, lon)
    await air_collector.fetch_air_quality(lat, lon)
    await flood_collector.fetch_flood_risk(lat, lon)
    await marine_collector.fetch_marine(lat, lon)

    assert (forecast.query()["latitude"], forecast.query()["longitude"]) == ("48.85", "2.35")
    for provider in (air, flood, marine):
        assert (provider.query()["latitude"], provider.query()["longitude"]) == ("48.9", "2.4")

    # 7 km from Paris: another forecast cell (0.05 degree) but the same air / flood / marine cell (0.1 degree).
    near_lat, near_lon = 48.8566, 2.4400
    await forecast_collector.fetch_point(near_lat, near_lon)
    await air_collector.fetch_air_quality(near_lat, near_lon)
    await flood_collector.fetch_flood_risk(near_lat, near_lon)
    await marine_collector.fetch_marine(near_lat, near_lon)
    assert forecast.count == 2
    assert air.count == 1 and flood.count == 1 and marine.count == 1


@pytest.mark.asyncio
async def test_the_response_keeps_the_exact_position_and_distances_use_it(tmp_path: Path) -> None:
    hazard_lat, hazard_lon = 48.2, 2.9
    usgs = Provider(
        {
            "features": [
                {
                    "id": "us1",
                    "properties": {"mag": 5.4, "place": "Somewhere", "time": 1792334500000, "tsunami": 0},
                    "geometry": {"coordinates": [hazard_lon, hazard_lat, 10.0]},
                }
            ]
        }
    )
    forecast = Provider(samples.open_meteo_payload())
    service = make_service(
        tmp_path / "grid.db",
        usgs=USGSCollector(http_client=usgs.client()),
        open_meteo=OpenMeteoCollector(http_client=forecast.client()),
    )
    lat, lon = PARIS

    point = await service.build_point_response(lat=lat, lon=lon, radius_km=500.0)

    assert forecast.query()["latitude"] == "48.85"  # the snapped point went upstream
    assert point.coordinates.lat == round(lat, 4) and point.coordinates.lon == round(lon, 4)  # the exact one is reported
    assert len(point.nearby_hazards) == 1
    assert point.nearby_hazards[0].distance_to_zone_km == haversine_km(lat, lon, hazard_lat, hazard_lon)
    assert point.nearby_hazards[0].distance_to_zone_km != haversine_km(48.85, 2.35, hazard_lat, hazard_lon)
