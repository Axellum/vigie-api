"""The HTTP client a collector keeps, how it connects, the request asked again on a closed connection, and the bounded cache.

See ``ConnectionPolicy``, ``BaseCollector._http_client``, ``BaseCollector._get`` and ``CACHE_MAX_ENTRIES`` in
``app/collectors/base.py``, and the self-hosted Open-Meteo request of ``app/collectors/open_meteo_hosts.py``.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.collectors import open_meteo_hosts
from app.collectors.base import (
    CACHE_MAX_ENTRIES,
    OPEN_METEO_CONNECTION,
    PUBLIC_CONNECTION,
    SELF_HOSTED_CONNECTION,
    BaseCollector,
    ConnectionPolicy,
)
from app.collectors.flood_air import OpenMeteoAirQualityCollector, OpenMeteoFloodCollector
from app.collectors.marine import OpenMeteoMarineCollector
from app.collectors.meteo_gateway import MeteoGatewayRainCollector
from app.collectors.meteofrance_ensemble import MeteoFranceEnsembleCollector
from app.collectors.meteofrance_forests import MeteoFranceForestsCollector
from app.collectors.meteofrance_overseas import MeteoFranceOverseasCollector
from app.collectors.meteofrance_radar import MeteoFranceRadarCollector
from app.collectors.meteofrance_shortterm import MeteoFranceShortTermCollector
from app.collectors.meteofrance_station_daily import MeteoFranceStationDailyCollector
from app.collectors.meteofrance_weather import MeteoFranceWeatherCollector
from app.collectors.surf import SurfForecastCollector
from app.collectors.weather import DEFAULT_OPEN_METEO_FORECAST_URL, OpenMeteoCollector
from app.services.storage_wal import SQLiteWALStorage
from tests.helpers import Provider, SimClock

CLOSED = "Server disconnected without sending a response."


class Probe(BaseCollector[str]):
    """A collector that records the client each collection is given, and answers the key it was asked."""

    def __init__(self, connection: ConnectionPolicy = PUBLIC_CONNECTION, http_client: Any = None, timeout: float = 6.0) -> None:
        super().__init__("probe", "Probe", "test", ttl_seconds=60, timeout_seconds=timeout, http_client=http_client, connection=connection)
        self.clients: list[httpx.AsyncClient] = []

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> str:
        self.clients.append(client)
        return str(kwargs.get("answer", "value"))


@pytest.fixture()
def transports(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The arguments of every ``httpx.AsyncHTTPTransport`` built during the test."""
    seen: list[dict[str, Any]] = []
    real_transport = httpx.AsyncHTTPTransport

    def spy(**kwargs: Any) -> httpx.AsyncHTTPTransport:
        seen.append(kwargs)
        return real_transport(**kwargs)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", spy)
    return seen


# ------------------------------------------------------------------------------------------------ the kept client


async def test_the_collections_share_one_kept_client(sim_clock: SimClock) -> None:
    probe = Probe()
    try:
        await probe.fetch("a")
        await probe.fetch("b")
        await probe.fetch("a", force_refresh=True)
        assert len(probe.clients) == 3 and len({id(c) for c in probe.clients}) == 1
        assert not probe.clients[0].is_closed  # kept open for the next request
    finally:
        await probe.aclose()
    assert probe.clients[0].is_closed and probe._client is None


async def test_a_given_client_is_used_and_never_closed(sim_clock: SimClock) -> None:
    given = Provider().client()
    probe = Probe(http_client=given)
    await probe.fetch("a")
    await probe.aclose()
    assert probe.clients == [given] and not given.is_closed
    await given.aclose()


async def test_a_closed_client_is_made_again(sim_clock: SimClock) -> None:
    probe = Probe()
    first = probe._http_client()
    await probe.aclose()
    second = probe._http_client()
    try:
        assert second is not first and not second.is_closed
    finally:
        await probe.aclose()


def test_another_event_loop_gets_its_own_client() -> None:
    """A client belongs to the loop that made it (a test, a restarted server): a new loop never reuses it."""
    probe = Probe()

    async def client_of_this_loop() -> httpx.AsyncClient:
        return probe._http_client()

    first = asyncio.run(client_of_this_loop())
    second = asyncio.run(client_of_this_loop())
    assert second is not first
    asyncio.run(probe.aclose())  # the client of a loop that is gone is dropped, not awaited
    assert probe._client is None


async def test_public_policy_waits_the_whole_timeout_and_keeps_httpx_defaults(transports: list[dict[str, Any]]) -> None:
    probe = Probe(timeout=15.0)
    client = probe._http_client()
    try:
        assert client.timeout.connect == client.timeout.read == 15.0
        assert transports[-1]["retries"] == 0
        limits = transports[-1]["limits"]
        assert (limits.max_connections, limits.max_keepalive_connections, limits.keepalive_expiry) == (None, 20, 5.0)
        assert client.headers["User-Agent"].startswith("vigie-api") and client.follow_redirects
    finally:
        await probe.aclose()


async def test_self_hosted_policy_gives_up_a_silent_connection_soon(transports: list[dict[str, Any]]) -> None:
    probe = Probe(connection=SELF_HOSTED_CONNECTION, timeout=8.0)
    client = probe._http_client()
    try:
        assert client.timeout.connect == 2.0 and client.timeout.read == 8.0
        assert transports[-1]["retries"] == 1  # a new connection that does not answer is tried once more
        # Kept less long than the gateway keeps it (75 s, vigie-meteo#21), longer than the 5 s of httpx.
        assert transports[-1]["limits"].keepalive_expiry == 30.0
    finally:
        await probe.aclose()


async def test_a_timeout_object_is_kept_as_it_is(transports: list[dict[str, Any]]) -> None:
    probe = Probe(connection=SELF_HOSTED_CONNECTION)
    probe.timeout_seconds = httpx.Timeout(60.0, connect=15.0)  # a collector that sets every timeout itself
    client = probe._http_client()
    try:
        assert client.timeout.connect == 15.0 and client.timeout.read == 60.0
    finally:
        await probe.aclose()


@pytest.mark.parametrize(
    "make",
    [
        lambda: MeteoGatewayRainCollector("http://gateway.test"),
        lambda: MeteoFranceWeatherCollector("http://gateway.test"),
        lambda: MeteoFranceOverseasCollector("http://gateway.test"),
        lambda: MeteoFranceRadarCollector("http://gateway.test"),
        lambda: MeteoFranceShortTermCollector("http://gateway.test"),
        lambda: MeteoFranceEnsembleCollector("http://gateway.test"),
        lambda: MeteoFranceStationDailyCollector("http://gateway.test"),
        lambda: MeteoFranceForestsCollector("http://gateway.test"),
    ],
)
def test_the_gateway_collectors_connect_as_to_a_nearby_host(make: Any) -> None:
    assert make().connection == SELF_HOSTED_CONNECTION


@pytest.mark.parametrize(
    "make", [OpenMeteoCollector, OpenMeteoMarineCollector, OpenMeteoAirQualityCollector, SurfForecastCollector]
)
def test_the_open_meteo_collectors_try_a_new_connection_once_more(make: Any) -> None:
    assert make().connection == OPEN_METEO_CONNECTION


def test_the_other_collectors_keep_the_public_policy() -> None:
    assert OpenMeteoFloodCollector().connection == PUBLIC_CONNECTION  # the flood API has no self-hosted instance


async def test_the_aggregator_closes_every_kept_client(tmp_path: Path) -> None:
    from tests.helpers import make_service

    gateway = MeteoGatewayRainCollector("http://gateway.test")
    service = make_service(tmp_path / "v.db", meteo_gateway=gateway)
    kept = gateway._http_client()
    await service.aclose()
    assert kept.is_closed and gateway._client is None


# ------------------------------------------------------------------------------------------------ a closed connection


def closed_first(provider: Provider, times: int = 1) -> Any:
    def answer(request: httpx.Request) -> httpx.Response:
        if provider.count <= times:
            raise httpx.RemoteProtocolError(CLOSED, request=request)
        return httpx.Response(200, json={"ok": True})

    return answer


async def test_a_request_on_a_closed_connection_is_asked_once_more_and_counted_again(sim_clock: SimClock) -> None:
    provider = Provider()
    provider.answer = closed_first(provider)
    probe = Probe()
    async with provider.client() as client:
        response = await probe._get(client, "https://provider.test/feed")
    assert response.json() == {"ok": True} and provider.count == 2
    assert probe.usage.calls(sim_clock.monotonic()) == 2  # both requests left: both are counted


async def test_a_connection_closed_twice_is_an_error(sim_clock: SimClock) -> None:
    provider = Provider()
    provider.answer = closed_first(provider, times=5)
    probe = Probe()
    async with provider.client() as client:
        with pytest.raises(httpx.RemoteProtocolError):
            await probe._get(client, "https://provider.test/feed")
    assert provider.count == 2  # once more only


async def test_other_errors_are_not_asked_again(sim_clock: SimClock) -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    provider = Provider(answer=refused)
    probe = Probe()
    async with provider.client() as client:
        with pytest.raises(httpx.ReadTimeout):
            await probe._get(client, "https://provider.test/feed")
    assert provider.count == 1


async def test_head_and_capped_reads_are_asked_once_more_too(sim_clock: SimClock) -> None:
    provider = Provider()
    provider.answer = closed_first(provider)
    probe = Probe()
    async with provider.client() as client:
        assert (await probe._head(client, "https://provider.test/feed")).status_code == 200
        provider.requests.clear()
        capped = await probe._get_capped(client, "https://provider.test/feed", max_bytes=1000)
    assert capped.json() == {"ok": True} and provider.count == 2


async def test_redirects_can_be_refused_for_one_request(sim_clock: SimClock) -> None:
    provider = Provider(answer=lambda r: httpx.Response(302, headers={"Location": "https://elsewhere.test/"}))
    probe = Probe()
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider), follow_redirects=True) as client:
        response = await probe._get(client, "https://provider.test/frames", follow_redirects=False)
    assert response.status_code == 302 and provider.count == 1


# ------------------------------------------------------------------------------------------------ self-hosted Open-Meteo


OWN = "http://open-meteo.internal:8081/v1/forecast"
PARAMS = {"latitude": "40.0", "longitude": "-3.7", "current": "temperature_2m"}
WITH_VALUES = {"current": {"time": 1, "temperature_2m": 12.5}}


async def test_the_instance_has_2_s_to_connect_and_a_closed_connection_is_asked_again(sim_clock: SimClock) -> None:
    hosts: list[str] = []
    timeouts: list[dict[str, Any]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        timeouts.append(request.extensions.get("timeout", {}))
        if len(hosts) == 1:
            raise httpx.RemoteProtocolError(CLOSED, request=request)
        return httpx.Response(200, json=WITH_VALUES)

    collector = OpenMeteoCollector()
    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        response = await open_meteo_hosts.get_open_meteo(collector, client, OWN, DEFAULT_OPEN_METEO_FORECAST_URL, PARAMS)
    assert response.json() == WITH_VALUES
    assert hosts == ["open-meteo.internal", "open-meteo.internal"]  # not the public API, and the instance is not left aside
    assert collector.self_hosted_paused_until == {} and collector.public_fallbacks == 0
    assert timeouts[0]["connect"] == open_meteo_hosts.SELF_HOSTED_CONNECT_TIMEOUT_S == 2.0
    assert timeouts[0]["read"] == open_meteo_hosts.SELF_HOSTED_TIMEOUT_S


def test_an_unreachable_instance_is_left_aside_15_s() -> None:
    assert open_meteo_hosts.SELF_HOSTED_PAUSE_S == 15.0


# ------------------------------------------------------------------------------------------------ the bounded cache


async def test_the_cache_keeps_the_last_refreshed_entries(sim_clock: SimClock) -> None:
    probe = Probe()
    probe.cache_max_entries = 3
    for key in ("a", "b", "c"):
        await probe.fetch(key, answer=key)
    sim_clock.advance(61)  # every entry has expired
    await probe.fetch("a", answer="a2")  # refreshed: now the most recent
    await probe.fetch("d", answer="d")
    assert list(probe._cache) == ["c", "a", "d"]  # "b", refreshed longest ago, went first
    await probe.fetch("e", answer="e")
    assert list(probe._cache) == ["a", "d", "e"]
    assert probe._get_stale_cache("a") == "a2"  # a kept entry still serves when its provider fails


def test_the_default_bound_leaves_every_one_key_collector_alone() -> None:
    assert Probe().cache_max_entries == CACHE_MAX_ENTRIES == 4096


# ------------------------------------------------------------------------------------------------ SQLite


def test_the_connections_sort_in_memory_with_a_larger_cache_and_a_mapped_file(tmp_path: Path) -> None:
    storage = SQLiteWALStorage(tmp_path / "v.db")
    with storage._connect() as conn:
        assert conn.execute("PRAGMA temp_store").fetchone()[0] == 2  # MEMORY
        assert conn.execute("PRAGMA cache_size").fetchone()[0] == -16384  # 16 MB
        assert conn.execute("PRAGMA mmap_size").fetchone()[0] == 268435456
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert isinstance(conn, sqlite3.Connection)
