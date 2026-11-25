"""What a computation read from the collectors' caches, to tell whether it would read the same again without sending a request.

A computation that reads many collectors (the world view of ``GET /v1/events``) is worked out again at every call, although
its inputs only change when a source is read again. While it runs inside ``watching()``, every ``BaseCollector.fetch_ex`` notes
which cache entry it served (``Witness.note``) and until when that entry is fresh. The answer can then be kept as long as
``Witness.still_holds()``: every entry noted is still the entry of its collector and still fresh (a new call would read no
provider and see the same objects), no deadline given by the computation itself (``until_wall``) has passed, and nothing was
read that cannot be reproduced (``taint``: a failed read that would be tried again, a partial read).
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Optional

from app import clock


class Witness:
    """The reads of one computation."""

    def __init__(self) -> None:
        # (collector, cache key, the entry served or ``None``, monotonic time until which that read repeats, circuit state to keep)
        self._reads: list[tuple[Any, str, Any, float, Any]] = []
        self._wall_deadline = math.inf
        self.reusable = True

    def note(self, collector: Any, key: str, entry: Any, until_monotonic: float, circuit: Any = None) -> None:
        """A read of ``collector`` for ``key`` returned ``entry`` (``None``: nothing cached) and repeats until ``until_monotonic``."""
        self._reads.append((collector, key, entry, until_monotonic, circuit))

    def taint(self) -> None:
        """Something was read that a later call could read differently without any cache entry changing: nothing is reusable."""
        self.reusable = False

    def until_wall(self, deadline: float) -> None:
        """The answer stops being right after this wall-clock time (a warning that ends)."""
        self._wall_deadline = min(self._wall_deadline, deadline)

    def reads(self) -> int:
        return len(self._reads)

    def still_holds(self) -> bool:
        """True when a new computation would read the very same cache entries, with no request."""
        if not self.reusable or clock.wall() > self._wall_deadline:
            return False
        now = clock.monotonic()
        for collector, key, entry, until, circuit in self._reads:
            if now > until or collector._cache.get(key) is not entry:
                return False
            if circuit is not None and collector.circuit_state is not circuit:
                return False
        return True


_current: ContextVar[Optional[Witness]] = ContextVar("vigie_freshness_witness", default=None)


def current() -> Optional[Witness]:
    """The witness of the computation that is running, if any."""
    return _current.get()


@contextmanager
def watching() -> Iterator[Witness]:
    """Note, for the length of the block (and of the tasks it starts), what the collectors serve."""
    witness = Witness()
    token = _current.set(witness)
    try:
        yield witness
    finally:
        _current.reset(token)
