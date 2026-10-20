"""MET Norway terms: conditional requests (``If-Modified-Since``), ``Expires``, at most 4 decimals."""

from __future__ import annotations

import re
from typing import Any, Optional

import httpx
import pytest

from app import clock as app_clock
from app.collectors.base import CollectorUnavailableError, response_lifetime_s
from app.collectors.weather import MetNorwayCollector
from tests import samples
from tests.helpers import Provider, SimClock, http_date


class FakeMet:
    """MET Norway as its terms describe it: ``Last-Modified``, ``Expires`` and ``304 Not Modified``."""

    def __init__(self, clock: SimClock, expires_in: Optional[float] = 600.0, clock_offset: float = 0.0) -> None:
        self.clock = clock
        self.expires_in = expires_in
        self.clock_offset = clock_offset  # the provider's clock minus this machine's clock
        self.last_modified_at = clock.time() - 120.0
        self.payload: dict[str, Any] = samples.metno_payload()
        self.fail = False
        self.provider = Provider(answer=self._answer)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            return httpx.Response(500)
        server_now = self.clock.time() + self.clock_offset
        headers = {"Date": http_date(server_now)}
        if self.expires_in is not None:
            headers["Expires"] = http_date(server_now + self.expires_in)
        if request.headers.get("If-Modified-Since") == http_date(self.last_modified_at):
            return httpx.Response(304, headers=headers)
        headers["Last-Modified"] = http_date(self.last_modified_at)
        return httpx.Response(200, json=self.payload, headers=headers)

    def collector(self) -> MetNorwayCollector:
        return MetNorwayCollector(http_client=self.provider.client())

    def conditional_headers(self) -> list[Optional[str]]:
        """The ``If-Modified-Since`` header of each request received."""
        return [request.headers.get("If-Modified-Since") for request in self.provider.requests]


@pytest.mark.asyncio
async def test_the_first_request_is_not_conditional_and_the_answer_is_kept_until_expires(sim_clock: SimClock) -> None:
    met = FakeMet(sim_clock, expires_in=600.0)
    collector = met.collector()

    first = await collector.fetch_point(48.85, 2.35)
    assert met.conditional_headers() == [None]

    sim_clock.advance(599.0)
    assert await collector.fetch_point(48.85, 2.35) is first
    assert met.provider.count == 1  # not asked again before Expires

    sim_clock.advance(2.0)  # 601 s: Expires has passed
    revalidated = await collector.fetch_point(48.85, 2.35)
    assert met.provider.count == 2
    # The header is exactly the Last-Modified of the previous answer, as the terms require.
    assert met.conditional_headers()[1] == http_date(met.last_modified_at)
    assert revalidated is first  # 304: the cached value is kept, not parsed again


@pytest.mark.asyncio
async def test_a_304_starts_a_new_lifetime_from_its_own_expires(sim_clock: SimClock) -> None:
    met = FakeMet(sim_clock, expires_in=600.0)
    collector = met.collector()
    await collector.fetch_point(48.85, 2.35)
    sim_clock.advance(601.0)
    await collector.fetch_point(48.85, 2.35)  # 304
    assert met.provider.count == 2

    sim_clock.advance(599.0)
    await collector.fetch_point(48.85, 2.35)
    assert met.provider.count == 2  # still valid: 599 s after the 304

    sim_clock.advance(2.0)
    await collector.fetch_point(48.85, 2.35)
    assert met.provider.count == 3
    assert met.conditional_headers()[2] == http_date(met.last_modified_at)  # the validator was kept


@pytest.mark.asyncio
async def test_new_data_replaces_the_value_and_the_validator(sim_clock: SimClock) -> None:
    met = FakeMet(sim_clock, expires_in=600.0)
    collector = met.collector()
    first = await collector.fetch_point(48.85, 2.35)
    old_last_modified = met.last_modified_at

    sim_clock.advance(601.0)
    met.last_modified_at = sim_clock.time() - 30.0
    met.payload = samples.metno_payload()
    met.payload["properties"]["timeseries"][0]["data"]["instant"]["details"]["air_temperature"] = 3.5
    second = await collector.fetch_point(48.85, 2.35)

    assert met.provider.count == 2
    assert met.conditional_headers()[1] == http_date(old_last_modified)  # it asked about the old version
    assert second is not first and second["current"].temp_c == 3.5  # and got the new one (a 200)

    sim_clock.advance(601.0)
    await collector.fetch_point(48.85, 2.35)
    assert met.conditional_headers()[2] == http_date(met.last_modified_at)  # now about the new version


@pytest.mark.asyncio
async def test_without_expires_the_default_ttl_applies(sim_clock: SimClock) -> None:
    met = FakeMet(sim_clock, expires_in=None)
    collector = met.collector()
    await collector.fetch_point(48.85, 2.35)

    sim_clock.advance(299.0)
    await collector.fetch_point(48.85, 2.35)
    assert met.provider.count == 1
    sim_clock.advance(2.0)
    await collector.fetch_point(48.85, 2.35)
    assert met.provider.count == 2


@pytest.mark.asyncio
async def test_an_expires_in_the_past_falls_back_to_the_default_ttl(sim_clock: SimClock) -> None:
    met = FakeMet(sim_clock, expires_in=-30.0)
    collector = met.collector()
    await collector.fetch_point(48.85, 2.35)
    sim_clock.advance(200.0)
    await collector.fetch_point(48.85, 2.35)
    assert met.provider.count == 1  # not asked in a loop because Expires is already past


@pytest.mark.asyncio
async def test_a_wrong_local_clock_does_not_change_the_lifetime(sim_clock: SimClock) -> None:
    met = FakeMet(sim_clock, expires_in=600.0, clock_offset=3600.0)  # this machine is an hour behind
    collector = met.collector()
    await collector.fetch_point(48.85, 2.35)

    sim_clock.advance(599.0)
    await collector.fetch_point(48.85, 2.35)
    assert met.provider.count == 1
    sim_clock.advance(2.0)
    await collector.fetch_point(48.85, 2.35)
    assert met.provider.count == 2


@pytest.mark.asyncio
async def test_a_failure_serves_the_expired_value_and_keeps_the_validator(sim_clock: SimClock) -> None:
    met = FakeMet(sim_clock, expires_in=600.0)
    collector = met.collector()
    first = await collector.fetch_point(48.85, 2.35)

    sim_clock.advance(601.0)
    met.fail = True
    stale = await collector.fetch_point(48.85, 2.35)  # the expired value is served, and says so
    assert stale["stale"] is True and stale["provider_name"].endswith("(stale cache)")
    assert stale["current"] == first["current"]

    met.fail = False
    assert await collector.fetch_point(48.85, 2.35) is first  # 304 again
    assert met.conditional_headers()[1:] == [http_date(met.last_modified_at)] * 2


@pytest.mark.asyncio
async def test_a_304_to_an_unconditional_request_is_a_failure_not_a_crash(sim_clock: SimClock) -> None:
    provider = Provider(answer=lambda request: httpx.Response(304))
    collector = MetNorwayCollector(http_client=provider.client())

    with pytest.raises(CollectorUnavailableError):
        await collector.fetch_point(48.85, 2.35, allow_stale_on_error=False)
    assert collector.consecutive_failures == 1


@pytest.mark.asyncio
async def test_coordinates_have_at_most_four_decimals(sim_clock: SimClock) -> None:
    met = FakeMet(sim_clock)
    collector = MetNorwayCollector(http_client=met.provider.client(), grid_deg=0.0001)

    await collector.fetch_point(48.856614, 2.352222)

    query = met.provider.query()
    assert re.fullmatch(r"-?\d+(\.\d{1,4})?", query["lat"]) and re.fullmatch(r"-?\d+(\.\d{1,4})?", query["lon"])
    assert (query["lat"], query["lon"]) == ("48.8566", "2.3522")


def test_response_lifetime_is_expires_minus_date(sim_clock: SimClock) -> None:
    now = sim_clock.time()

    def lifetime(**headers: str) -> Optional[float]:
        return response_lifetime_s(httpx.Response(200, headers=headers))

    assert lifetime(Date=http_date(now), Expires=http_date(now + 90)) == 90.0
    assert lifetime(Expires=http_date(now + 45)) == 45.0  # no Date: the local clock is the reference
    assert lifetime(Date=http_date(now), Expires=http_date(now - 1)) is None
    assert lifetime(Expires="0") is None and lifetime(Expires="not a date") is None
    assert lifetime() is None
    assert app_clock.wall() == now
