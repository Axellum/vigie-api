"""Base class of the external collectors: circuit breaker, TTL cache, request coalescing and metrics.

Guarantees invariant 1 of ``AGENTS.md``: a collector that times out or fails
(USGS, EMSC, GDACS, NOAA, Open-Meteo, MET Norway...) never blocks the asyncio
event loop and never makes the API fail. It keeps the last valid value in cache
and reports its health through ``get_health()``.

Every request a collector sends goes through ``BaseCollector._get``, which counts it (and its weight)
and refuses it when the published quota of the provider is used up (see ``budget.py``).
"""

from __future__ import annotations

import asyncio
import math
import os
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Generic, Optional, TypeVar

import httpx

from app import clock
from app.collectors.budget import (
    QUOTAS,
    BudgetExhaustedError,
    CallBudget,
    UsageWindow,
    budget_fraction_from_env,
)
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


def parse_iso_utc(value: Any) -> Optional[int]:
    """Parse an ISO 8601 date-time to UTC epoch seconds (a value with no offset is taken as UTC); ``None`` if it is not one."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def http_date_to_epoch(value: Optional[str]) -> Optional[float]:
    """Convert an HTTP date (``Expires``, ``Date``, ``Last-Modified``) to epoch seconds, or ``None``."""
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    return parsed.timestamp() if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc).timestamp()


def response_lifetime_s(response: httpx.Response) -> Optional[float]:
    """How long the provider says the answer stays valid, from its ``Expires`` header.

    The lifetime is ``Expires - Date`` (both are the provider's own clock, so a wrong clock on this
    machine changes nothing); without a ``Date`` header it is ``Expires`` minus the local wall clock.
    ``None`` when there is no usable ``Expires`` or when it is already past.
    """
    expires = http_date_to_epoch(response.headers.get("Expires"))
    if expires is None:
        return None
    reference = http_date_to_epoch(response.headers.get("Date"))
    lifetime = expires - (reference if reference is not None else clock.wall())
    return lifetime if lifetime > 0 else None


class CollectorUnavailableError(RuntimeError):
    """Raised when a collector fails and no cached fallback value is available."""


@dataclass
class CacheEntry(Generic[T]):
    """One cached answer."""

    stored_at: float  # monotonic time of the last refresh (its age is reported in the health)
    value: T
    fresh_until: float  # monotonic time until which the value is served without a new request
    validators: Optional[dict[str, str]] = None  # HTTP validators for a conditional request (``Last-Modified``)


@dataclass(frozen=True)
class Collected(Generic[T]):
    """What ``_collect`` returns when the provider says more than the value.

    ``fresh_for_s`` is the lifetime the provider announced (``response_lifetime_s``): the cache keeps
    the value that long instead of the TTL of the collector. ``validators`` are kept to make the next
    request conditional.
    """

    value: T
    fresh_for_s: Optional[float] = None
    validators: Optional[dict[str, str]] = None


@dataclass(frozen=True)
class Fetched(Generic[T]):
    """A value and whether it is **stale**: served from an expired cache entry because the provider
    failed or its call budget is used up. The callers whose response can say so mark it."""

    value: T
    stale: bool = False


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
        self._cache: dict[str, CacheEntry[T]] = {}
        # The upstream request being sent for each cache key (see ``_coalesced_upstream``)
        self._inflight: dict[str, asyncio.Task[T]] = {}

        # Requests really sent over the last 24 hours, and the budget (shared with the providers that
        # share the same published quota, see ``share_budgets``)
        self.usage = UsageWindow()
        self.budget = CallBudget(QUOTAS.get(provider_id), budget_fraction_from_env())
        self.budget_refusals: int = 0

    def _get_cached_if_fresh(self, cache_key: str) -> Optional[T]:
        """Return the cached entry if its TTL has not expired."""
        entry = self._cache.get(cache_key)
        if entry is None:
            return None
        if clock.monotonic() <= entry.fresh_until:
            return entry.value
        return None

    def _get_stale_cache(self, cache_key: str) -> Optional[T]:
        """Return the last known value even if its TTL has expired (degraded mode)."""
        entry = self._cache.get(cache_key)
        return entry.value if entry is not None else None

    def _record_success(
        self,
        elapsed_ms: float,
        cache_key: str,
        data: T,
        ttl_seconds: Optional[float] = None,
        validators: Optional[dict[str, str]] = None,
    ) -> None:
        """Record a network success, close the circuit and refresh the cache."""
        now_mono = clock.monotonic()
        self.circuit_state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.opened_at_monotonic = None
        self.last_success_utc = int(clock.wall())
        self.last_error = None
        if self.latency_ms == 0.0:
            self.latency_ms = round(elapsed_ms, 2)
        else:
            self.latency_ms = round(0.7 * self.latency_ms + 0.3 * elapsed_ms, 2)
        lifetime = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        self._cache[cache_key] = CacheEntry(
            stored_at=now_mono, value=data, fresh_until=now_mono + lifetime, validators=validators
        )

    def _record_failure(self, error_msg: str) -> None:
        """Count a failure and open the circuit once the threshold is reached."""
        self.consecutive_failures += 1
        self.last_error = error_msg[:240]
        if self.consecutive_failures >= self.failure_threshold:
            self.circuit_state = CircuitState.OPEN
            self.opened_at_monotonic = clock.monotonic()

    def _can_attempt_network(self) -> bool:
        """Tell whether the circuit breaker allows a network call."""
        if self.circuit_state == CircuitState.CLOSED:
            return True
        if self.circuit_state == CircuitState.OPEN:
            if (
                self.opened_at_monotonic is not None
                and (clock.monotonic() - self.opened_at_monotonic) >= self.recovery_timeout_seconds
            ):
                self.circuit_state = CircuitState.HALF_OPEN
                return True
            return False
        # HALF_OPEN
        return True

    async def fetch(self, cache_key: str = "global", force_refresh: bool = False, **kwargs: Any) -> T:
        """Return the value of ``cache_key``: ``fetch_ex`` without the stale flag."""
        return (await self.fetch_ex(cache_key, force_refresh, **kwargs)).value

    async def fetch_ex(self, cache_key: str = "global", force_refresh: bool = False, **kwargs: Any) -> Fetched[T]:
        """Run the collection behind the TTL cache, the request coalescing, the circuit breaker and the budget.

        Concurrent calls for the same ``cache_key`` share one upstream request: the first one starts
        it, the others wait for its answer (or its error). Each caller then applies its own policy:
        with ``allow_stale_on_error=True`` (default) and a cached value, the cached value is returned
        on failure. Otherwise ``CollectorUnavailableError`` is raised so that the orchestrator can
        switch to its fallback.

        When the call budget of the provider is used up the request is not sent: the cached value is
        returned, even expired and whatever ``allow_stale_on_error`` says, and flagged stale; with no
        cached value ``CollectorUnavailableError`` is raised. A refused request is not a failure of the
        provider: the circuit breaker does not see it.

        ``ttl_seconds`` (optional) replaces the TTL of the collector for the entry this call stores;
        a ``_collect`` that returns ``Collected(fresh_for_s=...)`` replaces it with what the provider said.
        """
        allow_stale: bool = bool(kwargs.pop("allow_stale_on_error", True))
        ttl = kwargs.pop("ttl_seconds", None)
        ttl_seconds: float = self.ttl_seconds if ttl is None else float(ttl)

        if not force_refresh:
            fresh = self._get_cached_if_fresh(cache_key)
            if fresh is not None:
                return Fetched(fresh)

        if not self._can_attempt_network():
            stale = self._get_stale_cache(cache_key) if allow_stale else None
            if stale is not None:
                return Fetched(stale, stale=True)
            raise CollectorUnavailableError(
                f"Circuit breaker OPEN for {self.name} ({self.last_error})"
            )

        try:
            return Fetched(await self._coalesced_upstream(cache_key, ttl_seconds, kwargs))
        except BudgetExhaustedError as exc:
            stale = self._get_stale_cache(cache_key)
            if stale is not None:
                return Fetched(stale, stale=True)
            raise CollectorUnavailableError(f"Call budget of {self.name} exhausted: {exc}") from exc
        except Exception as exc:
            stale = self._get_stale_cache(cache_key) if allow_stale else None
            if stale is not None:
                return Fetched(stale, stale=True)
            raise CollectorUnavailableError(
                f"Collector {self.name} failed: {type(exc).__name__}: {exc}"
            ) from exc

    async def _coalesced_upstream(self, cache_key: str, ttl_seconds: float, kwargs: dict[str, Any]) -> T:
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
            task = loop.create_task(self._upstream(cache_key, ttl_seconds, kwargs))
            self._inflight[cache_key] = task
            task.add_done_callback(lambda done, key=cache_key: self._upstream_done(key, done))
        return await asyncio.shield(task)

    def _upstream_done(self, cache_key: str, task: "asyncio.Task[T]") -> None:
        """Forget a finished upstream request and mark its error as retrieved (every waiter may be gone)."""
        if self._inflight.get(cache_key) is task:
            del self._inflight[cache_key]
        if not task.cancelled():
            task.exception()

    async def _upstream(self, cache_key: str, ttl_seconds: float, kwargs: dict[str, Any]) -> T:
        """Send the upstream request once, record its outcome in the circuit breaker and the cache.

        ``_collect`` receives the entry already cached under this key (``previous``, possibly expired)
        so that a provider that supports it can be asked whether anything changed.
        """
        start_mono = clock.monotonic()
        previous = self._cache.get(cache_key)
        try:
            if self._external_client is not None:
                result = await self._collect(self._external_client, previous=previous, **kwargs)
            else:
                headers = {"User-Agent": build_user_agent(), "Accept": "application/json"}
                async with httpx.AsyncClient(
                    timeout=self.timeout_seconds, headers=headers, follow_redirects=True
                ) as client:
                    result = await self._collect(client, previous=previous, **kwargs)
        except BudgetExhaustedError:
            self.budget_refusals += 1
            raise
        except Exception as exc:
            self._record_failure(f"{type(exc).__name__}: {exc}")
            raise

        elapsed_ms = (clock.monotonic() - start_mono) * 1000.0
        if isinstance(result, Collected):
            lifetime = ttl_seconds if result.fresh_for_s is None else result.fresh_for_s
            self._record_success(elapsed_ms, cache_key, result.value, lifetime, result.validators)
            return result.value
        self._record_success(elapsed_ms, cache_key, result, ttl_seconds)
        return result

    async def _get(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        params: Optional[Mapping[str, str]] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> httpx.Response:
        """Send a GET request to the provider: the only way a collector reaches it.

        The request is counted (calls and weight over 24 hours) when it is sent, whatever its outcome.
        When the call budget is used up it is not sent and ``BudgetExhaustedError`` is raised.
        """
        weight = self.call_weight(params or {})
        now = clock.monotonic()
        if not self.budget.try_spend(weight, now):
            raise BudgetExhaustedError(f"{self.provider_id}: a request of weight {weight:g} does not fit in the budget")
        self.usage.add(now, weight)
        return await client.get(url, params=params, headers=headers)

    def call_weight(self, params: Mapping[str, str]) -> float:
        """Weight of one upstream request in the quota of the provider: 1.0 unless the provider weights its calls."""
        return 1.0

    @abstractmethod
    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> T | Collected[T]:
        """Provider-specific collection implemented by each concrete collector.

        Returns the value, or ``Collected`` when the provider also tells how long the value stays valid
        or gives validators. ``previous`` (a ``CacheEntry`` or ``None``) is always among the keyword
        arguments.
        """

    def get_health(self) -> ProviderHealth:
        """Return the instantaneous health of the collector."""
        newest_age_s: Optional[int] = None
        if self._cache:
            now_mono = clock.monotonic()
            min_age = min(now_mono - entry.stored_at for entry in self._cache.values())
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
            calls_24h=self.usage.calls(clock.monotonic()),
            weight_24h=self.usage.weight(clock.monotonic()),
            quota=self.budget.status(clock.monotonic()),
            budget_refusals=self.budget_refusals,
        )
