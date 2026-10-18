"""Classe de base des collecteurs externes avec Circuit Breaker, Cache TTL et Métriques.

Garantit l'invariant n°1 de `AGENTS.md` :
Un collecteur externe en timeout ou en erreur (USGS, EMSC, GDACS, NOAA, Open-Meteo, MET Norway)
ne doit jamais bloquer la boucle d'événements `asyncio` ni faire échouer l'API.
Il conserve le dernier état valide en cache et expose sa santé via `get_health()`.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Generic, Optional, TypeVar

import httpx

from app.models.schemas import CircuitState, ProviderHealth

T = TypeVar("T")

DEFAULT_USER_AGENT = "VigieMondialePro/1.1 (+https://github.com/Axellum/alerte-mondiale-api; contact@auxfilsdesidees.fr)"


class CollectorUnavailableError(RuntimeError):
    """Levée lorsqu'un collecteur échoue et qu'aucune donnée de secours en cache n'est disponible."""


class BaseCollector(ABC, Generic[T]):
    """Socle résilient pour tous les collecteurs HTTP asynchrones."""

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

        # État du Circuit Breaker
        self.circuit_state: CircuitState = CircuitState.CLOSED
        self.consecutive_failures: int = 0
        self.opened_at_monotonic: Optional[float] = None
        self.last_success_utc: Optional[int] = None
        self.last_error: Optional[str] = None
        self.latency_ms: float = 0.0

        # Cache mémoire indexé par clé (ex: "global" ou "48.86,2.35")
        self._cache: dict[str, tuple[float, T]] = {}

    def _get_cached_if_fresh(self, cache_key: str) -> Optional[T]:
        """Retourne l'entrée en cache si son TTL n'est pas expiré."""
        entry = self._cache.get(cache_key)
        if entry is None:
            return None
        ts_mono, data = entry
        if (time.monotonic() - ts_mono) <= self.ttl_seconds:
            return data
        return None

    def _get_stale_cache(self, cache_key: str) -> Optional[T]:
        """Retourne la dernière valeur connue même si son TTL est expiré (mode dégradé)."""
        entry = self._cache.get(cache_key)
        return entry[1] if entry is not None else None

    def _record_success(self, elapsed_ms: float, cache_key: str, data: T) -> None:
        """Enregistre un succès réseau, ferme le disjoncteur et met à jour le cache."""
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
        """Incrémente le compteur d'échecs et ouvre le disjoncteur si le seuil est atteint."""
        self.consecutive_failures += 1
        self.last_error = error_msg[:240]
        if self.consecutive_failures >= self.failure_threshold:
            self.circuit_state = CircuitState.OPEN
            self.opened_at_monotonic = time.monotonic()

    def _can_attempt_network(self) -> bool:
        """Vérifie si le Circuit Breaker autorise un appel réseau."""
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
        """Exécute la collecte avec protection par cache TTL et Circuit Breaker.

        Si `allow_stale_on_error=True` (défaut) et qu'une valeur en cache existe, la retourne.
        Sinon lève `CollectorUnavailableError` pour permettre à l'orchestrateur d'activer le Fallback.
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
                f"Circuit Breaker OPEN pour {self.name} ({self.last_error})"
            )

        start_mono = time.monotonic()
        try:
            if self._external_client is not None:
                result = await self._collect(self._external_client, **kwargs)
            else:
                headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"}
                async with httpx.AsyncClient(
                    timeout=self.timeout_seconds, headers=headers, follow_redirects=True
                ) as client:
                    result = await self._collect(client, **kwargs)

            elapsed_ms = (time.monotonic() - start_mono) * 1000.0
            self._record_success(elapsed_ms, cache_key, result)
            return result
        except Exception as exc:
            self._record_failure(f"{type(exc).__name__}: {exc}")
            stale = self._get_stale_cache(cache_key) if allow_stale else None
            if stale is not None:
                return stale
            raise CollectorUnavailableError(
                f"Échec du collecteur {self.name}: {type(exc).__name__}: {exc}"
            ) from exc

    @abstractmethod
    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> T:
        """Méthode métier implémentée par chaque collecteur concret."""

    def get_health(self) -> ProviderHealth:
        """Retourne l'état de santé instantané du collecteur."""
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
