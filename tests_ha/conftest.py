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

from .payloads import HEALTH_NO_PUSH, URL, response

FIXTURES = Path(__file__).parent / "fixtures"
HOME = {CONF_LATITUDE: 45.0, CONF_LONGITUDE: 5.0}


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
def mock_instance(aioclient_mock: AiohttpClientMocker) -> Callable[..., None]:
    """Make the instance answer ``payload`` on `/v1/vigilances` (and `health` on `/v1/health`); call again to change it."""

    def mock(payload: Any = None, *, health: dict[str, Any] | None = None, status: int = 200) -> None:
        aioclient_mock.clear_requests()
        aioclient_mock.get(f"{URL}/v1/health", json=HEALTH_NO_PUSH if health is None else health)
        aioclient_mock.get(f"{URL}/v1/vigilances", json=response() if payload is None else payload, status=status)

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
