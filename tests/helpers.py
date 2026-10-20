"""Helpers shared by the tests: a simulated provider behind ``httpx.MockTransport``."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Optional

import httpx


class Provider:
    """A simulated provider: answers from a function and records every request it receives.

    ``answer`` receives the request and returns the response; by default every request is answered with
    ``payload`` as JSON. The answer takes ``latency_ticks`` event-loop turns, so that concurrent
    requests overlap as they would on a network.
    """

    def __init__(
        self,
        payload: Any = None,
        answer: Optional[Callable[[httpx.Request], httpx.Response]] = None,
        latency_ticks: int = 2,
    ) -> None:
        self.payload = payload
        self.answer = answer
        self.latency_ticks = latency_ticks
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for _ in range(self.latency_ticks):
            await asyncio.sleep(0)
        if self.answer is not None:
            return self.answer(request)
        return httpx.Response(200, json=self.payload)

    @property
    def count(self) -> int:
        """Number of requests received."""
        return len(self.requests)

    def client(self) -> httpx.AsyncClient:
        """An ``httpx.AsyncClient`` whose requests are answered by this provider."""
        return httpx.AsyncClient(transport=httpx.MockTransport(self))

    def query(self, index: int = -1) -> dict[str, str]:
        """The query parameters of the ``index``-th request."""
        return dict(self.requests[index].url.params)
