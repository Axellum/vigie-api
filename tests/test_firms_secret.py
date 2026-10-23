"""The FIRMS MAP_KEY (optional) is in the URL of every request of the area API: it must not leave through an error, a log line or a response.

The key is used only when the static file of a source could not be read, so every test here makes the file fail first (``static_fails``).

The Vigie API of a public instance answers ``GET /v1/providers`` (``last_error``), ``GET /v1/health`` and the fire routes to anyone.
``httpx`` puts the URL in the text of some of its exceptions and in an INFO log line for each request, so the key is redacted at every
place where something is recorded or raised, and these tests look for it everywhere: the exception chain, ``last_error``, the health
of the provider, the answers of the routes and the log records. A control proves that the key *would* show up without the redaction.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from app.collectors.base import CollectorUnavailableError
from app.collectors.firms import FIRMSCollector, FIRMSError, _RedactingFilter
from tests.firms_samples import KEY, N20, N21, NOW, AreaServer, FirmsAnywhere, FirmsServer
from tests.helpers import Provider, SimClock

HOST_URL = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{KEY}/{N20}/world/1/2026-10-20"


def chain(exc: BaseException) -> Iterator[BaseException]:
    """The exception and everything it was raised from or while handling."""
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        stack.extend(e for e in (current.__cause__, current.__context__) if e is not None)


def assert_clean(*texts: Any) -> None:
    for text in texts:
        assert KEY not in str(text), f"the key is in: {str(text)[:200]}"
        assert KEY not in json.dumps(text, default=str)


def static_fails(request: httpx.Request, answer: Any) -> httpx.Response:
    """The file of FIRMS is down (it holds no key), the area API answers as ``answer`` says."""
    if request.url.path.startswith("/data/"):
        raise httpx.ConnectError("the file is not there", request=request)
    return answer(request)  # type: ignore[no-any-return]


def collector_with(answer: Any, sim_clock: SimClock, **kwargs: Any) -> FIRMSCollector:
    sim_clock.EPOCH = NOW
    provider = Provider(answer=lambda request: static_fails(request, answer), latency_ticks=0)
    return FIRMSCollector(KEY, http_client=provider.client(), sources=(N20,), window_hours=24, **kwargs)


# ------------------------------------------------------------------------------------------------ the control


@pytest.mark.asyncio
async def test_control_the_key_does_show_up_in_the_logs_of_httpx_without_the_redaction(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """If this fails the other tests of this file prove nothing: httpx writes the URL, key included, at INFO level."""
    monkeypatch.setattr(logging.getLogger("httpx"), "filters", [])
    caplog.set_level(logging.INFO, logger="httpx")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="ok"))) as client:
        await client.get(HOST_URL)
    assert KEY in caplog.text


@pytest.mark.asyncio
async def test_control_an_exception_of_a_transport_can_carry_the_url() -> None:
    request = httpx.Request("GET", HOST_URL)
    error = httpx.ConnectError(f"All connection attempts failed for {request.url}", request=request)
    assert KEY in str(error) and KEY in str(error.request.url)


# ------------------------------------------------------------------------------------------------ errors


@pytest.mark.asyncio
async def test_an_exception_that_holds_the_url_is_recorded_and_raised_without_the_key(sim_clock: SimClock) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"All connection attempts failed for {request.url}", request=request)

    collector = collector_with(answer, sim_clock)
    with pytest.raises(CollectorUnavailableError) as raised:
        await collector.fetch()
    for exc in chain(raised.value):
        assert KEY not in str(exc) and KEY not in repr(exc)
        request = getattr(exc, "request", None) or getattr(exc, "_request", None)
        assert request is None, "an exception that still holds the request holds the URL"
    assert collector.last_error is not None and "REDACTED" in collector.last_error
    assert_clean(collector.last_error, raised.value, collector.get_health().model_dump())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        lambda request: httpx.Response(403, text=f"Invalid MAP_KEY {KEY}"),
        lambda request: httpx.Response(500, text=f"error for {request.url}"),
        lambda request: httpx.Response(200, text=f"Invalid MAP_KEY. Your key {KEY} is not known"),
        lambda request: httpx.Response(200, text=f"<html>{request.url}</html>"),
    ],
    ids=["403 echoing the key", "500 echoing the url", "200 text echoing the key", "200 html with the url"],
)
async def test_an_answer_that_repeats_the_key_is_not_repeated(sim_clock: SimClock, answer: Any) -> None:
    collector = collector_with(answer, sim_clock)
    with pytest.raises(CollectorUnavailableError) as raised:
        await collector.fetch()
    assert_clean(collector.last_error, raised.value, collector.get_health().model_dump())


@pytest.mark.asyncio
async def test_a_timeout_and_a_cut_connection_are_clean_too(sim_clock: SimClock) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(f"timed out reading {request.url}", request=request)

    collector = collector_with(answer, sim_clock)
    with pytest.raises(CollectorUnavailableError) as raised:
        await collector.fetch()
    assert "ReadTimeout" in str(raised.value)
    assert_clean(collector.last_error, raised.value)


@pytest.mark.asyncio
async def test_the_failure_of_one_source_in_a_set_is_clean(sim_clock: SimClock) -> None:
    sim_clock.EPOCH = NOW
    server = FirmsServer(latency_ticks=0)

    def answer(request: httpx.Request) -> httpx.Response:
        if "noaa-21" in request.url.path or N21 in request.url.path:  # both the file and the area API of that source are lost
            raise httpx.ReadError(f"connection lost on {request.url}", request=request)
        return server._respond(request)

    collector = FIRMSCollector(KEY, http_client=Provider(answer=answer, latency_ticks=0).client(), window_hours=24)
    data = await collector.fetch()
    assert data.sources() == (N20,) and collector.failing() and collector.errors()
    assert_clean(collector.errors(), collector.last_error, collector.get_health().model_dump())


def test_the_base_class_redacts_whatever_it_records() -> None:
    collector = FIRMSCollector(KEY)
    collector._record_failure(f"boom at https://example.invalid/{KEY}/x")
    assert collector.last_error == "boom at https://example.invalid/REDACTED/x"
    assert collector.redact(f"{KEY}{KEY}") == "REDACTEDREDACTED" and collector.redact("nothing") == "nothing"


def test_a_message_the_error_class_builds_never_holds_the_key() -> None:
    assert KEY not in str(FIRMSError("VIIRS_NOAA20_NRT 2026-10-20: HTTP 403 from firms.modaps.eosdis.nasa.gov"))


# ------------------------------------------------------------------------------------------------ logs


@pytest.mark.asyncio
async def test_the_log_records_of_httpx_do_not_hold_the_key(sim_clock: SimClock, caplog: pytest.LogCaptureFixture) -> None:
    sim_clock.EPOCH = NOW
    caplog.set_level(logging.DEBUG)
    static, area = FirmsServer(latency_ticks=0), AreaServer(latency_ticks=0)
    static.failing = {N20, N21}  # the files are down: the area API is asked, with the key in its URL
    collector = FIRMSCollector(KEY, http_client=FirmsAnywhere(static, area).client(), window_hours=24)  # installs the filter on the loggers of httpx and httpcore
    await collector.fetch()
    area.failing = {N20, N21}
    sim_clock.advance(collector.ttl_seconds + 1)
    await collector.fetch_ex()
    assert "HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/REDACTED/" in caplog.text
    assert KEY not in caplog.text
    assert all(KEY not in record.getMessage() and KEY not in str(record.args) for record in caplog.records)


def test_the_filter_replaces_the_key_in_a_record_and_in_its_arguments() -> None:
    record = logging.LogRecord("httpx", logging.INFO, __file__, 1, "HTTP Request: %s %s", ("GET", HOST_URL), None)
    assert _RedactingFilter(KEY).filter(record) is True
    assert record.getMessage() == "HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/REDACTED/VIIRS_NOAA20_NRT/world/1/2026-10-20"
    quiet = logging.LogRecord("httpx", logging.INFO, __file__, 1, "no secret here %d", (3,), None)
    _RedactingFilter(KEY).filter(quiet)
    assert quiet.getMessage() == "no secret here 3"


def test_the_filter_is_installed_once_per_key() -> None:
    FIRMSCollector(KEY)
    FIRMSCollector(KEY)
    ours = [f for f in logging.getLogger("httpx").filters if isinstance(f, _RedactingFilter) and f._secret == KEY]
    assert len(ours) == 1
