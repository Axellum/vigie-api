"""What the travel advice keeps between two starts, in the API's database (``VIGIE_DB_PATH``).

``travel_pages``: the pages of a ministry that are read one by one (the FCDO's 226 pages), with the date of the version kept.
Without it a restart would read the 226 pages again (15 MB); with it, only the pages the index says have changed.

``travel_changes``: **the archive of the advices**, kept by Vigie. A row is written the first time an advice is read, then each
time a new version of it is read (another level, other words, another date of publication); never otherwise. It starts the day
this instance first read the ministry: what came before is not known here (the FCDO publishes its own history: that one is in
the answer as given).
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional


class TravelStore:
    """The tables of the travel advice in the API's database."""

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        from app.services.storage_wal import default_db_path

        self.db_path = default_db_path() if db_path is None else Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS travel_pages (
                    ministry TEXT NOT NULL,
                    page TEXT NOT NULL,
                    updated_utc INTEGER NOT NULL,
                    stored_utc INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (ministry, page)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS travel_changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ministry TEXT NOT NULL,
                    country TEXT NOT NULL,
                    title TEXT NOT NULL,
                    observed_utc INTEGER NOT NULL,
                    signature TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_travel_changes_country ON travel_changes(country, observed_utc DESC)")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def load_pages(self, ministry: str) -> dict[str, str]:
        """The pages kept for ``ministry``: page name -> what the collector stored."""
        with self._connect() as conn:
            rows = conn.execute("SELECT page, payload_json FROM travel_pages WHERE ministry = ?", (ministry,)).fetchall()
        return {page: payload for page, payload in rows}

    def save_page(self, ministry: str, page: str, updated_utc: int, payload: str) -> None:
        """Keep the version of ``page`` dated ``updated_utc`` (it replaces the one kept)."""
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO travel_pages (ministry, page, updated_utc, stored_utc, payload_json) VALUES (?, ?, ?, ?, ?)",
                (ministry, page, int(updated_utc), int(time.time()), payload),
            )

    def last_signatures(self) -> dict[tuple[str, str, str], str]:
        """The signature of the latest version archived of each advice: (ministry, country, title) -> signature."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ministry, country, title, signature FROM travel_changes
                WHERE id IN (SELECT MAX(id) FROM travel_changes GROUP BY ministry, country, title)
                """
            ).fetchall()
        return {(ministry, country, title): signature for ministry, country, title, signature in rows}

    def add_versions(self, rows: list[tuple[str, str, str, int, str, dict[str, Any]]]) -> None:
        """Archive new versions: (ministry, country, title, observed_utc, signature, payload) each."""
        if not rows:
            return
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO travel_changes (ministry, country, title, observed_utc, signature, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
                [(m, c, t, int(o), sig, json.dumps(p, ensure_ascii=False)) for m, c, t, o, sig, p in rows],
            )

    def versions(self, country: str, limit: int = 200) -> list[dict[str, Any]]:
        """The versions archived for ``country``, newest first: each with ``ministry``, ``title``, ``observed_utc`` and its payload."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ministry, title, observed_utc, payload_json FROM travel_changes WHERE country = ? ORDER BY observed_utc DESC, id DESC LIMIT ?",
                (country, int(limit)),
            ).fetchall()
        return [{"ministry": m, "title": t, "observed_utc": o, **json.loads(p)} for m, t, o, p in rows]

    def first_version_utc(self) -> Optional[int]:
        """When the archive starts (the first version written), ``None`` when it is empty."""
        with self._connect() as conn:
            row = conn.execute("SELECT MIN(observed_utc) FROM travel_changes").fetchone()
        return int(row[0]) if row and row[0] is not None else None
