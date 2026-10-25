"""The pilot: read each new ECMWF run, compute the province levels, store them (ADR 0011 and its amendment).

On when ``VIGIE_PREALERT_TR`` is ``1`` / ``true`` / ``yes`` / ``on`` (off by default: nothing is downloaded, nothing is imported
from ``numpy`` or ``eccodes``, and ``GET /v1/experimental/prealert`` answers 404).

**The model is the one the thresholds were learned on**: ECMWF IFS 0.25 degree open data (CC BY 4.0), read from ECMWF's mirror on
AWS (https://registry.opendata.aws/ecmwf-forecasts/), the same files as the backtest. The runs of 00 and 12 UTC have a lead every 3
hours; those of 06 and 18 UTC are not read.

**Which run.** Every ``CHECK_EVERY_S`` the newest run of the last 30 hours whose index of lead ``LAST_LEAD_H`` answers is looked for,
newest first (a ``HEAD`` each). A run already stored is not read again.

**A run** is, for each lead from 3 to 72 hours, its JSON index (one line per field, with an offset and a length) and one byte range
per field of ``ECMWF_PARAMS``: 24 x 6 = 144 requests, about 110 MB (whole-world fields of about 0.6 to 1.4 MB, measured on
2026-10-25), sent one after the other. The budget is ours (``ecmwf_opendata`` in ``app/collectors/budget.py``).

**What is stored**: one row per run, local day, province and phenomenon (value and level) in the table ``prealert_tr_levels`` of the
API's database. ``latest_for`` gives the rows of the newest run for a province: the experimental route reads them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from app.collectors.base import BaseCollector, CacheEntry

logger = logging.getLogger(__name__)

ECMWF_MIRROR = "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com"
ECMWF_PARAMS = ("tp", "10fg", "mx2t3", "mn2t3", "ptype", "max_i10fg", "max_2t", "min_2t")
CYCLES = (0, 12)
LAST_LEAD_H = 72
CHECK_EVERY_S = 30 * 60
PAUSE_S = 0.5
LOOK_BACK_H = 30
MAX_FIELD_BYTES = 4_000_000  # a whole-world field weighs 0.6 to 1.4 MB
MAX_INDEX_BYTES = 1_000_000


def enabled(env: Optional[dict[str, str]] = None) -> bool:
    """Whether the pilot runs: ``VIGIE_PREALERT_TR`` is ``1`` / ``true`` / ``yes`` / ``on`` (default: off)."""
    value = (os.environ if env is None else env).get("VIGIE_PREALERT_TR", "0")
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class LevelRow:
    run: str  # "2026-10-25T00:00Z"
    day: str  # local day, "2026-10-25"
    province: str  # "TR-34"
    phenomenon: str
    value: Optional[float]
    level: Optional[int]


@dataclass(frozen=True)
class ShadowRun:
    """The levels of one run; ``rows`` is empty when the run was already stored (nothing was read)."""

    run: datetime
    rows: tuple[LevelRow, ...]


def candidate_runs(now: datetime) -> list[datetime]:
    """The runs of 00 and 12 UTC of the last ``LOOK_BACK_H`` hours, newest first."""
    run = now.replace(minute=0, second=0, microsecond=0, hour=max(c for c in CYCLES if c <= now.hour))
    out = []
    while now - run <= timedelta(hours=LOOK_BACK_H):
        out.append(run)
        run -= timedelta(hours=12)
    return out


def file_url(run: datetime, lead: int) -> str:
    """The address of one lead of a run on the mirror, without its extension (``.index`` or ``.grib2``)."""
    return f"{ECMWF_MIRROR}/{run:%Y%m%d}/{run:%H}z/ifs/0p25/oper/{run:%Y%m%d%H}0000-{lead}h-oper-fc"


def wanted_fields(index_text: str) -> list[tuple[str, int, int]]:
    """``(param, offset, length)`` of the surface fields of ``ECMWF_PARAMS`` in an index; raises when one of the five is missing."""
    found = []
    for line in index_text.splitlines():
        if not line.strip():
            continue
        field = json.loads(line)
        if field.get("levtype") == "sfc" and field.get("param") in ECMWF_PARAMS:
            found.append((str(field["param"]), int(field["_offset"]), int(field["_length"])))
    names = {name for name, _, _ in found}
    for needed in (("tp",), ("10fg", "max_i10fg"), ("mx2t3", "max_2t"), ("mn2t3", "min_2t"), ("ptype",)):
        if not names.intersection(needed):
            raise ValueError(f"the index lists no {needed[0]}")
    return found


def compute_rows(run: datetime, by_lead_raw: dict[int, list[bytes]]) -> tuple[LevelRow, ...]:
    """Decode the fields of one run and compute every province level (CPU work: run it in a thread)."""
    from app.prealert.gfs import TURKEY_BOX, decode, run_days
    from app.prealert.levels import province_levels, province_quantities
    from app.prealert.provinces import provinces_on_grid

    by_lead = {lead: [m for raw in fields for m in decode(raw, TURKEY_BOX)] for lead, fields in by_lead_raw.items()}
    days = run_days(run, by_lead)
    first = by_lead[min(by_lead)][0]
    provinces = provinces_on_grid(first.lats, first.lons)
    stamp = f"{run:%Y-%m-%dT%H:%MZ}"
    rows: list[LevelRow] = []
    for day, values in sorted(days.items()):
        for province in provinces:
            quantities = province_quantities(values, province)
            levels = province_levels(quantities, province.code)
            for name, value in quantities.items():
                rows.append(LevelRow(stamp, day.isoformat(), province.code, name, value, levels[name]))
    return tuple(rows)


class EcmwfOpenDataCollector(BaseCollector[ShadowRun]):
    """The newest complete ECMWF run of 00 or 12 UTC, cut to Turkey, turned into province levels and stored."""

    def __init__(self, store: "ShadowStore", http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="ecmwf_opendata",
            name="ECMWF IFS 0.25 open data (mirror on AWS), unofficial pre-alert pilot for Turkey",
            domain="weather",
            ttl_seconds=CHECK_EVERY_S,
            timeout_seconds=httpx.Timeout(60.0, connect=15.0),
            http_client=http_client,
        )
        self.store = store
        self.pause_s = PAUSE_S
        self.last_lead_h = LAST_LEAD_H
        self.now = lambda: datetime.now(timezone.utc)

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> ShadowRun:
        previous: Optional[CacheEntry[ShadowRun]] = kwargs.get("previous")
        last = self.last_lead_h
        for run in candidate_runs(self.now()):
            if self.store.has_run(run):
                return ShadowRun(run, ())
            head = await self._head(client, file_url(run, last) + ".index")
            if head.status_code == 200:
                break
        else:
            if previous is not None:
                return previous.value
            raise RuntimeError("no complete ECMWF run in the last 30 hours")
        by_lead: dict[int, list[bytes]] = {}
        for lead in range(3, last + 1, 3):
            index = await self._get_capped(client, file_url(run, lead) + ".index", max_bytes=MAX_INDEX_BYTES)
            index.raise_for_status()
            fields: list[bytes] = []
            for _, offset, length in wanted_fields(index.text):
                if length > MAX_FIELD_BYTES:
                    raise ValueError(f"f{lead:03d}: a field of {length} bytes, more than the {MAX_FIELD_BYTES} allowed")
                part = await self._get(client, file_url(run, lead) + ".grib2", headers={"Range": f"bytes={offset}-{offset + length - 1}"})
                part.raise_for_status()
                if not part.content.startswith(b"GRIB"):
                    raise ValueError(f"f{lead:03d}: not a GRIB2 answer ({len(part.content)} bytes)")
                fields.append(part.content)
                await asyncio.sleep(self.pause_s)
            by_lead[lead] = fields
        rows = await asyncio.to_thread(compute_rows, run, by_lead)
        await asyncio.to_thread(self.store.save, rows)
        return ShadowRun(run, rows)


class ShadowStore:
    """The table ``prealert_tr_levels`` in the API's database (``VIGIE_DB_PATH``)."""

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        from app.services.storage_wal import default_db_path

        self.db_path = default_db_path() if db_path is None else Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS prealert_tr_levels (
                    run TEXT NOT NULL,
                    day TEXT NOT NULL,
                    province TEXT NOT NULL,
                    phenomenon TEXT NOT NULL,
                    value REAL,
                    level INTEGER,
                    stored_utc INTEGER NOT NULL,
                    PRIMARY KEY (run, day, province, phenomenon)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def has_run(self, run: datetime) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM prealert_tr_levels WHERE run = ? LIMIT 1", (f"{run:%Y-%m-%dT%H:%MZ}",)).fetchone()
        return row is not None

    def save(self, rows: tuple[LevelRow, ...]) -> None:
        stored = int(time.time())
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO prealert_tr_levels VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(r.run, r.day, r.province, r.phenomenon, r.value, r.level, stored) for r in rows],
            )

    def runs(self) -> list[str]:
        with self._connect() as conn:
            return [row[0] for row in conn.execute("SELECT DISTINCT run FROM prealert_tr_levels ORDER BY run")]

    def latest_for(self, province: str) -> tuple[Optional[str], list[LevelRow]]:
        """The newest stored run and its rows for one province (``(None, [])`` before the first run)."""
        with self._connect() as conn:
            row = conn.execute("SELECT MAX(run) FROM prealert_tr_levels").fetchone()
            run = row[0] if row else None
            if run is None:
                return None, []
            rows = conn.execute(
                "SELECT run, day, province, phenomenon, value, level FROM prealert_tr_levels WHERE run = ? AND province = ? ORDER BY day",
                (run, province),
            ).fetchall()
        return run, [LevelRow(*r) for r in rows]


class ShadowPilot:
    """Checks for a new run every ``CHECK_EVERY_S`` while the application runs."""

    def __init__(self, collector: EcmwfOpenDataCollector) -> None:
        self.collector = collector
        self.store = collector.store
        self._task: Optional[asyncio.Task[None]] = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                result = await self.collector.fetch(force_refresh=True)
                if result.rows:
                    logger.info("pre-alert pilot: run %s stored (%d rows)", f"{result.run:%Y-%m-%d %H} UTC", len(result.rows))
            except asyncio.CancelledError:
                raise
            except Exception as error:  # a failing provider never stops the API (rule 3); the next check tries again
                logger.warning("pre-alert pilot: %s", self.collector.redact(str(error)))
            await asyncio.sleep(CHECK_EVERY_S)
