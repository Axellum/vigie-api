"""Concurrent requests for the same cache key produce one upstream request."""

from __future__ import annotations

import asyncio
import gc
import time
from typing import Any

import httpx
import pytest

from app.collectors.base import BaseCollector, CollectorUnavailableError
from app.models.schemas import CircuitState
from tests.helpers import Provider


class Echo(BaseCollector[dict[str, Any]]):
    """A minimal collector: one GET, the JSON answer is the value."""

    def __init__(self, provider: Provider, **kwargs: Any) -> None:
        super().__init__(
            provider_id="echo", name="Echo", domain="test", ttl_seconds=60, http_client=provider.client(), **kwargs
        )

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> dict[str, Any]:
        resp = await client.get("https://echo.test/data", params={"key": str(kwargs.get("key", ""))})
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]


def failing_provider() -> Provider:
    return Provider(answer=lambda request: httpx.Response(500, json={"error": "boom"}))


@pytest.mark.asyncio
async def test_many_simultaneous_requests_send_one_upstream_request() -> None:
    provider = Provider({"value": 1})
    collector = Echo(provider)

    results = await asyncio.gather(*(collector.fetch("same") for _ in range(200)))

    assert provider.count == 1
    assert all(result is results[0] for result in results)
    assert collector._inflight == {}


@pytest.mark.asyncio
async def test_different_keys_are_not_coalesced() -> None:
    provider = Provider({"value": 1})
    collector = Echo(provider)

    await asyncio.gather(
        *(collector.fetch("a", key="a") for _ in range(10)),
        *(collector.fetch("b", key="b") for _ in range(10)),
    )

    assert provider.count == 2
    assert sorted(request.url.params["key"] for request in provider.requests) == ["a", "b"]


@pytest.mark.asyncio
async def test_an_error_reaches_every_waiter_and_is_counted_once() -> None:
    provider = failing_provider()
    collector = Echo(provider)

    results = await asyncio.gather(*(collector.fetch("same") for _ in range(50)), return_exceptions=True)

    assert provider.count == 1
    assert all(isinstance(result, CollectorUnavailableError) for result in results)
    assert collector.consecutive_failures == 1  # 50 callers, one failed request
    assert collector.circuit_state == CircuitState.CLOSED
    assert collector._inflight == {}

    # The failure is not remembered as an answer: the next call tries again.
    with pytest.raises(CollectorUnavailableError):
        await collector.fetch("same")
    assert provider.count == 2


@pytest.mark.asyncio
async def test_each_waiter_applies_its_own_stale_policy() -> None:
    provider = failing_provider()
    collector = Echo(provider)
    collector._cache["same"] = (time.monotonic() - 10_000.0, {"value": "old"})  # expired long ago

    results = await asyncio.gather(
        collector.fetch("same", allow_stale_on_error=True),
        collector.fetch("same", allow_stale_on_error=False),
        collector.fetch("same"),
        return_exceptions=True,
    )

    assert provider.count == 1
    assert results[0] == {"value": "old"}
    assert isinstance(results[1], CollectorUnavailableError)
    assert results[2] == {"value": "old"}  # stale on error is the default


@pytest.mark.asyncio
async def test_a_cancelled_caller_does_not_break_the_others() -> None:
    provider = Provider({"value": 1})
    collector = Echo(provider)

    callers = [asyncio.create_task(collector.fetch("same")) for _ in range(5)]
    await asyncio.sleep(0)  # the first caller has started the request
    callers[0].cancel()
    results = await asyncio.gather(*callers, return_exceptions=True)

    assert isinstance(results[0], asyncio.CancelledError)
    assert results[1:] == [{"value": 1}] * 4
    assert provider.count == 1
    assert collector._inflight == {}


@pytest.mark.asyncio
async def test_the_answer_is_cached_even_when_every_caller_gave_up() -> None:
    provider = Provider({"value": 1})
    collector = Echo(provider)

    callers = [asyncio.create_task(collector.fetch("same")) for _ in range(3)]
    await asyncio.sleep(0)
    for caller in callers:
        caller.cancel()
    await asyncio.gather(*callers, return_exceptions=True)
    for _ in range(5):  # let the upstream request finish on its own
        await asyncio.sleep(0)

    assert collector._inflight == {}
    assert await collector.fetch("same") == {"value": 1}
    assert provider.count == 1


@pytest.mark.asyncio
async def test_no_unretrieved_error_when_every_caller_gave_up() -> None:
    loop = asyncio.get_running_loop()
    problems: list[dict[str, Any]] = []
    loop.set_exception_handler(lambda _loop, context: problems.append(context))
    collector = Echo(failing_provider())

    callers = [asyncio.create_task(collector.fetch("same")) for _ in range(3)]
    await asyncio.sleep(0)
    for caller in callers:
        caller.cancel()
    await asyncio.gather(*callers, return_exceptions=True)
    for _ in range(5):
        await asyncio.sleep(0)
    del callers
    gc.collect()

    assert problems == []
    assert collector._inflight == {}


@pytest.mark.asyncio
async def test_a_request_left_by_another_event_loop_is_ignored() -> None:
    provider = Provider({"value": 1})
    collector = Echo(provider)
    other_loop = asyncio.new_event_loop()
    try:
        collector._inflight["same"] = other_loop.create_future()  # type: ignore[assignment]

        assert await collector.fetch("same") == {"value": 1}
    finally:
        other_loop.close()
    assert provider.count == 1


@pytest.mark.asyncio
async def test_one_probe_when_the_circuit_is_half_open() -> None:
    provider = Provider({"value": 1})
    collector = Echo(provider, recovery_timeout_seconds=30)
    collector.circuit_state = CircuitState.OPEN
    collector.opened_at_monotonic = time.monotonic() - 60.0  # the recovery delay has passed

    results = await asyncio.gather(*(collector.fetch("same") for _ in range(20)))

    assert provider.count == 1
    assert results == [{"value": 1}] * 20
    assert collector.circuit_state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_force_refresh_joins_a_request_already_running() -> None:
    provider = Provider({"value": 1})
    collector = Echo(provider)

    await asyncio.gather(collector.fetch("same"), collector.fetch("same", force_refresh=True))
    assert provider.count == 1

    await collector.fetch("same", force_refresh=True)  # nothing running: a new request
    assert provider.count == 2
