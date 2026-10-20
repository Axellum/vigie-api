"""Base class of the external collectors: circuit breaker, TTL cache and metrics.

Guarantees invariant 1 of ``AGENTS.md``: a collector that times out or fails
(USGS, EMSC, GDACS, NOAA, Open-Meteo, MET Norway...) never blocks the asyncio
event loop and never makes the API fail. It keeps the last valid value in cache
and reports its health through ``get_health()``.
"""

from __future__ import annotations

import asyncio
import math
import os
import time
from abc import ABC, abstractmethod
from typing import Any, Generic, Optional, TypeVar

import httpx

from app.models.schemas import CircuitState, ProviderHealth

T = TypeVar("T")


def build_user_agent() -> str:
    """Build the User-Agent sent to the providers.

    Some providers (MET Norway, NOAA NWS) require an identifying User-Agent with a
    way to contact the operator. Set ``VIGIE_CONTACT`` (an e-mail address or a URL).
    """
    contact = os.environ.get("VIGIE_CONTACT", "").strip()
    return f"vigie-api ({contact})" if contact else "vigie-api (personal project)"


def optional_float(value: Any) -> Optional[float]:
    """Return ``value`` as a finite float, or ``None`` when it is missing or not a number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class CollectorUnavailableError(RuntimeError):
    """Raised when a collector fails and no cached fallback value is available."""


class BaseCollector(ABC, Generic[T]):
    """Resilient base for all asynchronous HTTP collectors."""

    def __init__(
        self,
        provider_id: str,
        name: str,
        domain: str,
        ttl_seconds: int = 120,
        failure_threshold: int = 3,
        recovery_timeout_seconds: int = 60,
        timeout_seconds: float = 6.0,
        is_fallback: bool = False,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.provider_id = provider_id
        self.name = name
        self.domain = domain
        self.ttl_seconds = ttl_seconds
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.timeout_seconds = timeout_seconds
        self.is_fallback = is_fallback
        self._external_client = http_client

        # Circuit breaker state
        self.circuit_state: CircuitState = CircuitState.CLOSED
        self.consecutive_failures: int = 0
        self.opened_at_monotonic: Optional[float] = None
        self.last_success_utc: Optional[int] = None
        self.last_error: Optional[str] = None
        self.latency_ms: float = 0.0

        # In-memory cache keyed by an arbitrary string (e.g. "global" or "48.86,2.35")
        self._cache: dict[str, tuple[float, T]] = {}
        # The upstream request being sent for each cache key (see ``_coalesced_upstream``)
        self._inflight: dict[str, asyncio.Task[T]] = {}

    def _get_cached_if_fresh(self, cache_key: str) -> Optional[T]:
        """Return the cached entry if its TTL has not expired."""
        entry = self._cache.get(cache_key)
        if entry is None:
            return None
        ts_mono, data = entry
        if (time.monotonic() - ts_mono) <= self.ttl_seconds:
            return data
        return None

    def _get_stale_cache(self, cache_key: str) -> Optional[T]:
        """Return the last known value even if its TTL has expired (degraded mode)."""
        entry = self._cache.get(cache_key)
        return entry[1] if entry is not None else None

    def _record_success(self, elapsed_ms: float, cache_key: str, data: T) -> None:
        """Record a network success, close the circuit and refresh the cache."""
        now_mono = time.monotonic()
        self.circuit_state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.opened_at_monotonic = None
        self.last_success_utc = int(time.time())
        self.last_error = None
        if self.latency_ms == 0.0:
            self.latency_ms = round(elapsed_ms, 2)
        else:
            self.latency_ms = round(0.7 * self.latency_ms + 0.3 * elapsed_ms, 2)
        self._cache[cache_key] = (now_mono, data)

    def _record_failure(self, error_msg: str) -> None:
        """Count a failure and open the circuit once the threshold is reached."""
        self.consecutive_failures += 1
        self.last_error = error_msg[:240]
        if self.consecutive_failures >= self.failure_threshold:
            self.circuit_state = CircuitState.OPEN
            self.opened_at_monotonic = time.monotonic()

    def _can_attempt_network(self) -> bool:
        """Tell whether the circuit breaker allows a network call."""
        if self.circuit_state == CircuitState.CLOSED:
            return True
        if self.circuit_state == CircuitState.OPEN:
            if (
                self.opened_at_monotonic is not None
                and (time.monotonic() - self.opened_at_monotonic) >= self.recovery_timeout_seconds
            ):
                self.circuit_state = CircuitState.HALF_OPEN
                return True
            return False
        # HALF_OPEN
        return True

    async def fetch(self, cache_key: str = "global", force_refresh: bool = False, **kwargs: Any) -> T:
        """Run the collection behind the TTL cache, the request coalescing and the circuit breaker.

        Concurrent calls for the same ``cache_key`` share one upstream request: the first one starts
        it, the others wait for its answer (or its error). Each caller then applies its own policy:
        with ``allow_stale_on_error=True`` (default) and a cached value, the cached value is returned
        on failure. Otherwise ``CollectorUnavailableError`` is raised so that the orchestrator can
        switch to its fallback.
        """
        allow_stale: bool = bool(kwargs.pop("allow_stale_on_error", True))

        if not force_refresh:
            fresh = self._get_cached_if_fresh(cache_key)
            if fresh is not None:
                return fresh

        if not self._can_attempt_network():
            stale = self._get_stale_cache(cache_key) if allow_stale else None
            if stale is not None:
                return stale
            raise CollectorUnavailableError(
                f"Circuit breaker OPEN for {self.name} ({self.last_error})"
            )

        try:
            return await self._coalesced_upstream(cache_key, kwargs)
        except Exception as exc:
            stale = self._get_stale_cache(cache_key) if allow_stale else None
            if stale is not None:
                return stale
            raise CollectorUnavailableError(
                f"Collector {self.name} failed: {type(exc).__name__}: {exc}"
            ) from exc

    async def _coalesced_upstream(self, cache_key: str, kwargs: dict[str, Any]) -> T:
        """Return the answer of the upstream request for ``cache_key``, starting it only if none is running.

        The request runs in its own task, shared by every caller: a caller that is cancelled (a client
        that hangs up) leaves the others waiting for the answer, and the answer is cached even when
        nobody waits for it any more.
        """
        loop = asyncio.get_running_loop()
        task = self._inflight.get(cache_key)
        if task is not None and (task.done() or task.get_loop() is not loop):
            # A leftover of a closed event loop (a test client, a restarted server): never wait for it.
            self._inflight.pop(cache_key, None)
            task = None
        if task is None:
            task = loop.create_task(self._upstream(cache_key, kwargs))
            self._inflight[cache_key] = task
            task.add_done_callback(lambda done, key=cache_key: self._upstream_done(key, done))
        return await asyncio.shield(task)

    def _upstream_done(self, cache_key: str, task: "asyncio.Task[T]") -> None:
        """Forget a finished upstream request and mark its error as retrieved (every waiter may be gone)."""
        if self._inflight.get(cache_key) is task:
            del self._inflight[cache_key]
        if not task.cancelled():
            task.exception()

    async def _upstream(self, cache_key: str, kwargs: dict[str, Any]) -> T:
        """Send the upstream request once, record its outcome in the circuit breaker and the cache."""
        start_mono = time.monotonic()
        try:
            if self._external_client is not None:
                result = await self._collect(self._external_client, **kwargs)
            else:
                headers = {"User-Agent": build_user_agent(), "Accept": "application/json"}
                async with httpx.AsyncClient(
                    timeout=self.timeout_seconds, headers=headers, follow_redirects=True
                ) as client:
                    result = await self._collect(client, **kwargs)
        except Exception as exc:
            self._record_failure(f"{type(exc).__name__}: {exc}")
            raise

        elapsed_ms = (time.monotonic() - start_mono) * 1000.0
        self._record_success(elapsed_ms, cache_key, result)
        return result

    @abstractmethod
    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> T:
        """Provider-specific collection implemented by each concrete collector."""

    def get_health(self) -> ProviderHealth:
        """Return the instantaneous health of the collector."""
        newest_age_s: Optional[int] = None
        if self._cache:
            now_mono = time.monotonic()
            min_age = min(now_mono - ts for ts, _ in self._cache.values())
            newest_age_s = max(0, int(min_age))

        healthy = self.circuit_state != CircuitState.OPEN or newest_age_s is not None
        return ProviderHealth(
            provider_id=self.provider_id,
            name=self.name,
            domain=self.domain,
            is_fallback=self.is_fallback,
            healthy=healthy,
            circuit_state=self.circuit_state,
            latency_ms=self.latency_ms,
            consecutive_failures=self.consecutive_failures,
            last_success_utc=self.last_success_utc,
            cache_age_s=newest_age_s,
            last_error=self.last_error,
        )
