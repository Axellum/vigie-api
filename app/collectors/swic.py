"""The national services of the WMO registry, read as ``Vigilance`` objects: their own CAP feeds, and the WMO's copy of them.

One service of the registry (``SwicSource``) is read like this, at most once per ``SOURCE_TTL_S``:

1. **The service's own feeds** (``capAlertFeed``: one per language for 51 of the 140 records). A feed is an RSS 2.0 or Atom
   *index* of links to CAP documents. It is asked with ``If-None-Match`` / ``If-Modified-Since`` whenever the server gave a
   validator (138 of the 150 feeds that answered on 2026-10-20 did; a ``304`` costs a few hundred bytes). The feed of
   Trinidad and Tobago lists 848 items and gives no validator: its items are ignored once older than ``ITEM_MAX_AGE_S``.
2. **The WMO's copy of the service's feed** (``https://severeweather.wmo.int/v2/cap-alerts/<id>/rss.xml``), only for the
   services that publish on a national host. The copy lags and drops alerts (the project that reads both counted 95 of the
   208 alerts of Nigeria's feed missing from it), so it never replaces the service's own feed: both are read, and an alert is
   in the answer once, by its CAP ``identifier``. A service whose feed is a folder of the WMO's own bucket
   (``cap-sources.s3.amazonaws.com``, 63 of the 107) is not asked twice: the copy is made from that bucket and cannot hold
   what it does not. The copy answered HTTP 404 for 259 of the 260 identifiers asked on 2026-10-20 (the project that reads it
   reports the same since 2026-10-08), so after three misses in a row it is not asked again for an hour.
3. **The CAP documents** the indexes list: each one is downloaded **once** (kept while an index lists it, whatever it was,
   expired or test or cancelled, so that it is not asked again), at most ``MAX_NEW_DOCUMENTS`` per read, newest first; the
   rest come at the next read. An index that says what a document is (an Atom entry with CAP elements, an RSS item of the
   WMO's copy) spares the download of the expired and the test ones. Only addresses on the site of the feed are fetched.

What an alert replaces (CAP ``references``) leaves the answer, whichever document says it. The same warning written once
per language in several feeds (Russia, Kazakhstan, Belarus: one document per language, an identifier each) is one
warning with all its languages.

A service that cannot be read at all raises: the caller serves the last answer marked stale, or says "unavailable", it never
says "nothing in force".
"""

from __future__ import annotations

import asyncio
import ipaddress
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx

from app import clock
from app.collectors.base import BaseCollector, Collected, Fetched
from app.collectors.swic_cap import (
    CapAlert,
    CapError,
    CapInfo,
    child_text,
    children,
    derive_phenomenon,
    fold,
    group_translations,
    instant,
    is_in_force,
    is_warning,
    local,
    parse_alert,
    parse_xml,
    superseded,
    to_vigilances,
    translation_key,
)
from app.collectors.swic_registry import SwicSource
from app.collectors.swic_terms import credit_of
from app.models.vigilance import Vigilance

SOURCE_TTL_S = 300  # 5 minutes: see docs/data-sources.md
HEAVY_FEED_STEP_BYTES = 64 * 1024  # a feed that cannot be asked conditionally waits one more TTL per this many bytes
MAX_SOURCE_TTL_S = 1800  # ... up to half an hour
MIRROR_URL = "https://severeweather.wmo.int/v2/cap-alerts/{source_id}/rss.xml"
HUB_BUCKET_HOSTS = ("cap-sources.s3.amazonaws.com", "alert-feed-worldweather-org.s3.amazonaws.com")
MAX_INDEX_BYTES = 2_000_000
MAX_DOCUMENT_BYTES = 1_000_000
MAX_INDEX_ITEMS = 1_500
MAX_NEW_DOCUMENTS = 60  # per read of one service
DOCUMENT_CONCURRENCY = 4
ITEM_MAX_AGE_S = 14 * 86_400  # an item published longer ago is not read, unless the index says it is still in force
SERVICE_FAILURES_BEFORE_BACKOFF = 3  # a service that failed this many times in a row is asked less and less often
SERVICE_BACKOFF_S = 60.0  # ... first after this long, then twice as long each time
MAX_SERVICE_BACKOFF_S = 3600.0
PERSISTENT_FAILURE_RETRY_S = 3600.0  # a feed that answers 403, 404, 410...: asked again in an hour, not in a minute
MIRROR_MISSES_BEFORE_PAUSE = 3
MIRROR_PAUSE_S = 3600
MIRROR_MISSING_S = 24 * 3600  # a mirror id that answered 404 is not asked again for this long
DOCUMENT_FAILURES_BEFORE_GIVING_UP = 3
DOCUMENT_GIVE_UP_S = 3600
READ_TIMEOUT_S = 90.0  # the whole read of one service

INDEX_ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.9, */*;q=0.5"
CAP_ACCEPT = "application/cap+xml, application/xml;q=0.9, text/xml;q=0.9, */*;q=0.5"

# Second-level labels under which a country files its sites (``gov.in``, ``gob.mx``, ``go.th``...): the site of a host is then three labels.
_SECOND_LEVEL = frozenset({"gov", "gob", "gub", "go", "gouv", "gv", "co", "com", "org", "net", "ac", "edu", "nic", "or", "ne"})
# Sites that host other people's data: a link there must be on the very host of the feed, not merely on the same site.
_SHARED_HOSTING = frozenset({"amazonaws.com", "appspot.com", "cloudfront.net", "azurewebsites.net", "github.io", "herokuapp.com"})


def site_of(host: str) -> str:
    """The registrable name of a host, to tell whether two hosts belong to the same site (``api01.nve.no`` and ``cap.nve.no``)."""
    labels = [label for label in host.lower().strip(".").split(".") if label]
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def may_follow(link: str, feed_url: str) -> bool:
    """Whether a link taken from a feed may be fetched: http(s), a name (never an address or ``localhost``), on the site of the feed.

    A feed that says "the document is over there" must not be able to send this server to another site, nor to an address of its own
    network. On a site that hosts other people's data (an S3 bucket) the very host of the feed is required.
    """
    parts, feed = urlsplit(link), urlsplit(feed_url)
    host, feed_host = (parts.hostname or "").lower(), (feed.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host or not feed_host or len(link) > 2048:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    if host == "localhost" or host.endswith((".local", ".localhost", ".internal", ".lan")):
        return False
    if site_of(feed_host) in _SHARED_HOSTING:
        return host == feed_host
    return site_of(host) == site_of(feed_host)


def is_hub_bucket(url: str) -> bool:
    return (urlsplit(url).hostname or "") in HUB_BUCKET_HOSTS


def mirror_ids(source: SwicSource) -> list[str]:
    """The ids the WMO's copy may know the service by: its registry id, and the folder of each of its feeds on the Hub's bucket.

    The copy names a service by language (``cm-meteo-en``, ``cm-meteo-fr``) where the registry has one record; the folders of the
    bucket are those names. A service that publishes on a national host has only its registry id to try.
    """
    ids = [source.source_id]
    for feed in source.feeds:
        parts = urlsplit(feed)
        if (parts.hostname or "") in HUB_BUCKET_HOSTS:
            segments = [part for part in parts.path.split("/") if part]
            if len(segments) >= 2 and segments[-2] not in ids:
                ids.append(segments[-2])
    return ids


# ---------------------------------------------------------------------------------------------------- the indexes


@dataclass(frozen=True)
class IndexItem:
    """One entry of a feed: the address of a CAP document and what the entry says of it."""

    link: str
    hint_id: Optional[str] = None  # the entry's own id (an RSS guid): often the CAP identifier
    published_utc: Optional[int] = None
    expires_utc: Optional[int] = None  # CAP elements some indexes carry in each entry (Algeria's Atom, the WMO's copy)
    status: Optional[str] = None


@dataclass(frozen=True)
class IndexState:
    """What one feed said at its last answer."""

    items: tuple[IndexItem, ...] = ()
    entries: int = 0  # entries in the feed (a Jamaican "no active alert" entry is one, with no CAP link)
    updated_utc: Optional[int] = None
    validators: Optional[dict[str, str]] = None
    size: int = 0  # bytes that came over the network for the last full answer (compressed, as sent)


def _feed_date(value: Optional[str]) -> Optional[int]:
    """A date of a feed: ISO 8601 (Atom) or RFC 822 (RSS); a Buddhist-era year is read as Gregorian."""
    if not value or not value.strip():
        return None
    parsed = instant(value)
    if parsed is not None:
        return parsed
    try:
        when = parsedate_to_datetime(value.strip())
    except (TypeError, ValueError):
        return None
    if when.year >= 2400:
        try:
            when = when.replace(year=when.year - 543)
        except ValueError:
            return None
    return int(when.timestamp()) if when.tzinfo is not None else int(when.replace(tzinfo=None).timestamp())


def _entry_date(entry: ET.Element) -> Optional[int]:
    for name in ("updated", "published", "pubDate", "date"):
        value = _feed_date(child_text(entry, name))
        if value is not None:
            return value
    return None


def _is_the_feed(link: str, feed_url: str) -> bool:
    """Whether a link is the feed itself (Jamaica's "no active alert" entry links to its own address, in http while the registry says https)."""
    a, b = urlsplit(link), urlsplit(feed_url)
    return (a.hostname or "").lower() == (b.hostname or "").lower() and a.path.rstrip("/") == b.path.rstrip("/") and a.query == b.query


def _atom_link(entry: ET.Element, feed_url: str) -> Optional[str]:
    """The address of the CAP document of an Atom entry: the link typed ``application/cap+xml``, else a link that is not the feed itself."""
    plain: Optional[str] = None
    for link in children(entry, "link"):
        href = (link.get("href") or "").strip()
        if not href:
            continue
        if "cap+xml" in (link.get("type") or ""):
            return href
        if plain is None and (link.get("rel") or "alternate") in ("alternate", "related") and not _is_the_feed(href, feed_url):
            plain = href
    return plain


def parse_index(content: bytes, feed_url: str) -> IndexState:
    """The items of an RSS 2.0 or Atom feed that index CAP documents. Raises ``CapError`` for anything else."""
    root = parse_xml(content, "the feed")
    kind = local(root.tag)
    if kind not in ("rss", "feed"):
        raise CapError(f"the feed is neither RSS nor Atom ({root.tag})")
    items: list[IndexItem] = []
    if kind == "rss":
        channel = next(iter(children(root, "channel")), None)
        if channel is None:
            raise CapError("the RSS feed has no channel")
        entries = children(channel, "item")
        updated = _feed_date(child_text(channel, "lastBuildDate")) or _feed_date(child_text(channel, "pubDate"))
        for item in entries[:MAX_INDEX_ITEMS]:
            guid = child_text(item, "guid")
            link = child_text(item, "link") or (guid if guid and guid.startswith(("http://", "https://")) else None)
            if link is None or _is_the_feed(link, feed_url):
                continue
            items.append(
                IndexItem(
                    link=link,
                    hint_id=guid if guid and not guid.startswith(("http://", "https://")) else child_text(item, "identifier"),
                    published_utc=_entry_date(item),
                    expires_utc=_feed_date(child_text(item, "expires")),
                    status=(child_text(item, "status") or "").lower() or None,
                )
            )
    else:
        entries = children(root, "entry")
        updated = _feed_date(child_text(root, "updated"))
        for entry in entries[:MAX_INDEX_ITEMS]:
            link = _atom_link(entry, feed_url)
            if link is None:
                continue
            items.append(
                IndexItem(
                    link=link,
                    hint_id=child_text(entry, "identifier"),
                    published_utc=_entry_date(entry),
                    expires_utc=_feed_date(child_text(entry, "expires")),
                    status=(child_text(entry, "status") or "").lower() or None,
                )
            )
    return IndexState(items=tuple(items), entries=len(entries), updated_utc=updated)


# ---------------------------------------------------------------------------------------------------- one service


@dataclass(frozen=True)
class SwicFeed:
    """What the API holds of one service of the registry: its warnings, and how the reading went."""

    source_id: str
    vigilances: tuple[Vigilance, ...] = ()
    documents: int = 0  # CAP documents the feeds list (after the ones too old or called off by the index)
    unreadable: int = 0  # documents that are gone or are not a CAP alert
    pending: int = 0  # documents left for the next read (``MAX_NEW_DOCUMENTS``) or that failed this time
    old_items: int = 0  # items too old to be read, or called off by the index
    skipped_areas: int = 0  # areas beyond what is read of a document
    feeds_read: int = 0
    feeds_total: int = 0
    feed_errors: tuple[str, ...] = ()
    mirror: str = "not asked"  # "read", "not asked", "paused" or "missing"
    updated_utc: Optional[int] = None
    refresh_s: float = SOURCE_TTL_S  # how long this answer is kept before the feeds are asked again


@dataclass
class _Document:
    alert: Optional[CapAlert]
    gone_reason: Optional[str] = None  # set when there is no alert: "gone", "unreadable"
    seen_utc: float = 0.0


@dataclass
class _Warning:
    """One warning being assembled: an alert's group of infos, and the same warning read in other language feeds."""

    alert: CapAlert
    group: list[CapInfo]
    variant: int
    extras: list[tuple[CapAlert, list[CapInfo]]] = field(default_factory=list)

    def languages(self) -> set[str]:
        return {fold(info.language) for info in self.group} | {fold(info.language) for _, group in self.extras for info in group}

    def phenomenon(self) -> Any:
        return derive_phenomenon([info.event for info in self.group])

    def is_translation_of(self, other: "_Warning") -> bool:
        """Another document for the same warning (its own identifier or the same one): languages all named and not shared, no contradicting phenomenon."""
        mine, theirs = self.languages(), other.languages()
        if "" in mine or "" in theirs or mine & theirs:
            return False
        a, b = self.phenomenon(), other.phenomenon()
        return a is None or b is None or a == b


class SwicCollector(BaseCollector[SwicFeed]):
    """The services of the WMO registry: feeds, the WMO's copy, and CAP documents, one cache entry per service."""

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="wmo_swic",
            name="WMO SWIC registry services (national CAP feeds)",
            domain="warnings",
            ttl_seconds=SOURCE_TTL_S,
            timeout_seconds=25.0,  # PAGASA's TLS handshake alone took 10 to 45 seconds from the author's PC
            http_client=http_client,
        )
        self._indexes: dict[str, IndexState] = {}  # feed address -> its last state (for the validators and the 304)
        self._documents: dict[str, _Document] = {}  # document address -> what it was
        self._listed: dict[str, set[str]] = {}  # service -> the document addresses its indexes list now
        self._failures: dict[str, tuple[int, float]] = {}  # document address -> (failures, last failure)
        self._mirror_misses = 0
        self._mirror_down_until = 0.0
        self._mirror_missing: dict[str, float] = {}  # mirror id -> when it answered 404
        self._service_failures: dict[str, tuple[int, float]] = {}  # service -> (failures in a row, when the last one was)
        self._persistent: set[str] = set()  # services whose every feed answered a client error at the last read

    # ------------------------------------------------------------------ one circuit per service
    #
    # A hundred services share this collector: a feed behind a bot check, a server that never answers, must not close the circuit
    # of the others. So the circuit is kept for each service, and the one of the collector never opens.

    def _record_failure(self, error_msg: str, cache_key: str = "global") -> None:
        count = self._service_failures.get(cache_key, (0, 0.0))[0] + 1
        self._service_failures[cache_key] = (count, clock.monotonic())
        self.last_error = error_msg[:240]
        self.consecutive_failures = sum(1 for failures, _ in self._service_failures.values() if failures >= SERVICE_FAILURES_BEFORE_BACKOFF)

    def _record_success(self, elapsed_ms: float, cache_key: str, data: Any, ttl_seconds: Optional[float] = None, validators: Optional[dict[str, str]] = None) -> None:
        self._service_failures.pop(cache_key, None)
        self._persistent.discard(cache_key)
        super()._record_success(elapsed_ms, cache_key, data, ttl_seconds, validators)
        self.consecutive_failures = sum(1 for failures, _ in self._service_failures.values() if failures >= SERVICE_FAILURES_BEFORE_BACKOFF)

    def _can_attempt_network(self, cache_key: str = "global") -> bool:
        failures, last = self._service_failures.get(cache_key, (0, 0.0))
        if failures and cache_key in self._persistent:
            return clock.monotonic() - last >= PERSISTENT_FAILURE_RETRY_S
        if failures < SERVICE_FAILURES_BEFORE_BACKOFF:
            return True
        wait = min(MAX_SERVICE_BACKOFF_S, SERVICE_BACKOFF_S * 2 ** (failures - SERVICE_FAILURES_BEFORE_BACKOFF))
        return clock.monotonic() - last >= wait

    # ------------------------------------------------------------------ the public entry

    async def fetch_source(self, source: SwicSource, allow_stale_on_error: bool = True) -> Fetched[SwicFeed]:
        """The warnings of a service, from the cache while it is fresh, else one read of its feeds."""
        return await self.fetch_ex(cache_key=source.source_id, source=source, allow_stale_on_error=allow_stale_on_error)

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Collected[SwicFeed]:
        source: SwicSource = kwargs["source"]
        try:
            feed = await asyncio.wait_for(self._read(client, source), READ_TIMEOUT_S)
            return Collected(feed, fresh_for_s=feed.refresh_s)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"{source.source_id}: the read took more than {READ_TIMEOUT_S:g} seconds") from exc

    # ------------------------------------------------------------------ the feeds

    async def _read_index(self, client: httpx.AsyncClient, url: str) -> IndexState:
        """One feed, asked conditionally; the state it had on a ``304``. Raises on an answer that is not a feed."""
        previous = self._indexes.get(url)
        headers = {"Accept": INDEX_ACCEPT}
        if previous is not None and previous.validators:
            if previous.validators.get("ETag"):
                headers["If-None-Match"] = previous.validators["ETag"]
            if previous.validators.get("Last-Modified"):
                headers["If-Modified-Since"] = previous.validators["Last-Modified"]
        response = await self._get_capped(client, url, max_bytes=MAX_INDEX_BYTES, headers=headers)
        if response.status_code == 304 and previous is not None:
            return previous
        if response.status_code == 204:  # no content: the service has no warning (Iceland's feed does this)
            state = IndexState()
        else:
            response.raise_for_status()
            state = parse_index(response.content, url)
        validators = {name: response.headers[name] for name in ("ETag", "Last-Modified") if response.headers.get(name)}
        wire = int(response.extensions.get("wire_bytes") or len(response.content))
        state = IndexState(state.items, state.entries, state.updated_utc, validators or None, wire)
        self._indexes[url] = state
        return state

    async def _read_mirror(self, client: httpx.AsyncClient, source: SwicSource) -> tuple[str, list[tuple[str, IndexState]]]:
        """The WMO's copy of a service: ``(what happened, (address, feed) read)``. Sober: see the module docstring."""
        if source.meteoalarm or all(is_hub_bucket(feed) for feed in source.feeds):
            return "not asked", []
        now = clock.monotonic()
        if now < self._mirror_down_until:
            return "paused", []
        read: list[tuple[str, IndexState]] = []
        outcome = "missing"
        for ident in mirror_ids(source):
            if now < self._mirror_missing.get(ident, 0.0):
                continue
            url = MIRROR_URL.format(source_id=ident)
            try:
                state = await self._read_index(client, url)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (404, 410):
                    self._mirror_missing[ident] = now + MIRROR_MISSING_S
                self._mirror_misses += 1
            except (httpx.TransportError, CapError, ValueError):
                self._mirror_misses += 1
            else:
                self._mirror_misses = 0
                read.append((url, state))
                outcome = "read"
            if self._mirror_misses >= MIRROR_MISSES_BEFORE_PAUSE:
                self._mirror_down_until = now + MIRROR_PAUSE_S
                break
        return outcome, read

    # ------------------------------------------------------------------ the documents

    async def _load_documents(self, client: httpx.AsyncClient, links: list[str]) -> tuple[int, int]:
        """Download the documents not held yet (at most ``MAX_NEW_DOCUMENTS``); returns how many failed now and how many are left."""
        now = clock.monotonic()
        wanted = [link for link in links if link not in self._documents and now >= self._give_up_until(link)]
        batch, left = wanted[:MAX_NEW_DOCUMENTS], max(0, len(wanted) - MAX_NEW_DOCUMENTS)
        gate = asyncio.Semaphore(DOCUMENT_CONCURRENCY)

        async def load(link: str) -> bool:
            async with gate:
                try:
                    response = await self._get_capped(client, link, max_bytes=MAX_DOCUMENT_BYTES, headers={"Accept": CAP_ACCEPT})
                    if response.status_code in (404, 410):
                        self._documents[link] = _Document(None, "gone", clock.wall())
                        return True
                    response.raise_for_status()
                    try:
                        self._documents[link] = _Document(parse_alert(response.content, link), None, clock.wall())
                    except CapError:
                        self._documents[link] = _Document(None, "unreadable", clock.wall())
                    self._failures.pop(link, None)
                    return True
                except (httpx.HTTPError, ValueError):
                    count = self._failures.get(link, (0, 0.0))[0] + 1
                    self._failures[link] = (count, clock.monotonic())
                    if count >= DOCUMENT_FAILURES_BEFORE_GIVING_UP:
                        self._documents[link] = _Document(None, "unreadable", clock.wall())
                    return False

        results = await asyncio.gather(*(load(link) for link in batch))
        return sum(1 for ok in results if not ok), left

    def _give_up_until(self, link: str) -> float:
        failures = self._failures.get(link)
        if failures is None or failures[0] < DOCUMENT_FAILURES_BEFORE_GIVING_UP:
            return 0.0
        return failures[1] + DOCUMENT_GIVE_UP_S

    # ------------------------------------------------------------------ one read of a service

    async def _read(self, client: httpx.AsyncClient, source: SwicSource) -> SwicFeed:
        now_wall = clock.wall()
        results = await asyncio.gather(*(self._read_index(client, url) for url in source.feeds), return_exceptions=True)
        mirror_outcome, mirror_read = await self._read_mirror(client, source)

        authority: list[tuple[str, IndexState]] = []
        errors: list[str] = []
        statuses: list[Optional[int]] = []
        for url, result in zip(source.feeds, results, strict=True):
            if isinstance(result, IndexState):
                authority.append((url, result))
            elif isinstance(result, (httpx.HTTPError, ValueError)):
                errors.append(f"{urlsplit(url).hostname}: {type(result).__name__}: {str(result)[:80]}")
                statuses.append(result.response.status_code if isinstance(result, httpx.HTTPStatusError) else None)
            else:
                raise result  # a bug or a cancellation: let it show
        if not authority and not mirror_read:
            if statuses and all(s is not None and 400 <= s < 500 and s not in (408, 429) for s in statuses):
                self._persistent.add(source.source_id)
            raise ValueError("no feed answered: " + ("; ".join(errors) or "the registry lists none"))

        # Where a link may lead: the site of the feed it came from; for the WMO's copy, also the sites of the service's own feeds and the Hub's bucket.
        links: dict[str, IndexItem] = {}
        old = 0
        hub = f"https://{HUB_BUCKET_HOSTS[0]}/"
        for from_mirror, feeds in ((False, authority), (True, mirror_read)):
            for feed_url, state in feeds:
                references = [feed_url, *source.feeds, hub] if from_mirror else [feed_url]
                for item in state.items:
                    if not any(may_follow(item.link, reference) for reference in references):
                        continue
                    if item.status is not None and item.status != "actual":
                        old += 1  # a test or an exercise, said by the index itself
                    elif item.expires_utc is not None and item.expires_utc < now_wall:
                        old += 1
                    elif item.expires_utc is None and item.published_utc is not None and item.published_utc < now_wall - ITEM_MAX_AGE_S:
                        old += 1
                    else:
                        links.setdefault(item.link, item)
        self._listed_update(source.source_id, set(links))

        failed, left = await self._load_documents(client, list(links))

        alerts: list[CapAlert] = []
        unreadable = 0
        for link in links:
            document = self._documents.get(link)
            if document is None:
                continue
            if document.alert is None:
                unreadable += 1
            else:
                alerts.append(document.alert)
        vigilances, skipped_areas = self._warnings(source, alerts, now_wall)
        updated = max((state.updated_utc for _, state in [*authority, *mirror_read] if state.updated_utc is not None), default=None)
        return SwicFeed(
            source_id=source.source_id,
            vigilances=tuple(vigilances),
            documents=len(links),
            unreadable=unreadable,
            pending=left + failed,
            old_items=old,
            skipped_areas=skipped_areas,
            feeds_read=len(authority),
            feeds_total=len(source.feeds),
            feed_errors=tuple(errors),
            mirror=mirror_outcome,
            updated_utc=updated,
            refresh_s=refresh_period_s([state for _, state in [*authority, *mirror_read]]),
        )

    def _listed_update(self, source_id: str, links: set[str]) -> None:
        """Remember what the indexes list now, and forget the documents that no index of any service lists any more."""
        previous = self._listed.get(source_id, set())
        self._listed[source_id] = links
        still_listed = set().union(*self._listed.values()) if self._listed else set()
        for link in previous - links:
            if link not in still_listed:
                self._documents.pop(link, None)
                self._failures.pop(link, None)

    @staticmethod
    def _warnings(source: SwicSource, alerts: list[CapAlert], now: float) -> tuple[list[Vigilance], int]:
        """The warnings of the alerts read: replaced ones left out, languages of one warning joined, each turned into ``Vigilance``."""
        # One alert is one (sender, identifier) in one set of languages: the document of a feed and its copy are one, but a service
        # that writes one document per language under one identifier has two (the translations of one warning, joined below).
        unique: dict[tuple[str, str, frozenset[str]], CapAlert] = {}
        for alert in alerts:  # the first copy of an alert wins: the service's own feed is read before the WMO's copy
            unique.setdefault((alert.sender, alert.identifier, frozenset(fold(info.language) for info in alert.infos)), alert)
        gone = superseded(list(unique.values()))
        live = [alert for key, alert in unique.items() if key[:2] not in gone and is_warning(alert)]
        warnings: list[_Warning] = []
        by_key: dict[tuple[object, ...], list[_Warning]] = {}
        skipped = 0
        for alert in live:
            skipped += sum(info.skipped_areas for info in alert.infos)
            for variant, group in enumerate(group_translations(alert.infos)):
                if not is_in_force(alert, group, now):
                    continue
                candidate = _Warning(alert, group, variant)
                key = translation_key(alert, group)
                twins = [w for w in by_key.get(key, []) if candidate.is_translation_of(w)]
                if len(twins) == 1:  # exactly one: with two, which is which cannot be told, and no text goes on the wrong warning
                    twins[0].extras.append((alert, group))
                    continue
                warnings.append(candidate)
                by_key.setdefault(key, []).append(candidate)
        credit = credit_of(source)
        out: list[Vigilance] = []
        for w in warnings:
            out.extend(
                to_vigilances(
                    w.alert,
                    w.group,
                    w.variant,
                    source_id=source.source_id,
                    country=source.country or "",
                    credit=credit,
                    extra_infos=tuple(w.extras),
                )
            )
        return out, skipped


def refresh_period_s(states: list[IndexState]) -> float:
    """How long a service's answer is kept before its feeds are asked again.

    A feed that gives a validator is asked every ``SOURCE_TTL_S``: an unchanged one costs a few hundred bytes. A feed that gives
    none is downloaded whole each time, so it waits one more ``SOURCE_TTL_S`` per ``HEAVY_FEED_STEP_BYTES`` it weighs on the wire,
    up to ``MAX_SOURCE_TTL_S`` (Trinidad and Tobago's feed, 641 KB of XML and 67 KB sent: every 10 minutes).
    """
    heavy = sum(state.size for state in states if not state.validators)
    steps = max(1, -(-heavy // HEAVY_FEED_STEP_BYTES))
    return float(min(MAX_SOURCE_TTL_S, SOURCE_TTL_S * steps))


def notes_of(feed: SwicFeed) -> list[str]:
    """What is worth telling in the answer about how a service was read (the caller adds them to ``coverage``)."""
    notes: list[str] = []
    if feed.feed_errors:
        notes.append(f"{len(feed.feed_errors)} of the {feed.feeds_total} feeds of the service did not answer ({feed.feed_errors[0]}).")
    if feed.mirror == "read" and feed.feeds_read == 0:
        notes.append("The service's own feed did not answer: its warnings come from the WMO's copy, which can lag.")
    if feed.pending:
        notes.append(f"{feed.pending} CAP documents are not read yet (at most {MAX_NEW_DOCUMENTS} new ones per read, the others at the next).")
    if feed.unreadable:
        notes.append(f"{feed.unreadable} documents of the feed could not be read as a CAP alert (gone, or not CAP).")
    if feed.skipped_areas:
        notes.append(f"{feed.skipped_areas} areas beyond the first of their alert are not read.")
    return notes
