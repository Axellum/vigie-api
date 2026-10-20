"""The recorded feeds and CAP documents of the services of the WMO registry (``tests/fixtures/swic/README.md``), and a server that plays them back.

Everything here was recorded on 2026-10-20 at about 17:00 UTC (``RECORDED_AT``); a test that needs a warning to be in force sets
the simulated clock to a moment inside it (``at()``), so that no test depends on the day it runs.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx

from app.collectors.swic_registry import SwicSource

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "swic"
MANIFEST: dict[str, Any] = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
RECORDED_AT = 1_792_515_600  # 2026-10-20T17:00:00Z


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def alert_files() -> list[str]:
    """The recorded CAP documents (``cap_*.xml``): one at least for each family of format that was seen."""
    return sorted(path.name for path in FIXTURES.glob("cap_*.xml"))


def index_url(name: str) -> str:
    """The address a recorded index (``index_*.xml``) was read from."""
    return str(MANIFEST["indexes"][name]["url"])


def document_url(file: str) -> str:
    return next(url for url, name in MANIFEST["documents"].items() if name == file)


def at(epoch: int) -> float:
    """Seconds to advance the simulated clock (which starts at ``SimClock.EPOCH``) to reach an epoch time."""
    from tests.helpers import SimClock

    return float(epoch) - SimClock.EPOCH


def source(source_id: str, country: str, *indexes: str, name: str = "", meteoalarm: bool = False) -> SwicSource:
    """A service of the registry whose feeds are the recorded indexes (their real addresses)."""
    return SwicSource(
        source_id=source_id,
        country=country,
        country_name="",
        name=name or source_id,
        feeds=tuple(index_url(index) for index in indexes),
        meteoalarm=meteoalarm,
    )


class FixtureServer:
    """Plays back the recorded indexes and documents at their real addresses; an address nobody recorded is a 404.

    ``answers`` replaces a recorded answer for an address: bytes (an HTTP 200), an ``int`` (a status without a body) or an exception
    (raised, as a network error). Like the real servers that gave validators, an index repeats them and answers ``304`` to a request
    that repeats them.
    """

    def __init__(self, *indexes: str) -> None:
        self.bodies: dict[str, bytes] = {}
        self.validators: dict[str, dict[str, str]] = {}
        for name in indexes:
            self.add(name)
        self.documents: dict[str, bytes] = {url: fixture(file) for url, file in MANIFEST["documents"].items()}
        self.answers: dict[str, Any] = {}
        self.requests: list[httpx.Request] = []
        self.not_modified: list[str] = []

    def add(self, name: str) -> None:
        info = MANIFEST["indexes"][name]
        self.bodies[info["url"]] = fixture(name)
        validators = {}
        if info.get("etag"):
            validators["ETag"] = info["etag"]
        if info.get("last_modified"):
            validators["Last-Modified"] = info["last_modified"]
        self.validators[info["url"]] = validators

    def count(self, url: str) -> int:
        return sum(1 for request in self.requests if str(request.url) == url)

    def own(self) -> list[httpx.Request]:
        """The requests that did not go to the WMO's copy of the feeds (that one is tested on its own)."""
        return [request for request in self.requests if request.url.host != "severeweather.wmo.int"]

    def counts(self) -> dict[str, int]:
        """Requests per address, the WMO's copy left out."""
        found: dict[str, int] = {}
        for request in self.own():
            found[str(request.url)] = found.get(str(request.url), 0) + 1
        return found

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(request)
        if url in self.answers:
            answer = self.answers[url]
            if isinstance(answer, Exception):
                raise answer
            if isinstance(answer, int):
                return httpx.Response(answer)
            return httpx.Response(200, content=answer)
        if url in self.bodies:
            held = self.validators[url]
            if held and (
                (held.get("ETag") and request.headers.get("If-None-Match") == held["ETag"])
                or (held.get("Last-Modified") and request.headers.get("If-Modified-Since") == held["Last-Modified"])
            ):
                self.not_modified.append(url)
                return httpx.Response(304)
            return httpx.Response(200, content=self.bodies[url], headers=held)
        if url in self.documents:
            return httpx.Response(200, content=self.documents[url])
        return httpx.Response(404)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def cap(
    identifier: str = "test-1",
    *,
    sender: str = "test@example.org",
    sent: str = "2026-10-20T16:00:00+00:00",
    status: str = "Actual",
    msg_type: str = "Alert",
    scope: str = "Public",
    references: str = "",
    infos: Iterable[str] = (),
) -> bytes:
    """A synthetic CAP 1.2 document (always labelled as such by the tests that use it)."""
    refs = f"<references>{references}</references>" if references else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"><identifier>{identifier}</identifier><sender>{sender}</sender>'
        f"<sent>{sent}</sent><status>{status}</status><msgType>{msg_type}</msgType><scope>{scope}</scope>{refs}"
        + "".join(infos)
        + "</alert>"
    ).encode()


def info(
    *,
    language: str = "en",
    event: str = "Heavy rain",
    severity: str = "Moderate",
    urgency: str = "Expected",
    certainty: str = "Likely",
    onset: str = "2026-10-20T16:00:00+00:00",
    expires: str = "2026-10-21T16:00:00+00:00",
    areas: Iterable[str] = ("<area><areaDesc>Zone</areaDesc><polygon>10,10 10,11 11,11 11,10 10,10</polygon></area>",),
    parameters: str = "",
) -> str:
    return (
        f"<info><language>{language}</language><category>Met</category><event>{event}</event><urgency>{urgency}</urgency>"
        f"<severity>{severity}</severity><certainty>{certainty}</certainty><onset>{onset}</onset><expires>{expires}</expires>"
        f"<headline>{event}</headline>{parameters}" + "".join(areas) + "</info>"
    )


def service_serving_everything(mode: str = "all", server: "FixtureServer | None" = None):  # type: ignore[no-untyped-def]
    """A warnings service wired like the application (every national collector, the registry services in ``mode``) on a ``FixtureServer``.

    Nothing is asked until a test asks; the countries it lists are known from the bundled snapshot of the registry.
    """
    from app.collectors.disasters import NOAANWSCollector
    from app.collectors.dwd import DWDCollector
    from app.collectors.eccc import ECCCCollector
    from app.collectors.meteoalarm import MeteoAlarmCapCollector, MeteoAlarmCollector
    from app.collectors.metservice import MetServiceCollector
    from app.collectors.swic import SwicCollector
    from app.collectors.swic_registry import SwicRegistryCollector
    from app.collectors.vigicrues import VigicruesCollector
    from app.services.geoindex import default_countries, default_geometries
    from app.services.vigilance import VigilanceService

    client = (server or FixtureServer()).client()
    return VigilanceService(
        meteoalarm=MeteoAlarmCollector(http_client=client),
        meteoalarm_cap=MeteoAlarmCapCollector(http_client=client),
        noaa_nws=NOAANWSCollector(http_client=client),
        countries=default_countries(),
        geometries=default_geometries(),
        watched_zones=lambda: [],
        dwd=DWDCollector(http_client=client),
        eccc=ECCCCollector(http_client=client),
        metservice=MetServiceCollector(http_client=client),
        vigicrues=VigicruesCollector(http_client=client),
        swic=SwicCollector(http_client=client),
        swic_registry=SwicRegistryCollector(http_client=client),
        swic_mode=mode,
    )
