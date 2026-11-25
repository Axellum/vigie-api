"""What a computation read from the collectors' caches, to tell whether it would read the same again without sending a request.

A computation that reads many collectors (the world view of ``GET /v1/events``) is worked out again at every call, although
its inputs only change when a source is read again. While it runs inside ``watching()``, every ``BaseCollector.fetch_ex`` notes
which cache entry it served (``Witness.note``) and until when that entry is fresh. The answer can then be kept as long as
``Witness.still_holds()``: every entry noted is still the entry of its collector and still fresh (a new call would read no
provider and see the same objects), no deadline given by the computation itself (``until_wall``) has passed, and nothing was
read that cannot be reproduced (``taint``: a failed read that would be tried again, a partial read). ``why_not()`` says which of
these failed (with the provider, never the cache key: a key can hold a position).
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
        self.tainted_by: Optional[str] = None  # what made it not reusable first

    def note(self, collector: Any, key: str, entry: Any, until_monotonic: float, circuit: Any = None) -> None:
        """A read of ``collector`` for ``key`` returned ``entry`` (``None``: nothing cached) and repeats until ``until_monotonic``.

        ``circuit``: what ``collector._circuit_token(key)`` was when the read failed (``None`` for a read that did not fail); the read
        repeats only while it is the same object.
        """
        self._reads.append((collector, key, entry, until_monotonic, circuit))

    def taint(self, by: str) -> None:
        """Something was read that a later call could read differently without any cache entry changing: nothing is reusable."""
        if self.reusable:
            self.tainted_by = by
        self.reusable = False

    def until_wall(self, deadline: float) -> None:
        """The answer stops being right after this wall-clock time (a warning that ends)."""
        self._wall_deadline = min(self._wall_deadline, deadline)

    def reads(self) -> int:
        return len(self._reads)

    def still_holds(self) -> bool:
        """True when a new computation would read the very same cache entries, with no request."""
        return self.why_not() is None

    def why_not(self) -> Optional[str]:
        """``None`` while ``still_holds()``; else the first reason it does not (``"expired: usgs"``, ``"a warning ended"``...)."""
        if not self.reusable:
            return self.tainted_by or "a read that cannot be repeated"
        if clock.wall() > self._wall_deadline:
            return "a warning ended"
        now = clock.monotonic()
        for collector, key, entry, until, circuit in self._reads:
            if now > until:
                return f"expired: {collector.provider_id}"
            if collector._cache.get(key) is not entry:
                return f"read again: {collector.provider_id}"
            if circuit is not None and collector._circuit_token(key) is not circuit:
                return f"circuit changed: {collector.provider_id}"
        return None


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
