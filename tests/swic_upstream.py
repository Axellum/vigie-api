"""The WMO registry and the feeds of its services, simulated from what was measured on 2026-10-20 (``tests/fixtures/swic/profile.json``).

A simulated upstream behind an ``httpx.MockTransport`` (no network): it answers the registry, every feed address the registry
lists, the CAP documents these feeds point to, and the WMO's copy of the feeds (which answers 404, as it did for 259 of the 260
addresses asked), and **counts every request and every byte it sends**.

What is recorded and what is synthetic, so that nobody reads the figures for more than they are:

- Recorded: for each feed, the bytes that came over the network (compressed, as sent), whether the server gives a validator
  (``ETag`` / ``Last-Modified``), the status it answered (403 behind a bot check, 404, a connection that was refused...), how many
  entries it listed and how many of them the API would read (published in the last 14 days or still in force).
- Synthetic: the text of the indexes and of the CAP documents (a plain alert of the right size, padded with an XML comment to the
  recorded weight), the dates of the entries (spread evenly over 14 days) and the rhythm at which new alerts appear (the entries of
  the last 14 days divided by 14, per day). A document weighs the average of the two or three that were downloaded
  (4,500 bytes where none was): it is counted at its size **uncompressed**, so the byte totals of the documents are an upper bound.

``python -m tests.swic_upstream`` plays one day and prints the table quoted in ``docs/data-sources.md``.
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import json
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx

from app.collectors.swic_registry import REGISTRY_URL, SNAPSHOT_PATH
from tests.helpers import SimClock, http_date

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "swic"
PROFILE: dict[str, Any] = json.loads((FIXTURES / "profile.json").read_text(encoding="utf-8"))
REGISTRY_HOST = urlsplit(REGISTRY_URL).hostname
DAY_S = 86_400
WINDOW_S = 14 * DAY_S
ERROR_ANSWER_BYTES = 600  # the head of an error answer

CAP_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"><identifier>{identifier}</identifier><sender>{sender}</sender>'
    "<sent>{sent}</sent><status>Actual</status><msgType>Alert</msgType><scope>Public</scope>"
    "<info><language>en</language><category>Met</category><event>Heavy rain</event><urgency>Expected</urgency>"
    "<severity>Moderate</severity><certainty>Likely</certainty><effective>{sent}</effective><expires>{expires}</expires>"
    "<headline>Synthetic alert {n}</headline><description>Synthetic alert of the sobriety simulation.</description>"
    "<area><areaDesc>Zone {n}</areaDesc><polygon>0.0,0.0 0.0,1.0 1.0,1.0 1.0,0.0 0.0,0.0</polygon></area></info></alert>"
)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pad(body: str, size: int) -> bytes:
    """The body, followed by an XML comment that brings it to ``size`` bytes (never shrunk)."""
    raw = body.encode("utf-8")
    missing = size - len(raw) - 8
    return raw + b"\n<!--" + b"x" * missing + b"-->" if missing > 0 else raw


def _wire(body: str, size: int, seed: str) -> bytes:
    """The body gzip-compressed, with incompressible filler (in an XML comment) until it weighs ``size`` bytes on the wire.

    The feeds that were measured are mostly sent compressed (Mexico's 255 KB of XML weigh 13 KB on the wire); the synthetic index
    is repetitive and shrinks as much, and the filler stands for the text it does not have. Never smaller than the index itself.
    """
    base = gzip.compress(body.encode("utf-8"), compresslevel=6, mtime=0)
    missing = size - len(base) - 12
    if missing <= 0:
        return base
    filler = base64.b64encode(random.Random(seed).randbytes(missing)).decode("ascii")
    return gzip.compress((body + "\n<!--" + filler + "-->").encode("utf-8"), compresslevel=6, mtime=0)


@dataclass
class _Feed:
    service: str
    url: str
    profile: dict[str, Any]
    host: str = ""
    directory: str = ""
    version: int = -1  # how many new alerts had appeared at the last answer
    changed_at: float = 0.0

    def __post_init__(self) -> None:
        parts = urlsplit(self.url)
        self.host = parts.hostname or ""
        self.directory = parts.path.rsplit("/", 1)[0]

    @property
    def recent(self) -> int:
        return int(self.profile.get("recent") or 0)

    @property
    def has_validator(self) -> bool:
        return bool(self.profile.get("etag") or self.profile.get("last_modified"))

    def arrivals_per_day(self) -> float:
        return self.recent / 14.0

    def published(self, k: int, epoch0: float) -> float:
        """When alert ``k`` appeared: the first ``recent`` before the start, spread over 14 days, then at the daily rhythm."""
        if k < self.recent:
            return epoch0 - WINDOW_S + (k + 1) * WINDOW_S / max(1, self.recent) - 1.0
        return epoch0 + (k - self.recent + 1) * DAY_S / max(self.arrivals_per_day(), 1e-9)


@dataclass
class SwicWorld:
    """The simulated upstream of the registry and of its services."""

    clock: SimClock
    latency_ticks: int = 0
    calls: Counter[tuple[str, str]] = field(default_factory=Counter)  # (service id, kind) -> requests
    bytes: Counter[tuple[str, str]] = field(default_factory=Counter)  # (service id, kind) -> bytes sent
    not_modified: Counter[str] = field(default_factory=Counter)  # service id -> requests answered 304
    registry_calls: int = 0
    registry_bytes: int = 0
    registry_not_modified: int = 0

    def __post_init__(self) -> None:
        self.epoch0 = self.clock.time()
        self._feeds: dict[str, _Feed] = {}
        self._by_host: dict[str, set[str]] = {}
        self._wired: dict[tuple[str, int, int], bytes] = {}
        for service, feeds in PROFILE["services"].items():
            if service in PROFILE["excluded"]:
                continue
            for entry in feeds:
                feed = _Feed(service, entry["url"], entry)
                self._feeds[entry["url"]] = feed
                self._by_host.setdefault(feed.host, set()).add(service)
        self._registry_body = gzip.decompress(SNAPSHOT_PATH.read_bytes())
        self._registry_modified = http_date(self.epoch0 - 3 * DAY_S)

    # ---------------------------------------------------------------- routing

    def knows(self, request: httpx.Request) -> bool:
        return request.url.host == REGISTRY_HOST or str(request.url).split("#")[0] in self._feeds or self._doc_of(request) is not None

    def source_of(self, request: httpx.Request) -> str:
        """The provider id a request is counted under: the registry has its own, the services share ``wmo_swic``."""
        if request.url.host == REGISTRY_HOST and request.url.path == urlsplit(REGISTRY_URL).path:
            return "wmo_swic_registry"
        return "wmo_swic"

    def _doc_of(self, request: httpx.Request) -> Optional[tuple[_Feed, int]]:
        """(the feed, the number of the alert) a document address belongs to: ``<feed directory>/alert-<k>.xml``."""
        url = request.url
        name = url.path.rsplit("/", 1)[-1]
        if not (name.startswith("alert-") and name.endswith(".xml")):
            return None
        for feed in self._feeds.values():
            if feed.host == url.host and url.path == f"{feed.directory}/{name}":
                try:
                    return feed, int(name[6:-4])
                except ValueError:
                    return None
        return None

    # ---------------------------------------------------------------- answers

    async def respond(self, request: httpx.Request) -> httpx.Response:
        for _ in range(self.latency_ticks):
            await asyncio.sleep(0)
        url = str(request.url).split("#")[0]
        if request.url.host == REGISTRY_HOST and request.url.path == urlsplit(REGISTRY_URL).path:
            return self._registry(request)
        if request.url.host == REGISTRY_HOST:  # the WMO's copy of a feed: 404, a page of 19 KB
            service = request.url.path.split("/")[3] if request.url.path.count("/") >= 4 else "mirror"
            size = int(PROFILE["mirror_404_bytes"])
            self.calls[(service, "mirror")] += 1
            self.bytes[(service, "mirror")] += size
            return httpx.Response(404, content=b"x" * size, headers={"Content-Type": "text/html"})
        feed = self._feeds.get(url)
        if feed is not None:
            return self._index(feed, request)
        found = self._doc_of(request)
        assert found is not None, url
        return self._document(*found)

    def _registry(self, request: httpx.Request) -> httpx.Response:
        self.registry_calls += 1
        if request.headers.get("If-Modified-Since") == self._registry_modified:
            self.registry_not_modified += 1
            self.registry_bytes += 300
            return httpx.Response(304)
        size = int(PROFILE["registry"]["wire_bytes"])
        self.registry_bytes += size
        return httpx.Response(200, content=self._registry_body, headers={"Last-Modified": self._registry_modified})

    def _count(self, feed: _Feed, kind: str, size: int) -> None:
        self.calls[(feed.service, kind)] += 1
        self.bytes[(feed.service, kind)] += size

    def _index(self, feed: _Feed, request: httpx.Request) -> httpx.Response:
        profile = feed.profile
        if profile.get("error"):  # a connection refused, a timeout, a bad certificate: the request is counted, nothing comes back
            self._count(feed, "index", 0)
            raise httpx.ConnectError(f"{profile['error']} (simulated, as measured on 2026-10-20)", request=request)
        if profile["status"] != 200:
            self._count(feed, "index", ERROR_ANSWER_BYTES)  # the collector does not read an error page: its head only
            return httpx.Response(int(profile["status"]), content=b"x" * ERROR_ANSWER_BYTES)
        now = self.clock.time()
        arrived = 0 if feed.recent == 0 else max(0, int((now - self.epoch0) * feed.arrivals_per_day() / DAY_S))
        etag = f'"{feed.service}-{arrived}"'
        modified = http_date(self.epoch0 - 3600 if arrived == 0 else feed.published(feed.recent + arrived - 1, self.epoch0))
        if feed.has_validator and (
            request.headers.get("If-None-Match") == etag or request.headers.get("If-Modified-Since") == modified
        ):
            self.not_modified[feed.service] += 1
            self._count(feed, "index", 300)
            return httpx.Response(304)
        key = (feed.url, arrived, int(now // 3600))
        body = self._wired.get(key)
        if body is None:
            body = self._wired[key] = _wire(self._index_body(feed, arrived, now), int(profile["wire_bytes"]), feed.url)
        self._count(feed, "index", len(body))
        headers = {"Content-Encoding": "gzip"}
        if profile.get("etag"):
            headers["ETag"] = etag
        if profile.get("last_modified"):
            headers["Last-Modified"] = modified
        return httpx.Response(200, content=body, headers=headers)

    def _index_body(self, feed: _Feed, arrived: int, now: float) -> str:
        """An RSS index of the alerts of the last 14 days (``recent`` of them at the start, one more at each arrival) and a few old ones."""
        items = []
        for k in range(feed.recent + arrived):
            when = feed.published(k, self.epoch0)
            if when < now - WINDOW_S:
                continue
            items.append(
                f"<item><title>Alert {k}</title><link>https://{feed.host}{feed.directory}/alert-{k}.xml</link>"
                f'<guid isPermaLink="false">{feed.service}-{k}</guid><pubDate>{http_date(when)}</pubDate></item>'
            )
        for n in range(min(20, max(0, int(feed.profile.get("entries") or 0) - feed.recent))):  # listed and long gone: never downloaded
            when = now - WINDOW_S - (n + 1) * 3600
            items.append(
                f"<item><title>Old {n}</title><link>https://{feed.host}{feed.directory}/alert-old-{n}.xml</link>"
                f'<guid isPermaLink="false">{feed.service}-old-{n}</guid><pubDate>{http_date(when)}</pubDate></item>'
            )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel><title>Simulated feed</title>'
            f"<link>{feed.url}</link>" + "".join(items) + "</channel></rss>"
        )

    def _document(self, feed: _Feed, k: int) -> httpx.Response:
        when = feed.published(k, self.epoch0)
        body = CAP_TEMPLATE.format(
            identifier=f"{feed.service}-{k}", sender=f"{feed.service}@example.org", sent=_iso(when), expires=_iso(when + 12 * 3600), n=k
        )
        raw = _pad(body, int(feed.profile.get("doc_bytes") or 4500))
        self._count(feed, "document", len(raw))
        return httpx.Response(200, content=raw)

    # ---------------------------------------------------------------- the totals

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.respond))

    def totals(self) -> dict[str, int]:
        kinds = ("index", "document", "mirror")
        out = {f"{kind}_requests": sum(n for (_, k), n in self.calls.items() if k == kind) for kind in kinds}
        out.update({f"{kind}_bytes": sum(n for (_, k), n in self.bytes.items() if k == kind) for kind in kinds})
        out["registry_requests"] = self.registry_calls
        out["registry_bytes"] = self.registry_bytes
        out["requests"] = sum(self.calls.values()) + self.registry_calls
        out["bytes"] = sum(self.bytes.values()) + self.registry_bytes
        return out
