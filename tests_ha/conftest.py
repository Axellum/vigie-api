"""Shared fixtures of the Home Assistant tests."""

from __future__ import annotations

import json
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, CONF_URL
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.vigie.const import DOMAIN

from .payloads import HEALTH_NO_PUSH, URL, earthquakes_response, prealert_response, response

FIXTURES = Path(__file__).parent / "fixtures"
HOME = {CONF_LATITUDE: 45.0, CONF_LONGITUDE: 5.0}
BIARRITZ = {CONF_LATITUDE: 43.48, CONF_LONGITUDE: -1.56}

# Real answers of the public instance, read on 2026-11-22 (see fixtures/README.md).
POINT = "point_biarritz_2026-11-22.json"
TIDES = "tides_biarritz_2026-11-22.json"
NORMALS = "normals_biarritz_2026-11-22.json"
STATION_WIND = "stationwind_biarritz_2026-11-22.json"
SPOTS = "spots_2026-11-22.json"
SPOT = "biarritz-plage-des-corsaires"
ACTIVITIES = f"activities_{SPOT}_2026-11-22.json"


def recorded(name: str) -> Any:
    """A recorded answer of the fixtures folder."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load `custom_components/vigie` from the repository."""


@pytest.fixture
def haute_corse() -> dict[str, Any]:
    """A real answer of a live instance (2026-10-20): seven MeteoAlarm warnings, one of them red."""
    return json.loads((FIXTURES / "vigilances_haute_corse.json").read_text(encoding="utf-8"))


@pytest.fixture
def mock_entry() -> MockConfigEntry:
    """An entry for a place of the repository's tests."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Vigie Test",
        unique_id=f"{URL}|45.0000|5.0000",
        data={CONF_URL: URL, **HOME},
    )


@pytest.fixture
def biarritz_entry() -> MockConfigEntry:
    """An entry for Biarritz, the place of the recorded weather answers (a spot is within reach)."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Vigie Biarritz",
        unique_id=f"{URL}|43.4800|-1.5600",
        data={CONF_URL: URL, **BIARRITZ},
    )


@pytest.fixture
def mock_instance(aioclient_mock: AiohttpClientMocker) -> Callable[..., None]:
    """Make the instance answer ``payload`` on `/v1/vigilances`, `quakes` on `/v1/earthquakes` (no earthquake by default) and
    `health` on `/v1/health`, and `prealert` on `/v1/experimental/prealert` (no level by default); call again to change it."""

    def mock(
        payload: Any = None,
        *,
        health: dict[str, Any] | None = None,
        status: int = 200,
        quakes: Any = None,
        quakes_status: int = 200,
        prealert: Any = None,
        prealert_status: int = 200,
        point: Any = None,
        point_status: int = 200,
        tides: Any = None,
        tides_status: int = 200,
        normals: Any = None,
        station_wind: Any = None,
        spots: Any = None,
        activities: Any = None,
    ) -> None:
        aioclient_mock.clear_requests()
        aioclient_mock.get(f"{URL}/v1/health", json=HEALTH_NO_PUSH if health is None else health)
        aioclient_mock.get(f"{URL}/v1/vigilances", json=response() if payload is None else payload, status=status)
        aioclient_mock.get(f"{URL}/v1/earthquakes", json=earthquakes_response() if quakes is None else quakes, status=quakes_status)
        aioclient_mock.get(f"{URL}/v1/experimental/prealert", json=prealert_response() if prealert is None else prealert, status=prealert_status)
        # The weather of the place and the card's data (option "Weather and card", on by default): real answers for Biarritz.
        aioclient_mock.get(f"{URL}/v1/point", json=recorded(POINT) if point is None else point, status=point_status)
        aioclient_mock.get(f"{URL}/v1/tides", json=recorded(TIDES) if tides is None else tides, status=tides_status)
        aioclient_mock.get(f"{URL}/v1/climate/normals", json=recorded(NORMALS) if normals is None else normals)
        aioclient_mock.get(f"{URL}/v1/stations/wind", json=recorded(STATION_WIND) if station_wind is None else station_wind)
        aioclient_mock.get(f"{URL}/v1/surf/spots", json=recorded(SPOTS) if spots is None else spots)
        aioclient_mock.get(f"{URL}/v1/activities/{SPOT}", json=recorded(ACTIVITIES) if activities is None else activities)

    return mock


@pytest.fixture
def no_stream() -> Generator[None]:
    """Keep the push stream out of the way: the stream has its own tests."""

    async def idle(self: object) -> None:
        return None

    with patch("custom_components.vigie.stream.VigieStream.run", idle):
        yield


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add the entry and set it up."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
