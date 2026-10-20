"""A simulated MeteoAlarm, NWS and national services behind ``httpx.MockTransport`` for the tests of the warnings: it serves
the recorded answers of 2026-10-20 by default, whatever a test puts in ``feeds`` / ``caps`` / ``nws`` / ``dwd`` / ``eccc`` /
``metservice`` / ``vigicrues``, and counts every request it gets."""

from __future__ import annotations

import hashlib
from typing import Any, Optional

import httpx

from app.collectors.disasters import NOAANWSCollector
from app.collectors.dwd import DWDCollector
from app.collectors.eccc import ECCCCollector
from app.collectors.meteoalarm import COUNTRY_FEEDS, MeteoAlarmCapCollector, MeteoAlarmCollector
from app.collectors.metservice import MetServiceCollector
from app.collectors.vigicrues import VigicruesCollector
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
        # The national services: the recorded answer, or an int HTTP status (or other bytes) a test puts in its place.
        self.dwd: Any = samples.national_fixture("dwd", "status_district_mul.zip")
        self.eccc: Any = samples.national_fixture("eccc", "weather_alerts_items.json")
        self.metservice: Any = samples.national_fixture("metservice", "rss_empty.xml")
        self.metservice_documents: dict[str, bytes] = {}
        self.vigicrues: Any = samples.national_fixture("vigicrues", "InfoVigiCru_subset.geojson")
        self.dwd_provider = Provider(answer=self._dwd_answer, latency_ticks=0)
        self.eccc_provider = Provider(answer=lambda request: self._plain(self.eccc), latency_ticks=0)
        self.metservice_provider = Provider(answer=self._metservice_answer, latency_ticks=0)
        self.vigicrues_provider = Provider(answer=self._vigicrues_answer, latency_ticks=0)
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

    @staticmethod
    def _plain(content: Any) -> httpx.Response:
        return httpx.Response(content) if isinstance(content, int) else httpx.Response(200, content=content)

    def _dwd_answer(self, request: httpx.Request) -> httpx.Response:
        """The DWD server: an ETag, and a 304 to the request that repeats it."""
        if isinstance(self.dwd, int):
            return httpx.Response(self.dwd)
        etag = '"' + hashlib.md5(self.dwd).hexdigest()[:12] + '"'
        if request.headers.get("If-None-Match") == etag:
            return httpx.Response(304)
        return httpx.Response(200, content=self.dwd, headers={"ETag": etag, "Last-Modified": "Tue, 20 Oct 2026 11:50:16 GMT"})

    def _metservice_answer(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/cap/rss"):
            return self._plain(self.metservice)
        return self._plain(self.metservice_documents.get(url, 404))

    def _vigicrues_answer(self, request: httpx.Request) -> httpx.Response:
        """The flow (no validator, as recorded) and the head of the publication image, which changes when the flow does."""
        if request.url.path == "/ftp/img_france.png":
            if isinstance(self.vigicrues, int):
                return httpx.Response(self.vigicrues)
            return httpx.Response(200, headers={"Last-Modified": "Tue, 20 Oct 2026 07:55:36 GMT", "ETag": '"' + hashlib.md5(self.vigicrues).hexdigest()[:12] + '"'})
        return self._plain(self.vigicrues)

    def _nws_answer(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(self.nws) if isinstance(self.nws, int) else httpx.Response(200, content=self.nws)

    @property
    def feed_requests(self) -> list[str]:
        return [r.url.path.rsplit("-atom-", 1)[-1] for r in self.meteoalarm.requests if r.url.path.startswith("/feeds/")]

    @property
    def cap_requests(self) -> list[str]:
        return [r.url.path.rsplit("/", 1)[-1] for r in self.meteoalarm.requests if r.url.path.startswith("/api/v1/warnings/")]


def make_service(
    upstream: Upstream, zones: Optional[list[dict[str, Any]]] = None, national: bool = False, metservice: bool = True
) -> VigilanceService:
    """The service on the simulated upstream. ``national=True`` wires DWD, ECCC, MetService and Vigicrues as well.

    ``metservice=False`` leaves MetService out, as an instance that turned it off (``VIGIE_ENABLE_METSERVICE=0``).
    """
    extra: dict[str, Any] = {}
    if national:
        extra = {
            "dwd": DWDCollector(http_client=upstream.dwd_provider.client()),
            "eccc": ECCCCollector(http_client=upstream.eccc_provider.client()),
            "metservice": MetServiceCollector(http_client=upstream.metservice_provider.client()),
            "vigicrues": VigicruesCollector(http_client=upstream.vigicrues_provider.client()),
        }
    if not metservice:
        extra.pop("metservice", None)
    return VigilanceService(
        meteoalarm=MeteoAlarmCollector(http_client=upstream.meteoalarm.client()),
        meteoalarm_cap=MeteoAlarmCapCollector(http_client=upstream.meteoalarm.client()),
        noaa_nws=NOAANWSCollector(http_client=upstream.nws_provider.client()),
        countries=default_countries(),
        geometries=default_geometries(),
        watched_zones=lambda: zones or [],
        **extra,
    )
