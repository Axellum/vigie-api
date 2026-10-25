"""The shadow pilot: read each new GFS run from NOMADS, compute the province levels, store them. Shown to no one (ADR 0011).

On when ``VIGIE_PREALERT_TR`` is ``1`` / ``true`` / ``yes`` / ``on`` (off by default: nothing is downloaded, nothing is imported
from ``numpy`` or ``eccodes``).

**Which run.** GFS runs at 00, 06, 12 and 18 UTC; a run is complete on NOMADS some hours later (not documented; a run is read only
once the inventory of its last lead, ``f072``, answers). Every ``CHECK_EVERY_S`` the newest complete run of the last 30 hours is
looked for, newest first, with a ``HEAD`` on that inventory: about 2 requests a check when nothing is new. A run already stored is
not read again.

**A run is 24 requests** to the NOMADS grib filter, one per lead (3 to 72 hours), each cut server-side to Turkey and to the six
fields of ``gfs.NOMADS_VARIABLES`` (about 20 KB each, measured on 2026-10-25), sent one after the other with ``PAUSE_S`` between
them. NOMADS publishes no limit; the NWS says it blocks abusive users (https://www.weather.gov/abusive-user-block), so the budget is
ours (``noaa-gfs-nomads`` in ``app/collectors/budget.py``): 4 runs and the checks fit in it with room.

**What is stored**: one row per run, local day, province and phenomenon (value and level) in the table ``prealert_tr_levels`` of the
API's database, for the scoring of ADR 0011. Nothing reads it but the scoring tool.
"""

from __future__ import annotations

import asyncio
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

NOMADS_FILTER = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
NOMADS_FILES = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod"
LAST_LEAD_H = 72
CHECK_EVERY_S = 30 * 60
PAUSE_S = 2.0
LOOK_BACK_H = 30
MAX_FILE_BYTES = 200_000  # one lead cut to Turkey weighs about 20 KB


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
    """The GFS runs of the last ``LOOK_BACK_H`` hours, newest first."""
    latest = now.replace(minute=0, second=0, microsecond=0, hour=now.hour - now.hour % 6)
    out = []
    run = latest
    while now - run <= timedelta(hours=LOOK_BACK_H):
        out.append(run)
        run -= timedelta(hours=6)
    return out


def filter_params(run: datetime, lead: int) -> dict[str, str]:
    """The query of the NOMADS grib filter for one lead, cut to Turkey (``gfs.NOMADS_*``)."""
    from app.prealert.gfs import NOMADS_LEVELS, NOMADS_VARIABLES

    params = {"dir": f"/gfs.{run:%Y%m%d}/{run:%H}/atmos", "file": f"gfs.t{run:%H}z.pgrb2.0p25.f{lead:03d}"}
    params.update({f"var_{name}": "on" for name in NOMADS_VARIABLES})
    params.update({f"lev_{name}": "on" for name in NOMADS_LEVELS})
    params.update({"subregion": "", "toplat": "42.5", "leftlon": "25.5", "rightlon": "45", "bottomlat": "35.5"})
    return params


def compute_rows(run: datetime, files: dict[int, bytes]) -> tuple[LevelRow, ...]:
    """Decode the files of one run and compute every province level (CPU work: run it in a thread)."""
    from app.prealert.gfs import decode, run_days
    from app.prealert.levels import province_levels, province_quantities
    from app.prealert.provinces import provinces_on_grid

    by_lead = {lead: decode(data) for lead, data in files.items()}
    days = run_days(run, by_lead)
    first = by_lead[min(by_lead)][0]
    provinces = provinces_on_grid(first.lats, first.lons)
    stamp = f"{run:%Y-%m-%dT%H:%MZ}"
    rows: list[LevelRow] = []
    for day, values in sorted(days.items()):
        for province in provinces:
            quantities = province_quantities(values, province)
            levels = province_levels(quantities)
            for name, value in quantities.items():
                rows.append(LevelRow(stamp, day.isoformat(), province.code, name, value, levels[name]))
    return tuple(rows)


class GfsNomadsCollector(BaseCollector[ShadowRun]):
    """The newest complete GFS run, cut to Turkey by the NOMADS grib filter, turned into province levels."""

    def __init__(self, store: "ShadowStore", http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="noaa_gfs_nomads",
            name="NOAA GFS 0.25 (NOMADS grib filter), unofficial pre-alert pilot for Turkey",
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
            head = await self._head(client, f"{NOMADS_FILES}/gfs.{run:%Y%m%d}/{run:%H}/atmos/gfs.t{run:%H}z.pgrb2.0p25.f{last:03d}.idx")
            if head.status_code == 200:
                break
        else:
            if previous is not None:
                return previous.value
            raise RuntimeError("no complete GFS run on NOMADS in the last 30 hours")
        files: dict[int, bytes] = {}
        for lead in range(3, last + 1, 3):
            response = await self._get(client, NOMADS_FILTER, params=filter_params(run, lead))
            response.raise_for_status()
            if len(response.content) > MAX_FILE_BYTES or not response.content.startswith(b"GRIB"):
                raise ValueError(f"f{lead:03d}: not a GRIB2 answer of the expected size ({len(response.content)} bytes)")
            files[lead] = response.content
            await asyncio.sleep(self.pause_s)
        rows = await asyncio.to_thread(compute_rows, run, files)
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


class ShadowPilot:
    """Checks for a new run every ``CHECK_EVERY_S`` while the application runs."""

    def __init__(self, collector: GfsNomadsCollector) -> None:
        self.collector = collector
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
