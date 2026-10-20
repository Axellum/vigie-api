"""The push stream: a hint to read again, with the poll as the fallback."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.vigie.api import VigieConnectionError
from custom_components.vigie.const import STREAM_MAX_DELAY_S, STREAM_MIN_DELAY_S, STREAM_RECHECK_PUSH_S
from custom_components.vigie.stream import VigieStream

from .conftest import setup_entry
from .payloads import HEALTH_NO_PUSH, HEALTH_PUSH, URL, response, vigilance

LAT, LON = 45.0, 5.0


def text(**message: Any) -> SimpleNamespace:
    return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(message))


def push(lat: float = LAT, lon: float = LON, **point: Any) -> SimpleNamespace:
    return text(event="vigilance_push", change="appeared", point={"lat": lat, "lon": lon, **point})


class FakeSocket:
    """A WebSocket that says what it is told to, then closes (or waits for ever)."""

    def __init__(self, messages: list[Any], then_wait: asyncio.Event | None = None, stays_s: float = 0.0) -> None:
        self.sent: list[dict[str, Any]] = []
        self._messages = messages
        self._wait = then_wait
        self._stays_s = stays_s

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)

    async def __aiter__(self):  # noqa: D105
        if self._stays_s:
            await asyncio.sleep(self._stays_s)
        for message in self._messages:
            yield message
        if self._wait is not None:
            await self._wait.wait()
            yield push()


class FakeConnection:
    def __init__(self, socket: FakeSocket | None = None, error: Exception | None = None) -> None:
        self.socket, self.error = socket, error

    async def __aenter__(self) -> FakeSocket:
        if self.error is not None:
            raise self.error
        assert self.socket is not None
        return self.socket

    async def __aexit__(self, *exc: object) -> None:
        return None


class FakeClient:
    base_url = URL

    def __init__(self, healths: list[Any], connections: list[FakeConnection]) -> None:
        self._healths, self._connections = list(healths), list(connections)

    async def health(self) -> dict[str, Any]:
        item = self._healths.pop(0) if len(self._healths) > 1 else self._healths[0]
        if isinstance(item, Exception):
            raise item
        return item

    def connect_stream(self) -> FakeConnection:
        return self._connections.pop(0)


class Stopped(Exception):
    """Ends `run()` in a test."""


class ScriptedStream(VigieStream):
    """A stream whose waits are recorded, and that stops after a number of them."""

    def __init__(self, *args: Any, stop_after: int, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.waits: list[float] = []
        self._stop_after = stop_after

    async def _sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        if len(self.waits) >= self._stop_after:
            raise Stopped


async def run(stream: ScriptedStream) -> None:
    with pytest.raises(Stopped):
        await stream.run()


async def test_an_instance_without_a_scheduler_is_not_followed() -> None:
    """Without `VIGIE_SCHEDULER=1` the instance never pushes (and a connection would make it build its whole snapshot)."""
    calls: list[str] = []

    async def on_push() -> None:
        calls.append("push")

    client = FakeClient([HEALTH_NO_PUSH], [])
    stream = ScriptedStream(client, LAT, LON, on_push, stop_after=2)  # type: ignore[arg-type]
    await run(stream)
    assert stream.state == "not_offered" and stream.waits == [STREAM_RECHECK_PUSH_S, STREAM_RECHECK_PUSH_S] and calls == []


async def test_it_follows_the_place_and_reads_again_on_a_push_for_that_place_only() -> None:
    calls: list[str] = []

    async def on_push() -> None:
        calls.append("push")

    socket = FakeSocket(
        [
            text(event="initial_snapshot", hazards_count=0),
            text(event="vigilance_state", point={"lat": LAT, "lon": LON}, vigilances=[]),
            push(),  # ours
            push(48.0, 2.0),  # another place
            push(LAT, LON, zone_id="paris", zone_name="Paris"),  # a watched zone of the instance with our coordinates: still ours
            push(LAT + 0.00004, LON - 0.00004),  # within the four decimals the instance rounds to
            push(LAT + 0.001, LON),  # about 100 m away: not ours
            SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data="not json"),
            text(event="vigilance_push", point="oops"),
            text(event="vigilance_push", point={"lat": "45", "lon": "5"}),
            text(event="alert_push"),
        ]
    )
    client = FakeClient([HEALTH_PUSH], [FakeConnection(socket)])
    stream = ScriptedStream(client, LAT, LON, on_push, stop_after=1)  # type: ignore[arg-type]
    await run(stream)
    assert socket.sent == [{"action": "follow", "lat": LAT, "lon": LON}]
    assert calls == ["push", "push", "push"] and stream.pushes_received == 3
    assert stream.state == "reconnecting" and stream.last_error == "the instance closed the stream"


async def test_a_failing_connection_is_retried_with_a_growing_delay_up_to_a_cap() -> None:
    async def on_push() -> None:
        return None

    errors = [FakeConnection(error=aiohttp.ClientConnectionError("refused")) for _ in range(9)]
    stream = ScriptedStream(FakeClient([HEALTH_PUSH], errors), LAT, LON, on_push, stop_after=9)  # type: ignore[arg-type]
    await run(stream)
    bases = [STREAM_MIN_DELAY_S * 2**i for i in range(9)]
    for wait, base in zip(stream.waits, bases, strict=True):
        base = min(base, STREAM_MAX_DELAY_S)
        assert base <= wait <= base * 1.25
    assert stream.waits[-1] >= STREAM_MAX_DELAY_S  # capped at five minutes, not growing for ever
    assert stream.last_error and "ClientConnectionError" in stream.last_error


async def test_an_instance_that_cannot_be_asked_is_retried() -> None:
    async def on_push() -> None:
        return None

    stream = ScriptedStream(FakeClient([VigieConnectionError("down")], []), LAT, LON, on_push, stop_after=2)  # type: ignore[arg-type]
    await run(stream)
    assert stream.state == "reconnecting" and "VigieConnectionError" in (stream.last_error or "")
    assert len(stream.waits) == 2 and stream.waits[1] > stream.waits[0]


async def test_a_connection_that_lasted_resets_the_delay() -> None:
    async def on_push() -> None:
        return None

    connections = [
        FakeConnection(error=aiohttp.ClientConnectionError("refused")),
        FakeConnection(error=aiohttp.ClientConnectionError("refused")),
        FakeConnection(FakeSocket([], stays_s=0.05)),  # connects, stays a moment, closes
        FakeConnection(error=aiohttp.ClientConnectionError("refused")),
    ]
    stream = ScriptedStream(FakeClient([HEALTH_PUSH], connections), LAT, LON, on_push, stop_after=4)  # type: ignore[arg-type]
    with patch("custom_components.vigie.stream.STREAM_STABLE_AFTER_S", 0.02):  # a connection that stayed 50 ms has lasted
        await run(stream)
    # 5, 10 (two failures), then the connection that lasted: back to the minimum.
    assert STREAM_MIN_DELAY_S * 2 <= stream.waits[1]
    assert stream.waits[2] <= STREAM_MIN_DELAY_S * 1.25


async def test_a_push_makes_the_entry_read_again_before_its_interval(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    release = asyncio.Event()
    mock_instance(response([]), health=HEALTH_PUSH)
    with patch("custom_components.vigie.api.VigieClient.connect_stream", lambda self: FakeConnection(FakeSocket([], release))):
        await setup_entry(hass, mock_entry)
        for _ in range(5):
            await asyncio.sleep(0)
        assert mock_entry.runtime_data.stream.state == "connected"
        assert hass.states.get("sensor.vigie_test_maximum_level").state == "none"

        mock_instance(response([vigilance(level="RED")]), health=HEALTH_PUSH)
        release.set()  # the instance says something changed for the place
        await hass.async_block_till_done()
        assert hass.states.get("sensor.vigie_test_maximum_level").state == "red"  # no five minutes of waiting
        assert mock_entry.runtime_data.stream.pushes_received == 1
        reads = [call for call in aioclient_mock.mock_calls if call[1].path == "/v1/vigilances"]
        assert len(reads) == 1  # (the mock was reset by the second `mock_instance`)


async def test_when_the_stream_fails_the_poll_goes_on(
    hass: HomeAssistant, mock_instance: Callable[..., None], mock_entry: MockConfigEntry
) -> None:
    mock_instance(response([]), health=HEALTH_PUSH)

    async def no_wait(self: VigieStream, seconds: float) -> None:
        await asyncio.sleep(3600)  # the retry waits; the test never lets it come

    with (
        patch("custom_components.vigie.api.VigieClient.connect_stream", lambda self: FakeConnection(error=aiohttp.ClientConnectionError("refused"))),
        patch.object(VigieStream, "_sleep", no_wait),
    ):
        await setup_entry(hass, mock_entry)
        for _ in range(5):
            await asyncio.sleep(0)
        assert mock_entry.runtime_data.stream.state == "reconnecting"
        assert hass.states.get("sensor.vigie_test_maximum_level").state == "none"

        mock_instance(response([vigilance(level="ORANGE")]), health=HEALTH_PUSH)
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=6))
        await hass.async_block_till_done()
        assert hass.states.get("sensor.vigie_test_maximum_level").state == "orange"  # read by the poll, without any push
