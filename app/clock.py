"""Time source of the collectors, replaceable by a test.

The collectors measure their cache lifetimes with a monotonic clock and compare the HTTP dates of the
providers (``Expires``, ``Date``) with the wall clock. Both come from here, so that a test can play
a whole day in a few seconds with a simulated clock instead of sleeping.

The same module runs the work that would hold the event loop (parsing a 5 MB CSV, counting thousands of detections) in a thread. A
thread takes real time that a simulated day does not wait for, so the simulation replaces the runner with one that works in place.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

_monotonic: Callable[[], float] = time.monotonic
_wall: Callable[[], float] = time.time
_blocking: Callable[..., Awaitable[Any]] = asyncio.to_thread


def monotonic() -> float:
    """Seconds on a clock that never goes back (cache lifetimes, circuit breaker)."""
    return _monotonic()


def wall() -> float:
    """Seconds since the Unix epoch (dates exchanged with the providers)."""
    return _wall()


async def run_blocking(function: Callable[..., Any], *args: Any) -> Any:
    """``function(*args)`` in a thread (the event loop stays free), unless a test replaced the runner."""
    return await _blocking(function, *args)
