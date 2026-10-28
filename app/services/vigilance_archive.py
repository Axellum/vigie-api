"""The archive of the official warnings (``GET /v1/vigilances/archive``).

Every warning a source lists when the API reads it is archived, in force or not (a cancellation, a warning past its end), so that
the warnings of the past can be read again. What is kept is the warning as the API serves it, the words of the issuer untouched:

- One row per message and per area: the API's ``id`` (one area of one message) and the source's ``identifier``. A warning seen
  again is the same row; an update of the source is a new message, so a new row (Vigicrues has no messages: one row per section
  and per bulletin). The filters of the route are columns of the row (country, source, area, phenomenon, level, times).
- ``first_seen_utc`` and ``last_seen_utc``: when the API first and last saw it in a source's answer. The last sighting is written
  at most every ``ARCHIVE_TOUCH_S`` seconds: it is right to within that.
- The payload: the ``Vigilance`` as ``GET /v1/vigilances`` serves it, every language, compressed. A MeteoAlarm warning's words come
  from its CAP document, which the API asks only for a warning an answer returns: the archive takes them when the API holds the
  document (the row is rewritten when it comes) and asks for nothing itself. The archive sends no request to any provider.
- The shape of the area, when the source gives one (a polygon, a river section), once per shape whatever the number of warnings
  that share it, and the box around it on the row.
- ``vigilance_reads``: for each source and country, the UTC hours of each day in which the API read it with success. A day
  without a warning in the archive means "nothing in force" only if the source was read that day: the API reads a source only for
  a country that is needed (a watched zone, a followed point, a recent request).

``VIGIE_ARCHIVE_KEEP_DAYS`` sets how long a warning is kept after it was last seen (0, the default: for ever).
"""

from __future__ import annotations

import hashlib
import logging
import os
import zlib
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Optional

from app import clock
from app.attribution import METEOALARM_DISCLAIMER, merge_credits
from app.models.schemas import SEVERITY_RANK
from app.models.vigilance import (
    ArchivedVigilance,
    Vigilance,
    VigilanceArchiveDay,
    VigilanceArchiveQuery,
    VigilanceArchiveRead,
    VigilanceArchiveResponse,
    VigilanceArchiveStats,
)
from app.services.storage_wal import SQLiteWALStorage

log = logging.getLogger(__name__)

ARCHIVE_TOUCH_S = 600  # the last sighting of a warning seen again is written at most this often
FORGET_S = 3600  # a warning not seen for this long leaves the memory of the pass (not the archive)
PRUNE_EVERY_S = 86_400
KEEP_DAYS_ENV = "VIGIE_ARCHIVE_KEEP_DAYS"
COMPRESSION_LEVEL = 6
NOTE = (
    "The official warnings this instance saw, as it served them (the words of the issuer untouched; `level` and `phenomenon` are the "
    "API's own, listed in `derived`). A source is read only for a country that is needed (a watched zone, a followed point, a recent "
    "request): a period without a warning here means nothing was in force only if `reads` shows that the source was read then. "
    "`last_seen_utc` is right to within ten minutes. A MeteoAlarm warning has its words when the instance had read its CAP document."
)

# What one pass is given: a warning and what completes it (the CAP document a MeteoAlarm warning's words are in, else None).
Seen = tuple[Vigilance, Any]


def keep_days_from_env() -> int:
    """``VIGIE_ARCHIVE_KEEP_DAYS``: how many days a warning is kept after it was last seen; 0 (the default, or a bad value): for ever."""
    raw = os.environ.get(KEEP_DAYS_ENV, "").strip()
    try:
        value = int(raw) if raw else 0
    except ValueError:
        return 0
    return max(0, value)


def _fingerprint(vigilance: Vigilance, extra: Any) -> tuple[Any, ...]:
    """What changes the archived row of a warning: its times, its level, its status, and whether its words are known."""
    return (
        vigilance.message_type,
        vigilance.level_original,
        vigilance.phenomenon_original,
        vigilance.onset_utc,
        vigilance.effective_utc,
        vigilance.expires_utc,
        vigilance.ends_utc,
        len(vigilance.texts),
        extra is not None,
    )


def _service_of(vigilance: Vigilance) -> Optional[str]:
    """The registry service of a ``swic`` warning (the second part of its id), ``None`` for any other source."""
    if vigilance.source != "swic":
        return None
    parts = vigilance.id.split(":", 2)
    return parts[1] if len(parts) > 2 else None


def _row(vigilance: Vigilance, shape_hash: Optional[str], bounds: Optional[tuple[float, ...]], now: int) -> dict[str, Any]:
    codes = [f"{code.scheme}:{code.value}" for code in vigilance.area.geocodes]
    min_lon, min_lat, max_lon, max_lat = bounds if bounds is not None else (None, None, None, None)
    return {
        "id": vigilance.id,
        "identifier": vigilance.identifier,
        "source": vigilance.source,
        "source_id": _service_of(vigilance),
        "country": vigilance.country,
        "area_desc": vigilance.area.description,
        "area_codes": f",{','.join(codes)}," if codes else None,
        "phenomenon": vigilance.phenomenon.value if vigilance.phenomenon else None,
        "phenomenon_original": vigilance.phenomenon_original,
        "level": vigilance.level.value if vigilance.level else None,
        "level_rank": SEVERITY_RANK[vigilance.level] if vigilance.level else None,
        "level_original": vigilance.level_original,
        "status": vigilance.status.value if vigilance.status else None,
        "message_type": vigilance.message_type,
        "sent_utc": vigilance.sent_utc,
        "effective_utc": vigilance.effective_utc,
        "onset_utc": vigilance.onset_utc,
        "expires_utc": vigilance.expires_utc,
        "ends_utc": vigilance.ends_utc,
        "first_seen_utc": now,
        "last_seen_utc": now,
        "has_texts": 1 if vigilance.texts else 0,
        "shape_hash": shape_hash,
        "min_lon": min_lon,
        "min_lat": min_lat,
        "max_lon": max_lon,
        "max_lat": max_lat,
    }


def _day(utc: float) -> str:
    return datetime.fromtimestamp(utc, tz=timezone.utc).strftime("%Y-%m-%d")


class VigilanceArchive:
    """Archives the warnings the sources list (``observe``) and reads them back (``query``, ``stats``)."""

    def __init__(self, storage: Callable[[], SQLiteWALStorage], keep_days: Optional[int] = None) -> None:
        self._storage = storage  # read at each pass: the application may replace its storage (a test does)
        self.keep_days = keep_days_from_env() if keep_days is None else max(0, int(keep_days))
        self._for: Optional[SQLiteWALStorage] = None
        self._seen: dict[tuple[str, str], tuple[tuple[Any, ...], float]] = {}  # (id, identifier) -> (fingerprint, last written)
        self._reads: dict[tuple[str, str], int] = {}  # (source key, country) -> the hour (epoch hours) last written
        self._forgot_at = 0.0
        self._pruned_at: Optional[float] = None
        self.last_write_utc: Optional[int] = None
        self.last_error: Optional[str] = None

    def _current_storage(self) -> SQLiteWALStorage:
        storage = self._storage()
        if storage is not self._for:  # another database: what the passes remember is about the old one
            self._for = storage
            self._seen.clear()
            self._reads.clear()
            self._pruned_at = None
        return storage

    async def observe(self, reads: list[tuple[str, Optional[str]]], seen: list[Seen], finish: Callable[[Vigilance, Any], Vigilance]) -> None:
        """One pass: the sources that answered (``(source key, country)``) and every warning they listed.

        A warning new or changed is written whole (``finish`` gives it as the API serves it); a warning seen again unchanged has its
        last sighting moved, at most every ``ARCHIVE_TOUCH_S``. Never raises: the archive must not make an answer fail.
        """
        try:
            storage = self._current_storage()
            now = clock.wall()
            now_s = int(now)
            written: list[tuple[dict[str, Any], bytes]] = []
            shapes: dict[str, bytes] = {}
            touched: list[tuple[str, str]] = []
            remembered: dict[tuple[str, str], tuple[tuple[Any, ...], float]] = {}
            for vigilance, extra in seen:
                key = (vigilance.id, vigilance.identifier)
                if key in remembered:
                    continue  # listed twice in one pass (two sources, two countries)
                fingerprint = _fingerprint(vigilance, extra)
                known = self._seen.get(key)
                if known is not None and known[0] == fingerprint:
                    if now - known[1] >= ARCHIVE_TOUCH_S:
                        touched.append(key)
                        remembered[key] = (fingerprint, now)
                    continue
                shape_hash, bounds = None, None
                shape = vigilance.shape
                if shape is not None and not shape.is_empty:
                    wkb = shape.wkb
                    shape_hash = hashlib.sha1(wkb).hexdigest()
                    bounds = tuple(float(b) for b in shape.bounds)
                    shapes.setdefault(shape_hash, zlib.compress(wkb, COMPRESSION_LEVEL))
                served = finish(vigilance, extra)
                payload = zlib.compress(served.model_dump_json().encode("utf-8"), COMPRESSION_LEVEL)
                written.append((_row(served, shape_hash, bounds, now_s), payload))
                remembered[key] = (fingerprint, now)
            hour = now_s // 3600
            read_rows = [
                (source_key, country or "", _day(now), 1 << (hour % 24), now_s)
                for source_key, country in dict.fromkeys(reads)
                if self._reads.get((source_key, country or "")) != hour
            ]
            if written or touched or read_rows:
                await clock.run_blocking(storage.archive_vigilances, written, shapes, touched, read_rows, now_s)
                self._seen.update(remembered)
                for source_key, country, *_ in read_rows:
                    self._reads[(source_key, country)] = hour
                self.last_write_utc = now_s
            self.last_error = None
            if now - self._forgot_at >= ARCHIVE_TOUCH_S:
                self._forgot_at = now
                self._seen = {key: value for key, value in self._seen.items() if now - value[1] < FORGET_S}
            await self._prune_if_due(storage, now)
        except Exception as exc:  # noqa: BLE001 - the archive is a side effect: it never fails an answer
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("vigilance archive: %s", self.last_error)

    async def _prune_if_due(self, storage: SQLiteWALStorage, now: float) -> None:
        if self.keep_days <= 0 or (self._pruned_at is not None and now - self._pruned_at < PRUNE_EVERY_S):
            return
        self._pruned_at = now
        await clock.run_blocking(storage.prune_vigilance_archive, int(now) - self.keep_days * 86_400)

    # ------------------------------------------------------------------ reading it back

    async def query(self, query: VigilanceArchiveQuery) -> VigilanceArchiveResponse:
        """The archived warnings that match, the most recently first seen first, and the reads of their sources."""
        storage = self._current_storage()
        min_rank = SEVERITY_RANK[query.min_level] if query.min_level is not None else None
        total, rows = await clock.run_blocking(
            lambda: storage.query_vigilance_archive(
                country=query.country,
                area=query.area,
                source=query.source,
                phenomenon=query.phenomenon.value if query.phenomenon else None,
                level=query.level.value if query.level else None,
                min_level_rank=min_rank,
                since_utc=query.since_utc,
                until_utc=query.until_utc,
                limit=query.limit,
                offset=query.offset,
            )
        )
        items = [
            ArchivedVigilance(
                first_seen_utc=first, last_seen_utc=last, vigilance=Vigilance.model_validate_json(zlib.decompress(payload))
            )
            for first, last, payload in rows
        ]
        now = clock.wall()
        last_day = _day(min(now, query.until_utc) if query.until_utc is not None else now)
        first_day = _day(query.since_utc) if query.since_utc is not None else _day(now - 6 * 86_400)
        reads = [
            VigilanceArchiveRead(
                source=source_key,
                country=country or None,
                day=day,
                first_utc=first,
                last_utc=last,
                hours=[h for h in range(24) if hours & (1 << h)],
            )
            for source_key, country, day, first, last, hours in await clock.run_blocking(
                storage.vigilance_reads, query.country, first_day, last_day
            )
        ]
        return VigilanceArchiveResponse(
            server_utc=int(now),
            query=query,
            total=total,
            items=items,
            reads=reads,
            keep_days=self.keep_days or None,
            attribution=merge_credits(*(item.vigilance.attribution for item in items)),
            disclaimer=METEOALARM_DISCLAIMER if any(item.vigilance.source == "meteoalarm" for item in items) else None,
            note=NOTE,
        )

    async def stats(self, days: int) -> VigilanceArchiveStats:
        """What the archive took in per UTC day over the last ``days`` days, and what it weighs on disk."""
        storage = self._current_storage()
        now = clock.wall()
        since = int(now) - days * 86_400
        per_day, reads, payload_bytes, shape_bytes = await clock.run_blocking(storage.vigilance_archive_days, since)
        warnings, first_seen, last_seen = await clock.run_blocking(storage.vigilance_archive_summary)
        listed = {day for day, *_ in per_day} | {day for day in reads if day >= _day(since)}
        by_day = {day: (count, size, countries) for day, count, size, countries in per_day}
        return VigilanceArchiveStats(
            server_utc=int(now),
            warnings=warnings,
            first_seen_utc=first_seen,
            last_seen_utc=last_seen,
            payload_bytes=payload_bytes,
            shape_bytes=shape_bytes,
            database_bytes=storage.database_bytes(),
            keep_days=self.keep_days or None,
            days=[
                VigilanceArchiveDay(
                    day=day,
                    warnings_first_seen=by_day.get(day, (0, 0, 0))[0],
                    payload_bytes=by_day.get(day, (0, 0, 0))[1],
                    countries_with_warnings=by_day.get(day, (0, 0, 0))[2],
                    sources_read=reads.get(day, (0, 0))[0],
                    countries_read=reads.get(day, (0, 0))[1],
                )
                for day in sorted(listed, reverse=True)
            ],
        )

    def health(self) -> dict[str, Any]:
        """For ``GET /v1/health``: how many warnings, since when, how long they are kept, and the last write or error."""
        try:
            warnings, first_seen, _ = self._current_storage().vigilance_archive_summary()
        except Exception as exc:  # noqa: BLE001 - the health route reports, it does not fail
            return {"warnings": None, "first_seen_utc": None, "keep_days": self.keep_days or None, "last_error": f"{type(exc).__name__}: {exc}"}
        return {
            "warnings": warnings,
            "first_seen_utc": first_seen,
            "keep_days": self.keep_days or None,
            "last_write_utc": self.last_write_utc,
            "last_error": self.last_error,
        }
