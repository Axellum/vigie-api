"""What the travel advice keeps between two starts, in the API's database (``VIGIE_DB_PATH``).

``travel_pages``: the pages of a ministry that are read one by one (the FCDO's 226 pages), with the date of the version kept.
Without it a restart would read the 226 pages again (15 MB); with it, only the pages the index says have changed.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Optional


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
