"""A self-hosted Open-Meteo asked first, the public API as a fallback (ADR 0039, ``app/collectors/open_meteo_hosts.py``)."""

from __future__ import annotations

import contextlib
from typing import Any

import httpx
import pytest

from app.collectors.flood_air import (
    DEFAULT_OPEN_METEO_AIR_QUALITY_URL,
    OpenMeteoAirQualityCollector,
    open_meteo_air_quality_url,
)
from app.collectors.open_meteo_hosts import (
    SELF_HOSTED_PAUSE_S,
    get_open_meteo,
    has_values,
    public_fallback_enabled,
)
from app.collectors.weather import DEFAULT_OPEN_METEO_FORECAST_URL, OpenMeteoCollector

OWN = "http://open-meteo.internal:8081/v1/forecast"
PARAMS = {"latitude": "40.0", "longitude": "-3.7", "current": "temperature_2m"}
WITH_VALUES = {"current": {"time": 1, "temperature_2m": 12.5}}
ALL_NULL = {"current": {"time": 1, "temperature_2m": None}}


class Upstream:
    """Answers the self-hosted host and the public one from Python, and records which host was asked."""

    def __init__(self, own: Any, public: Any = WITH_VALUES) -> None:
        self.own = own
        self.public = public
        self.hosts: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.hosts.append(request.url.host)
        answer = self.own if request.url.host == "open-meteo.internal" else self.public
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=answer)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


def test_has_values() -> None:
    assert has_values(WITH_VALUES)
    assert not has_values(ALL_NULL)
    assert has_values({"hourly": {"time": [1, 2], "wave_height": [None, 1.2]}})
    assert not has_values({"hourly": {"time": [1, 2], "wave_height": [None, None]}})
    assert has_values([ALL_NULL, WITH_VALUES])  # several places in one request
    assert not has_values({"error": True, "reason": "no data"})
    assert not has_values("text")


def test_public_fallback_switch() -> None:
    assert public_fallback_enabled({})
    assert public_fallback_enabled({"VIGIE_OPEN_METEO_PUBLIC_FALLBACK": "1"})
    for off in ("0", "false", "No", " off "):
        assert not public_fallback_enabled({"VIGIE_OPEN_METEO_PUBLIC_FALLBACK": off})


def test_air_quality_url_from_env() -> None:
    assert open_meteo_air_quality_url({}) == DEFAULT_OPEN_METEO_AIR_QUALITY_URL
    assert open_meteo_air_quality_url({"VIGIE_OPEN_METEO_AIR_QUALITY_URL": " http://x/v1/air-quality "}) == (
        "http://x/v1/air-quality"
    )


def test_air_quality_collector_reads_its_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIGIE_OPEN_METEO_AIR_QUALITY_URL", "http://open-meteo.internal:8081/v1/air-quality")
    assert OpenMeteoAirQualityCollector().BASE_URL == "http://open-meteo.internal:8081/v1/air-quality"


async def _ask(collector: OpenMeteoCollector, upstream: Upstream, url: str = OWN) -> httpx.Response:
    async with upstream.client() as client:
        return await get_open_meteo(collector, client, url, DEFAULT_OPEN_METEO_FORECAST_URL, PARAMS)


async def test_self_hosted_answer_is_used_and_not_counted(sim_clock: Any) -> None:
    collector = OpenMeteoCollector()
    upstream = Upstream(own=WITH_VALUES, public={"current": {"time": 1, "temperature_2m": 99.0}})
    response = await _ask(collector, upstream)
    assert response.json() == WITH_VALUES
    assert upstream.hosts == ["open-meteo.internal"]
    assert collector.usage.calls(sim_clock.monotonic()) == 0  # outside the public quota
    assert (collector.self_hosted_calls, collector.public_fallbacks) == (1, 0)


async def test_public_url_alone_is_counted_as_before(sim_clock: Any) -> None:
    collector = OpenMeteoCollector()
    upstream = Upstream(own=WITH_VALUES)
    await _ask(collector, upstream, url=DEFAULT_OPEN_METEO_FORECAST_URL)
    assert upstream.hosts == ["api.open-meteo.com"]
    assert collector.usage.calls(sim_clock.monotonic()) == 1
    assert collector.self_hosted_calls == 0


@pytest.mark.parametrize(
    "own",
    [
        httpx.Response(503, text="down"),
        httpx.Response(400, json={"error": True, "reason": "Missing native ICON grid artifact"}),
        httpx.Response(200, text="<html>not json</html>"),
        ALL_NULL,
    ],
    ids=["5xx", "4xx", "not-json", "all-null"],
)
async def test_falls_back_to_the_public_api(sim_clock: Any, own: Any) -> None:
    collector = OpenMeteoCollector()
    upstream = Upstream(own=own)
    response = await _ask(collector, upstream)
    assert response.json() == WITH_VALUES
    assert upstream.hosts == ["open-meteo.internal", "api.open-meteo.com"]
    assert collector.usage.calls(sim_clock.monotonic()) == 1  # only the fallback is counted
    assert collector.public_fallbacks == 1


async def test_unreachable_instance_is_left_aside_for_a_while(sim_clock: Any) -> None:
    collector = OpenMeteoCollector()
    upstream = Upstream(own=httpx.ConnectError("refused"))
    await _ask(collector, upstream)
    await _ask(collector, upstream)  # left aside: straight to the public API
    assert upstream.hosts == ["open-meteo.internal", "api.open-meteo.com", "api.open-meteo.com"]
    sim_clock.advance(SELF_HOSTED_PAUSE_S + 1)
    upstream.own = WITH_VALUES
    await _ask(collector, upstream)  # tried again after the pause
    assert upstream.hosts[-1] == "open-meteo.internal"
    assert collector.public_fallbacks == 2


async def test_fallback_off_keeps_the_instance_answer(sim_clock: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIGIE_OPEN_METEO_PUBLIC_FALLBACK", "0")
    collector = OpenMeteoCollector()
    upstream = Upstream(own=ALL_NULL)
    response = await _ask(collector, upstream)
    assert response.json() == ALL_NULL
    upstream.own = httpx.ConnectError("refused")
    with pytest.raises(httpx.ConnectError):
        await _ask(collector, upstream)
    assert upstream.hosts == ["open-meteo.internal", "open-meteo.internal"]
    assert collector.usage.calls(sim_clock.monotonic()) == 0


async def test_weather_collector_goes_to_the_instance_first(sim_clock: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end through ``fetch``: the forecast collector asks the configured instance, not the public API."""
    monkeypatch.setenv("VIGIE_OPEN_METEO_FORECAST_URL", OWN)
    upstream = Upstream(own=httpx.Response(503), public=httpx.Response(503))
    collector = OpenMeteoCollector(http_client=upstream.client())
    with contextlib.suppress(Exception):  # both down: the collector reports it, that is not the point here
        await collector.fetch(cache_key="t", lat=40.0, lon=-3.7)
    assert upstream.hosts[:2] == ["open-meteo.internal", "api.open-meteo.com"]
