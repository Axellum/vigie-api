"""A simulated MeteoAlarm and NWS behind ``httpx.MockTransport`` for the tests of the warnings: it serves the recorded
feeds of 2026-10-20 by default, whatever a test puts in ``feeds`` / ``caps`` / ``nws``, and counts every request it gets."""

from __future__ import annotations

from typing import Any, Optional

import httpx

from app.collectors.disasters import NOAANWSCollector
from app.collectors.meteoalarm import COUNTRY_FEEDS, MeteoAlarmCapCollector, MeteoAlarmCollector
from app.services.geoindex import default_countries, default_geometries
from app.services.vigilance import VigilanceService
from tests import vigilance_samples as samples
from tests.helpers import Provider

KNOWN_CAPS = {
    "b77d3bc0-76a2-4886-8da8-bac8d99f57fc": "cap_france_b77d3bc0.xml",
    "581d4ba4-01c3-4924-ae9c-43fcd1e7c68a": "cap_germany_581d4ba4.xml",
    "9c454e4a-5829-413f-a5d2-b0d50bc456e7": "cap_iceland_9c454e4a.xml",
}
SLUG_FILES = {slug: samples.FEED_FILES[country] for country, slug in COUNTRY_FEEDS.items() if country in samples.FEED_FILES}
SLUG_FILES["denmark"] = "atom_empty.xml"


def atom_entry(
    document: str,
    *,
    area: int = 0,
    level: str = "Yellow",
    kind: str = "Flooding",
    code: str = "FR713",
    name: str = "Drôme",
    expires: str = "2026-10-30T22:00:00+00:00",
    message_type: str = "Alert",
) -> str:
    """One entry as MeteoAlarm writes it (the fields the API reads), for the tests that change a feed over time."""
    base = "https://feeds.meteoalarm.org/api/v1/warnings/feeds-france/" + document
    return f"""  <entry>
    <cap:geocode><valueName>NUTS3</valueName><value>{code}</value></cap:geocode>
    <link title="{name}" href="https://meteoalarm.org?geocode=EMMA_ID:{code}" hreflang="en"/>
    <cap:areaDesc>{name}</cap:areaDesc>
    <cap:event>{level} {kind}</cap:event>
    <cap:sent>2026-10-20T09:42:09+00:00</cap:sent>
    <cap:expires>{expires}</cap:expires>
    <cap:effective>2026-10-20T10:00:00+00:00</cap:effective>
    <cap:onset>2026-10-20T10:00:00+00:00</cap:onset>
    <cap:message_type>{message_type}</cap:message_type>
    <cap:status>Actual</cap:status>
    <cap:identifier>test.{document}</cap:identifier>
    <link type="application/cap+xml" href="{base}"/>
    <id>{base}?index_info=0&amp;index_area={area}&amp;index_geocode=0</id>
    <title>{level} {kind} Warning issued for France - {name}</title>
  </entry>
"""


def atom_feed(*entries: str) -> bytes:
    head = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:cap="urn:oasis:names:tc:emergency:cap:1.2">\n<updated>2026-10-20T10:00:00Z</updated>\n'
    )
    return (head + "".join(entries) + "</feed>\n").encode()


class Upstream:
    """MeteoAlarm and the NWS, simulated: the recorded feeds by default, anything a test puts in ``feeds`` / ``caps``."""

    def __init__(self) -> None:
        self.feeds: dict[str, Any] = {}  # slug -> bytes, or an int HTTP status
        self.caps: dict[str, Any] = {}  # document uuid -> bytes, or an int HTTP status
        self.nws: Any = samples.fixture("nws_active_severe_extreme.json")
        self.default_cap: Optional[bytes] = None  # served for a document nobody recorded (else HTTP 404, as Italy's)
        self.meteoalarm = Provider(answer=self._meteoalarm_answer, latency_ticks=0)
        self.nws_provider = Provider(answer=self._nws_answer, latency_ticks=0)

    def _meteoalarm_answer(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/api/v1/warnings/"):
            uuid = path.rsplit("/", 1)[-1]
            recorded = samples.fixture(KNOWN_CAPS[uuid]) if uuid in KNOWN_CAPS else (self.default_cap or 404)
            content = self.caps.get(uuid, recorded)
        else:
            slug = path.rsplit("-atom-", 1)[-1]
            content = self.feeds.get(slug, samples.fixture(SLUG_FILES[slug]) if slug in SLUG_FILES else 404)
        return httpx.Response(content) if isinstance(content, int) else httpx.Response(200, content=content)

    def _nws_answer(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(self.nws) if isinstance(self.nws, int) else httpx.Response(200, content=self.nws)

    @property
    def feed_requests(self) -> list[str]:
        return [r.url.path.rsplit("-atom-", 1)[-1] for r in self.meteoalarm.requests if r.url.path.startswith("/feeds/")]

    @property
    def cap_requests(self) -> list[str]:
        return [r.url.path.rsplit("/", 1)[-1] for r in self.meteoalarm.requests if r.url.path.startswith("/api/v1/warnings/")]


def make_service(upstream: Upstream, zones: Optional[list[dict[str, Any]]] = None) -> VigilanceService:
    return VigilanceService(
        meteoalarm=MeteoAlarmCollector(http_client=upstream.meteoalarm.client()),
        meteoalarm_cap=MeteoAlarmCapCollector(http_client=upstream.meteoalarm.client()),
        noaa_nws=NOAANWSCollector(http_client=upstream.nws_provider.client()),
        countries=default_countries(),
        geometries=default_geometries(),
        watched_zones=lambda: zones or [],
    )
