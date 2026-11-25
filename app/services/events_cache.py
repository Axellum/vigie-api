"""``GET /v1/events`` kept ready: the answer, already serialized, until something it was made from changes.

The world view reads about sixty sources, groups every warning and hazard, and writes several megabytes of JSON: 150 ms at every
call on the server, although its inputs only change when a source is read again (every few minutes at best). The answer is kept
for each set of parameters, and served again while ``freshness.Witness.still_holds()``: every cache entry the computation read is
still the entry of its collector and still fresh, so that computing it again would send no request and read the same objects. It is
dropped as soon as one of them is replaced or expires, when a warning ends (the answer holds only warnings in force), or when the
state that is not in a collector's cache changes (simulation, watched zones, settings, the registry read). A read that failed (and
would be tried again) or a partial read (documents still to be fetched) is never kept.

Calls that arrive while the answer is being worked out share that one computation. ``server_utc`` and ``fetched_age_s`` of
``coverage`` are those of the moment it was worked out.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass
from typing import Any

from app import freshness

MAX_ENTRIES = 8  # sets of parameters kept (the world, its compact form, a few countries)
MAX_BYTES = 64 * 1024 * 1024  # what all of them may weigh together (the world with every warning is about 6 MB)

State = tuple[Any, ...]


def same_state(a: State, b: State) -> bool:
    """True when every element is the same object as last time (or an equal number)."""
    return len(a) == len(b) and all(x is y or (isinstance(x, (int, bool)) and isinstance(y, (int, bool)) and x == y) for x, y in zip(a, b, strict=True))


@dataclass
class Kept:
    """An answer and what it was made from."""

    body: bytes
    witness: freshness.Witness
    state: State


class EventsAnswers:
    """The answers of ``GET /v1/events``, serialized, by set of parameters."""

    def __init__(self, max_entries: int = MAX_ENTRIES, max_bytes: int = MAX_BYTES) -> None:
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._kept: OrderedDict[Hashable, Kept] = OrderedDict()
        self._inflight: dict[Hashable, asyncio.Task[bytes]] = {}
        self.hits = 0  # answers served from what was kept
        self.misses = 0  # answers worked out
        self.not_kept = 0  # of those, the ones that could not be kept (a failed read, documents to come, the state moved)

    def __len__(self) -> int:
        return len(self._kept)

    def size_bytes(self) -> int:
        return sum(len(kept.body) for kept in self._kept.values())

    def stats(self) -> dict[str, int]:
        """What ``GET /v1/health`` says of it: how many answers are kept, what they weigh, and how the calls were served."""
        return {"kept": len(self._kept), "bytes": self.size_bytes(), "hits": self.hits, "misses": self.misses, "not_kept": self.not_kept}

    async def get(self, key: Hashable, state: Callable[[], State], work: Callable[[], Awaitable[bytes]]) -> bytes:
        """The answer for ``key``: the one kept when it still holds, else ``work()`` (once, whatever the number of callers)."""
        kept = self._kept.get(key)
        if kept is not None:
            if same_state(kept.state, state()) and kept.witness.still_holds():
                self._kept.move_to_end(key)
                self.hits += 1
                return kept.body
            del self._kept[key]
        loop = asyncio.get_running_loop()
        task = self._inflight.get(key)
        if task is not None and (task.done() or task.get_loop() is not loop):
            self._inflight.pop(key, None)  # a leftover of a closed event loop (a test client, a restarted server)
            task = None
        if task is None:
            self.misses += 1
            task = loop.create_task(self._compute(key, state, work))
            self._inflight[key] = task
            task.add_done_callback(lambda done, k=key: self._done(k, done))
        return await asyncio.shield(task)

    def _done(self, key: Hashable, task: asyncio.Task[bytes]) -> None:
        if self._inflight.get(key) is task:
            del self._inflight[key]
        if not task.cancelled():
            task.exception()  # every caller may have gone: the error is not "never retrieved"

    async def _compute(self, key: Hashable, state: Callable[[], State], work: Callable[[], Awaitable[bytes]]) -> bytes:
        before = state()
        with freshness.watching() as witness:
            body = await work()
        # Kept only if what it read could be read again as is: nothing replaced it meanwhile, nothing failed, no state moved.
        if witness.reusable and witness.reads() and same_state(before, state()) and witness.still_holds():
            self._kept[key] = Kept(body, witness, before)
            self._kept.move_to_end(key)
            while len(self._kept) > self.max_entries or (len(self._kept) > 1 and self.size_bytes() > self.max_bytes):
                self._kept.popitem(last=False)
        else:
            self.not_kept += 1
        return body
