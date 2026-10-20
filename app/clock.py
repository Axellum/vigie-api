"""Time source of the collectors, replaceable by a test.

The collectors measure their cache lifetimes with a monotonic clock and compare the HTTP dates of the
providers (``Expires``, ``Date``) with the wall clock. Both come from here, so that a test can play
a whole day in a few seconds with a simulated clock instead of sleeping.
"""

from __future__ import annotations

import time
from typing import Callable

_monotonic: Callable[[], float] = time.monotonic
_wall: Callable[[], float] = time.time


def monotonic() -> float:
    """Seconds on a clock that never goes back (cache lifetimes, circuit breaker)."""
    return _monotonic()


def wall() -> float:
    """Seconds since the Unix epoch (dates exchanged with the providers)."""
    return _wall()
