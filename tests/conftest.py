"""Shared test configuration.

- No test may reach the network: every ``httpx.AsyncClient`` request is refused (only an
  ``httpx.MockTransport``, which answers from Python, is let through), so a collector that is
  not mocked behaves like an unavailable provider.
- The application database is a temporary file, never the developer's ``vigie.db``.
- ``world`` gives a ready-to-use application with every provider mocked by sample data.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

# Must be set before ``app.main`` is imported: it builds the aggregator at import time.
_TEST_DIR = Path(tempfile.mkdtemp(prefix="vigie-tests-"))
os.environ["VIGIE_DB_PATH"] = str(_TEST_DIR / "vigie-tests.db")
for _name in ("VIGIE_SCHEDULER", "VIGIE_CAP_SENDER", "VIGIE_CONTACT", "VIGIE_ENABLE_SIMULATION"):
    os.environ.pop(_name, None)

from app import clock as app_clock  # noqa: E402
from app.main import aggregator, app  # noqa: E402
from app.models.schemas import (  # noqa: E402
    AirQualityStatus,
    FloodRiskStatus,
    SeverityLevel,
    SimulationRequest,
    SimulationScenario,
    SpaceWeatherStatus,
)
from app.services.aggregator import AggregatorService  # noqa: E402
from app.services.storage_wal import SQLiteWALStorage  # noqa: E402
from tests import samples  # noqa: E402
from tests.helpers import SimClock  # noqa: E402

_REAL_SEND = httpx.AsyncClient.send


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse every outgoing HTTP request made with httpx, except those answered by ``httpx.MockTransport``.

    A ``MockTransport`` answers from Python and never opens a socket, so a test can simulate a provider
    with it. Any other client, including the ones the collectors create themselves, is refused.
    """

    async def _guard(self: httpx.AsyncClient, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        if isinstance(self._transport, httpx.MockTransport):
            return await _REAL_SEND(self, request, **kwargs)
        raise httpx.ConnectError("network access is disabled in tests", request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", _guard)


@pytest.fixture()
def sim_clock(monkeypatch: pytest.MonkeyPatch) -> SimClock:
    """A simulated clock installed as the time source of the collectors (cache lifetimes, HTTP dates)."""
    clock = SimClock()
    monkeypatch.setattr(app_clock, "_monotonic", clock.monotonic)
    monkeypatch.setattr(app_clock, "_wall", clock.time)
    return clock


@pytest.fixture()
def simulation_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable ``POST /v1/simulate`` for the test (it is off unless ``VIGIE_ENABLE_SIMULATION`` is set)."""
    monkeypatch.setenv("VIGIE_ENABLE_SIMULATION", "1")


@dataclass
class World:
    """The application under test with every provider mocked."""

    aggregator: AggregatorService
    client: TestClient
    weather: dict[str, Any]


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    """Application with an isolated database and sample data for every provider."""
    store = SQLiteWALStorage(db_path=tmp_path / "api.db")
    monkeypatch.setattr(aggregator, "storage", store)
    aggregator.refresh_zones_from_storage()
    aggregator.trigger_simulation(SimulationRequest(scenario=SimulationScenario.CLEAR))

    weather = samples.parsed_open_meteo()
    monkeypatch.setattr(aggregator.usgs, "fetch", AsyncMock(return_value=[]))
    monkeypatch.setattr(aggregator.emsc, "fetch", AsyncMock(return_value=[]))
    monkeypatch.setattr(aggregator.gdacs, "fetch", AsyncMock(return_value=[]))
    monkeypatch.setattr(aggregator.noaa_nws, "fetch", AsyncMock(return_value=[]))
    monkeypatch.setattr(aggregator.volcano, "fetch", AsyncMock(return_value=[]))
    monkeypatch.setattr(aggregator.open_meteo, "fetch_point", AsyncMock(return_value=weather))
    monkeypatch.setattr(aggregator.met_norway, "fetch_point", AsyncMock(side_effect=RuntimeError("down")))
    monkeypatch.setattr(
        aggregator.air_quality,
        "fetch_air_quality",
        AsyncMock(
            return_value=AirQualityStatus(
                european_aqi=31, us_aqi=42, pm2_5_ugm3=8.0, pm10_ugm3=14.0, uv_index=3.0, dust_ugm3=1.0
            )
        ),
    )
    monkeypatch.setattr(
        aggregator.flood,
        "fetch_flood_risk",
        AsyncMock(
            return_value=FloodRiskStatus(
                river_discharge_m3s=42.0,
                river_discharge_mean_m3s=40.0,
                river_discharge_max_m3s=46.0,
                anomaly_ratio=1.15,
                flood_alert_level=SeverityLevel.GREEN,
            )
        ),
    )
    monkeypatch.setattr(aggregator.marine, "fetch_marine", AsyncMock(return_value=samples.parsed_marine()))
    monkeypatch.setattr(
        aggregator.swpc,
        "fetch",
        AsyncMock(
            return_value=SpaceWeatherStatus(
                solar_kp=2.3, solar_storm_level="G0", gps_degraded=False, hf_radio_blackout_risk=False
            )
        ),
    )
    monkeypatch.setattr(
        aggregator.rainviewer,
        "fetch",
        AsyncMock(
            return_value={
                "radar_timestamp_utc": 1792334400,
                "radar_tile_url_template": "https://tilecache.rainviewer.com/v2/radar/1792334400/256/{z}/{x}/{y}/2/1_1.png",
            }
        ),
    )

    yield World(aggregator=aggregator, client=TestClient(app), weather=weather)
    aggregator.trigger_simulation(SimulationRequest(scenario=SimulationScenario.CLEAR))
