"""The archive of the surf forecasts, to measure their error later (``surf_forecast_archive``).

Each time the API reads the swell or the wind of the spots from Open-Meteo (``SurfForecastCollector``), it keeps the values
that were forecast, with the instant they were issued and the instant they are valid for. It sends **no request of its own**:
what is stored is what was already fetched for the surf pages (``tests/test_forecast_archive.py`` checks that no request is added).

- One row per spot, per ``valid_utc`` and per issue bucket of ``BUCKET_S`` seconds: the first forecast read in a bucket is kept,
  the following ones of the same bucket are not (the model is refreshed every 12 hours, and the API reads it more often than that).
  ``issued_utc`` is the instant the API read it, not the model run: the lead time is ``valid_utc - issued_utc`` to within the
  age of the provider's data.
- Only the hours that are a multiple of ``STEP_S`` (3 hours, the native step of MFWAM) and under ``MAX_LEAD_S`` ahead are kept.
- The values are the provider's, untouched, in the units asked (m, s, degrees, km/h). A variable the provider left out stays NULL.
- ``VIGIE_FORECAST_ARCHIVE_KEEP_DAYS`` (default 90, 0 = for ever) sets how long an issued forecast is kept.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from typing import Any, Optional

from app import clock
from app.services.storage_wal import SQLiteWALStorage

log = logging.getLogger(__name__)

BUCKET_S = 6 * 3600
STEP_S = 3 * 3600
MAX_LEAD_S = 96 * 3600
PRUNE_EVERY_S = 86_400
KEEP_DAYS_ENV = "VIGIE_FORECAST_ARCHIVE_KEEP_DAYS"
DEFAULT_KEEP_DAYS = 90

# Column of the archive -> variable of the parsed Open-Meteo answer, by kind.
COLUMNS: dict[str, dict[str, str]] = {
    "swell": {
        "wave_height": "wave_height",
        "wave_period": "wave_period",
        "wave_direction": "wave_direction",
        "swell_height": "swell_wave_height",
        "swell_period": "swell_wave_period",
        "swell_direction": "swell_wave_direction",
    },
    "wind": {"wind_speed": "wind_speed_10m", "wind_direction": "wind_direction_10m", "wind_gusts": "wind_gusts_10m"},
}


def keep_days_from_env() -> int:
    raw = os.environ.get(KEEP_DAYS_ENV, "").strip()
    try:
        value = int(raw) if raw else DEFAULT_KEEP_DAYS
    except ValueError:
        return DEFAULT_KEEP_DAYS
    return max(0, value)


def rows_of(kind: str, answers: dict[str, dict[str, Any]], issued_utc: int) -> list[tuple[Any, ...]]:
    """The archive rows of one read: ``(spot, bucket, valid, issued, *values)`` for the hours kept."""
    names = COLUMNS[kind]
    bucket = issued_utc // BUCKET_S * BUCKET_S
    rows: list[tuple[Any, ...]] = []
    for spot_id, parsed in answers.items():
        for i, valid in enumerate(parsed["times"]):
            if valid % STEP_S or not 0 <= valid - issued_utc <= MAX_LEAD_S:
                continue
            values = tuple(parsed[variable][i] for variable in names.values())
            if all(v is None for v in values):
                continue
            rows.append((spot_id, bucket, valid, issued_utc, *values))
    return rows


class SurfForecastArchive:
    """Writes what ``SurfForecastCollector`` reads (``record``). Never raises, never sends a request."""

    def __init__(self, storage: Callable[[], SQLiteWALStorage], keep_days: Optional[int] = None) -> None:
        self._storage = storage
        self.keep_days = keep_days_from_env() if keep_days is None else max(0, int(keep_days))
        self._tasks: set[asyncio.Task[None]] = set()
        self._pruned_at: Optional[float] = None
        self.last_write_utc: Optional[int] = None
        self.last_error: Optional[str] = None

    def record(self, kind: str, answers: dict[str, dict[str, Any]]) -> None:
        """Called by the collector in the event loop: the rows are built here, written in a worker thread."""
        try:
            now = int(clock.wall())
            rows = rows_of(kind, answers, now)
            if rows:
                task = asyncio.get_running_loop().create_task(self._write(kind, rows, now))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("surf forecast archive: %s", self.last_error)

    async def _write(self, kind: str, rows: list[tuple[Any, ...]], now: int) -> None:
        try:
            storage = self._storage()
            await clock.run_blocking(storage.archive_surf_forecast, rows, tuple(COLUMNS[kind]))
            self.last_write_utc = now
            self.last_error = None
            if self.keep_days > 0 and (self._pruned_at is None or now - self._pruned_at >= PRUNE_EVERY_S):
                self._pruned_at = now
                await clock.run_blocking(storage.prune_surf_forecast, now - self.keep_days * 86_400)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("surf forecast archive: %s", self.last_error)

    async def settle(self) -> None:
        """Wait for the writes in flight (tests, shutdown)."""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def health(self) -> dict[str, Any]:
        rows, oldest, newest = self._storage().surf_forecast_summary()
        return {"rows": rows, "oldest_issued_utc": oldest, "newest_issued_utc": newest, "keep_days": self.keep_days,
                "last_write_utc": self.last_write_utc, "error": self.last_error}
