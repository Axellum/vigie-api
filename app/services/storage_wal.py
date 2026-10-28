"""SQLite persistence in WAL mode (``app/services/storage_wal.py``).

Provides:

1. Dynamic, persistent management of the **watched zones** (watchlist) behind ``/v1/zones``
   (``GET``, ``POST``, ``DELETE``).
2. De-duplicated hazard history (``hazards_history``) for trend analysis and replay through
   ``/v1/history``.
3. The last valid snapshot (``snapshot_cache``), so a cold start can serve something even
   when the Internet is down.
4. The **watched points** behind ``/v1/watched-points`` (``watched_points``), and for each hazard source the run of reads
   without a gap that "nothing happened" rests on (``source_runs``, see ``docs/decisions/0008-sensitive-points.md``).
5. The **archive of the official warnings** behind ``/v1/vigilances/archive`` (``vigilance_archive``, its payloads and shapes,
   and ``vigilance_reads``, the hours each source was read): see ``app/services/vigilance_archive.py``.

The database file is ``VIGIE_DB_PATH`` when set, otherwise ``vigie.db`` at the repository
root (git-ignored). A ``vigie_mondiale.db`` left by an earlier layout is reused when present.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from app.models.places import WatchedPoint, WatchedPointUpsertRequest
from app.models.schemas import (
    SEVERITY_RANK,
    DeviceType,
    HazardAlert,
    HazardCategory,
    MonitoredZoneConfig,
    MonitoredZoneUpsertRequest,
    SeverityLevel,
    SnapshotResponse,
)

# Initial watchlist. The UTC offsets are the fixed offsets of the cities (no daylight saving
# time): they are only used when the weather provider does not give the real local offset.
DEFAULT_WATCHLIST_SEED: list[dict[str, Any]] = [
    {"id": "z0", "name": "Paris, FR", "lat": 48.8566, "lon": 2.3522, "utc_offset_s": 3600, "alert_radius_km": 500.0, "is_marine": False},
    {"id": "z1", "name": "Tokyo, JP", "lat": 35.6762, "lon": 139.6503, "utc_offset_s": 32400, "alert_radius_km": 600.0, "is_marine": True},
    {"id": "z2", "name": "Pointe-à-Pitre, GP", "lat": 16.2410, "lon": -61.5330, "utc_offset_s": -14400, "alert_radius_km": 500.0, "is_marine": True},
    {"id": "z3", "name": "San Francisco, US", "lat": 37.7749, "lon": -122.4194, "utc_offset_s": -28800, "alert_radius_km": 500.0, "is_marine": True},
    {"id": "z4", "name": "Reykjavik, IS", "lat": 64.1466, "lon": -21.9426, "utc_offset_s": 0, "alert_radius_km": 450.0, "is_marine": True},
]


class WatchedPointsLimitError(Exception):
    """Raised when a new watched point would go over the limit of points an instance keeps."""


def default_db_path() -> Path:
    """Return the database path: ``VIGIE_DB_PATH``, else ``vigie.db`` in the repository root."""
    env_path = os.environ.get("VIGIE_DB_PATH")
    if env_path:
        return Path(env_path)
    root = Path(__file__).resolve().parents[2]
    current = root / "vigie.db"
    legacy = root / "vigie_mondiale.db"
    return legacy if (not current.exists() and legacy.exists()) else current


class SQLiteWALStorage:
    """High-concurrency SQLite storage in Write-Ahead Logging (WAL) mode."""

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        self.db_path = default_db_path() if db_path is None else Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema_and_seed()

    def _connect(self) -> sqlite3.Connection:
        """Open a SQLite connection configured with WAL, synchronous=NORMAL and a busy timeout."""
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    @staticmethod
    def _make_hazard_value_nullable(conn: sqlite3.Connection) -> None:
        """Rebuild ``hazards_history`` when an older layout declared ``value_primary NOT NULL``.

        A hazard whose source gives no value (NOAA NWS alerts) is stored with a NULL value,
        never with a made-up number.
        """
        columns = conn.execute("PRAGMA table_info(hazards_history)").fetchall()
        needs_rebuild = any(c["name"] == "value_primary" and int(c["notnull"]) == 1 for c in columns)
        if not needs_rebuild:
            return
        conn.executescript(
            """
            ALTER TABLE hazards_history RENAME TO hazards_history_old;
            CREATE TABLE hazards_history (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                severity TEXT NOT NULL,
                severity_rank INTEGER NOT NULL,
                timestamp_utc INTEGER NOT NULL,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                value_primary REAL,
                source TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_utc INTEGER NOT NULL
            );
            INSERT INTO hazards_history SELECT * FROM hazards_history_old;
            DROP TABLE hazards_history_old;
            """
        )

    def _init_schema_and_seed(self) -> None:
        """Create the tables when missing and seed the initial watchlist."""
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS monitored_zones (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    lat REAL NOT NULL,
                    lon REAL NOT NULL,
                    utc_offset_s INTEGER NOT NULL DEFAULT 0,
                    alert_radius_km REAL NOT NULL DEFAULT 500.0,
                    is_marine INTEGER NOT NULL DEFAULT 0,
                    created_utc INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS hazards_history (
                    id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    severity_rank INTEGER NOT NULL,
                    timestamp_utc INTEGER NOT NULL,
                    lat REAL NOT NULL,
                    lon REAL NOT NULL,
                    value_primary REAL,
                    source TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_utc INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS snapshot_cache (
                    device_type TEXT PRIMARY KEY,
                    server_utc INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS watched_points (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    lat REAL NOT NULL,
                    lon REAL NOT NULL,
                    radius_km REAL NOT NULL,
                    created_utc INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS source_runs (
                    source TEXT PRIMARY KEY,
                    since_utc INTEGER NOT NULL,
                    last_ok_utc INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS vigilance_archive (
                    id TEXT NOT NULL,
                    identifier TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_id TEXT,
                    country TEXT,
                    area_desc TEXT,
                    area_codes TEXT,
                    phenomenon TEXT,
                    phenomenon_original TEXT,
                    level TEXT,
                    level_rank INTEGER,
                    level_original TEXT,
                    status TEXT,
                    message_type TEXT,
                    sent_utc INTEGER,
                    effective_utc INTEGER,
                    onset_utc INTEGER,
                    expires_utc INTEGER,
                    ends_utc INTEGER,
                    first_seen_utc INTEGER NOT NULL,
                    last_seen_utc INTEGER NOT NULL,
                    has_texts INTEGER NOT NULL,
                    shape_hash TEXT,
                    min_lon REAL,
                    min_lat REAL,
                    max_lon REAL,
                    max_lat REAL,
                    UNIQUE (id, identifier)
                );

                CREATE TABLE IF NOT EXISTS vigilance_archive_payload (
                    archive_rowid INTEGER PRIMARY KEY,
                    payload BLOB NOT NULL
                );

                CREATE TABLE IF NOT EXISTS vigilance_shapes (
                    hash TEXT PRIMARY KEY,
                    wkb BLOB NOT NULL
                );

                CREATE TABLE IF NOT EXISTS vigilance_reads (
                    source_key TEXT NOT NULL,
                    country TEXT NOT NULL,
                    day TEXT NOT NULL,
                    first_utc INTEGER NOT NULL,
                    last_utc INTEGER NOT NULL,
                    hours INTEGER NOT NULL,
                    PRIMARY KEY (source_key, country, day)
                );
                """
            )
            self._make_hazard_value_nullable(conn)
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_hazards_ts
                    ON hazards_history(timestamp_utc DESC);
                CREATE INDEX IF NOT EXISTS idx_hazards_cat_sev
                    ON hazards_history(category, severity_rank DESC, timestamp_utc DESC);
                CREATE INDEX IF NOT EXISTS idx_vigilance_archive_seen
                    ON vigilance_archive(first_seen_utc DESC);
                CREATE INDEX IF NOT EXISTS idx_vigilance_archive_country
                    ON vigilance_archive(country, first_seen_utc DESC);
                CREATE INDEX IF NOT EXISTS idx_vigilance_archive_last_seen
                    ON vigilance_archive(last_seen_utc);
                """
            )

            row = conn.execute("SELECT COUNT(*) AS cnt FROM monitored_zones").fetchone()
            if row and int(row["cnt"]) == 0:
                now_utc = int(time.time())
                for seed in DEFAULT_WATCHLIST_SEED:
                    conn.execute(
                        """
                        INSERT INTO monitored_zones (id, name, lat, lon, utc_offset_s, alert_radius_km, is_marine, created_utc)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            seed["id"],
                            seed["name"],
                            seed["lat"],
                            seed["lon"],
                            seed["utc_offset_s"],
                            seed["alert_radius_km"],
                            1 if seed["is_marine"] else 0,
                            now_utc,
                        ),
                    )
            conn.commit()

    def get_journal_mode(self) -> str:
        """Return the active SQLite journal mode (lets the tests check for ``wal``)."""
        with self._connect() as conn:
            row = conn.execute("PRAGMA journal_mode;").fetchone()
            return str(row[0]).lower() if row else "unknown"

    # ========================================================================
    # 1. Watched zones (watchlist)
    # ========================================================================

    def list_zones(self) -> list[MonitoredZoneConfig]:
        """Return all watched zones sorted by identifier."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, lat, lon, utc_offset_s, alert_radius_km, is_marine FROM monitored_zones ORDER BY id ASC"
            ).fetchall()
        return [
            MonitoredZoneConfig(
                id=str(r["id"]),
                name=str(r["name"]),
                lat=float(r["lat"]),
                lon=float(r["lon"]),
                utc_offset_s=int(r["utc_offset_s"]),
                alert_radius_km=float(r["alert_radius_km"]),
                is_marine=bool(int(r["is_marine"]) == 1),
            )
            for r in rows
        ]

    def upsert_zone(self, req: MonitoredZoneUpsertRequest) -> MonitoredZoneConfig:
        """Add or update a watched zone."""
        now_utc = int(time.time())
        with self._connect() as conn:
            zone_id = (req.id or "").strip()
            if not zone_id:
                existing_ids = {
                    str(r["id"])
                    for r in conn.execute("SELECT id FROM monitored_zones").fetchall()
                }
                idx = len(existing_ids)
                while f"z{idx}" in existing_ids:
                    idx += 1
                zone_id = f"z{idx}"

            conn.execute(
                """
                INSERT INTO monitored_zones (id, name, lat, lon, utc_offset_s, alert_radius_km, is_marine, created_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    lat = excluded.lat,
                    lon = excluded.lon,
                    utc_offset_s = excluded.utc_offset_s,
                    alert_radius_km = excluded.alert_radius_km,
                    is_marine = excluded.is_marine
                """,
                (
                    zone_id,
                    req.name.strip(),
                    round(float(req.lat), 4),
                    round(float(req.lon), 4),
                    int(req.utc_offset_s),
                    round(float(req.alert_radius_km), 1),
                    1 if req.is_marine else 0,
                    now_utc,
                ),
            )
            conn.commit()

        return MonitoredZoneConfig(
            id=zone_id,
            name=req.name.strip(),
            lat=round(float(req.lat), 4),
            lon=round(float(req.lon), 4),
            utc_offset_s=int(req.utc_offset_s),
            alert_radius_km=round(float(req.alert_radius_km), 1),
            is_marine=req.is_marine,
        )

    def delete_zone(self, zone_id: str) -> bool:
        """Delete a watched zone by identifier. Return True when a zone was deleted."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM monitored_zones WHERE id = ?", (zone_id,))
            conn.commit()
            return cur.rowcount > 0

    # ========================================================================
    # 2. Hazard history (``hazards_history``)
    # ========================================================================

    def record_hazards(self, hazards: Iterable[HazardAlert]) -> int:
        """Insert or update a list of de-duplicated alerts in the history."""
        now_utc = int(time.time())
        rows_to_upsert: list[tuple[Any, ...]] = []
        for h in hazards:
            rows_to_upsert.append(
                (
                    h.id,
                    h.category.value,
                    h.severity.value,
                    SEVERITY_RANK[h.severity],
                    h.timestamp_utc,
                    h.lat,
                    h.lon,
                    h.value_primary,
                    h.source,
                    h.model_dump_json(),
                    now_utc,
                )
            )

        if not rows_to_upsert:
            return 0

        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO hazards_history (
                    id, category, severity, severity_rank, timestamp_utc,
                    lat, lon, value_primary, source, payload_json, updated_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    severity = excluded.severity,
                    severity_rank = excluded.severity_rank,
                    value_primary = excluded.value_primary,
                    source = excluded.source,
                    payload_json = excluded.payload_json,
                    updated_utc = excluded.updated_utc
                """,
                rows_to_upsert,
            )
            conn.commit()
        return len(rows_to_upsert)

    def query_history(
        self,
        category: Optional[HazardCategory] = None,
        min_severity: Optional[SeverityLevel] = None,
        limit: int = 50,
    ) -> list[HazardAlert]:
        """Query the history, filtered by category and minimum severity."""
        clauses: list[str] = []
        params: list[Any] = []

        if category is not None:
            clauses.append("category = ?")
            params.append(category.value)

        if min_severity is not None:
            clauses.append("severity_rank >= ?")
            params.append(SEVERITY_RANK[min_severity])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        safe_limit = max(1, min(500, int(limit)))
        params.append(safe_limit)

        sql = f"""
            SELECT payload_json
            FROM hazards_history
            {where_sql}
            ORDER BY severity_rank DESC, timestamp_utc DESC
            LIMIT ?
        """
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()

        alerts: list[HazardAlert] = []
        for r in rows:
            try:
                alerts.append(HazardAlert.model_validate_json(str(r["payload_json"])))
            except Exception:
                continue
        return alerts

    # ========================================================================
    # 3. Last snapshot (``snapshot_cache``)
    # ========================================================================

    def save_snapshot(self, snapshot: SnapshotResponse) -> None:
        """Persist the last valid snapshot of a display profile."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO snapshot_cache (device_type, server_utc, payload_json)
                VALUES (?, ?, ?)
                ON CONFLICT(device_type) DO UPDATE SET
                    server_utc = excluded.server_utc,
                    payload_json = excluded.payload_json
                """,
                (
                    snapshot.device_type.value,
                    snapshot.server_utc,
                    json.dumps(snapshot.model_dump(mode="json"), separators=(",", ":")),
                ),
            )
            conn.commit()

    def load_last_snapshot(
        self, device_type: DeviceType = DeviceType.WAVESHARE_7B
    ) -> Optional[SnapshotResponse]:
        """Load the last saved snapshot, for an instant cold start."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM snapshot_cache WHERE device_type = ?",
                (device_type.value,),
            ).fetchone()
        if not row:
            return None
        try:
            return SnapshotResponse.model_validate_json(str(row["payload_json"]))
        except Exception:
            return None

    # ========================================================================
    # 4. Watched points and the runs of reads
    # ========================================================================

    def list_watched_points(self) -> list[WatchedPoint]:
        """Return the watched points sorted by creation, then identifier."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, kind, lat, lon, radius_km FROM watched_points ORDER BY created_utc ASC, id ASC"
            ).fetchall()
        return [
            WatchedPoint(
                id=str(r["id"]),
                name=str(r["name"]),
                kind=str(r["kind"]),  # type: ignore[arg-type]
                lat=float(r["lat"]),
                lon=float(r["lon"]),
                radius_km=float(r["radius_km"]),
            )
            for r in rows
        ]

    def upsert_watched_point(self, req: WatchedPointUpsertRequest, max_points: int) -> WatchedPoint:
        """Add or update a watched point. A new point over ``max_points`` raises ``WatchedPointsLimitError``."""
        now_utc = int(time.time())
        with self._connect() as conn:
            existing_ids = {str(r["id"]) for r in conn.execute("SELECT id FROM watched_points").fetchall()}
            point_id = (req.id or "").strip()
            if not point_id:
                idx = len(existing_ids) + 1
                while f"p{idx}" in existing_ids:
                    idx += 1
                point_id = f"p{idx}"
            if point_id not in existing_ids and len(existing_ids) >= max_points:
                raise WatchedPointsLimitError(f"An instance keeps {max_points} watched points at most")
            point = WatchedPoint(
                id=point_id,
                name=req.name.strip(),
                kind=req.kind,
                lat=round(float(req.lat), 4),
                lon=round(float(req.lon), 4),
                radius_km=round(float(req.radius_km), 1),
            )
            conn.execute(
                """
                INSERT INTO watched_points (id, name, kind, lat, lon, radius_km, created_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    kind = excluded.kind,
                    lat = excluded.lat,
                    lon = excluded.lon,
                    radius_km = excluded.radius_km
                """,
                (point.id, point.name, point.kind, point.lat, point.lon, point.radius_km, now_utc),
            )
            conn.commit()
        return point

    def delete_watched_point(self, point_id: str) -> bool:
        """Delete a watched point by identifier. Return True when a point was deleted."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM watched_points WHERE id = ?", (point_id,))
            conn.commit()
            return cur.rowcount > 0

    def load_source_runs(self) -> dict[str, tuple[int, int]]:
        """For each hazard source the start of its current run of reads without a gap, and its last good read (UTC seconds)."""
        with self._connect() as conn:
            rows = conn.execute("SELECT source, since_utc, last_ok_utc FROM source_runs").fetchall()
        return {str(r["source"]): (int(r["since_utc"]), int(r["last_ok_utc"])) for r in rows}

    def save_source_runs(self, runs: dict[str, tuple[int, int]]) -> None:
        """Persist the runs of reads of the hazard sources."""
        if not runs:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO source_runs (source, since_utc, last_ok_utc) VALUES (?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET since_utc = excluded.since_utc, last_ok_utc = excluded.last_ok_utc
                """,
                [(source, since, last) for source, (since, last) in runs.items()],
            )
            conn.commit()

    def hazard_positions_since(self, window_start_utc: int, limit: int = 50000) -> list[tuple[str, float, float]]:
        """``(id, lat, lon)`` of the hazards of the history that count in a window starting at ``window_start_utc``.

        An earthquake counts by the time it happened; any other hazard (a storm, a flood, a volcano in alert) by the last time the
        API saw it active, because it lasts. Simulated alerts are never counted.
        """
        with self._connect() as conn:
            rows = conn.execute(
                r"""
                SELECT id, lat, lon FROM hazards_history
                WHERE ((category = 'EARTHQUAKE' AND timestamp_utc >= ?) OR (category != 'EARTHQUAKE' AND updated_utc >= ?))
                  AND id NOT LIKE 'sim\_%' ESCAPE '\'
                ORDER BY timestamp_utc DESC
                LIMIT ?
                """,
                (window_start_utc, window_start_utc, int(limit)),
            ).fetchall()
        return [(str(r["id"]), float(r["lat"]), float(r["lon"])) for r in rows]

    def hazards_by_ids(self, ids: Iterable[str]) -> list[HazardAlert]:
        """The history entries with these identifiers, most recent event first."""
        wanted = list(dict.fromkeys(ids))
        alerts: list[HazardAlert] = []
        with self._connect() as conn:
            for start in range(0, len(wanted), 500):  # SQLite accepts a limited number of parameters at once
                chunk = wanted[start : start + 500]
                marks = ",".join("?" for _ in chunk)
                rows = conn.execute(f"SELECT payload_json FROM hazards_history WHERE id IN ({marks})", tuple(chunk)).fetchall()
                for r in rows:
                    try:
                        alerts.append(HazardAlert.model_validate_json(str(r["payload_json"])))
                    except Exception:
                        continue
        alerts.sort(key=lambda h: h.timestamp_utc, reverse=True)
        return alerts

    # ========================================================================
    # 5. Archive of the official warnings (``vigilance_archive``)
    # ========================================================================

    def archive_vigilances(
        self,
        written: list[tuple[dict[str, Any], bytes]],
        shapes: dict[str, bytes],
        touched: list[tuple[str, str]],
        reads: list[tuple[str, str, str, int, int]],
        now_utc: int,
    ) -> None:
        """Write one pass of the archive in one transaction.

        ``written``: the warnings new or changed since the last pass, each a row (``ARCHIVE_COLUMNS``) and its compressed payload;
        a warning already archived keeps its first sighting. ``shapes``: the compressed shapes by hash (one per shape, whatever the
        number of warnings that share it). ``touched``: the ``(id, identifier)`` of the warnings seen again, unchanged (their last
        sighting becomes ``now_utc``). ``reads``: ``(source_key, country, day, hour bit, time)`` of the sources that answered.
        """
        with self._connect() as conn:
            if shapes:
                conn.executemany("INSERT OR IGNORE INTO vigilance_shapes (hash, wkb) VALUES (?, ?)", list(shapes.items()))
            for row, payload in written:
                conn.execute(_ARCHIVE_UPSERT, tuple(row[column] for column in ARCHIVE_COLUMNS))
                found = conn.execute(
                    "SELECT rowid FROM vigilance_archive WHERE id = ? AND identifier = ?", (row["id"], row["identifier"])
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO vigilance_archive_payload (archive_rowid, payload) VALUES (?, ?)
                    ON CONFLICT(archive_rowid) DO UPDATE SET payload = excluded.payload
                    """,
                    (int(found[0]), payload),
                )
            if touched:
                conn.executemany(
                    "UPDATE vigilance_archive SET last_seen_utc = ? WHERE id = ? AND identifier = ? AND last_seen_utc < ?",
                    [(now_utc, warning_id, identifier, now_utc) for warning_id, identifier in touched],
                )
            if reads:
                conn.executemany(
                    """
                    INSERT INTO vigilance_reads (source_key, country, day, first_utc, last_utc, hours) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_key, country, day) DO UPDATE SET
                        first_utc = MIN(first_utc, excluded.first_utc),
                        last_utc = MAX(last_utc, excluded.last_utc),
                        hours = hours | excluded.hours
                    """,
                    [(source_key, country, day, at, at, hour_bit) for source_key, country, day, hour_bit, at in reads],
                )
            conn.commit()

    def query_vigilance_archive(
        self,
        *,
        country: Optional[str] = None,
        area: Optional[str] = None,
        source: Optional[str] = None,
        phenomenon: Optional[str] = None,
        level: Optional[str] = None,
        min_level_rank: Optional[int] = None,
        since_utc: Optional[int] = None,
        until_utc: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[int, list[tuple[int, int, bytes]]]:
        """The archived warnings that match, the most recently first seen first: the total, and ``(first_seen, last_seen, payload)``.

        ``since_utc`` and ``until_utc`` keep the warnings whose period overlaps them: it starts at the onset (else the effective
        time, the time of issue, the first sighting) and ends at the later of the end and the expiry (else the last sighting).
        ``area`` is a code of the area (``NUTS3:FR101`` or ``FR101``) or a part of its name (case ignored). ``source`` is
        ``meteoalarm``, ``dwd``... or ``swic:<service>``.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if country is not None:
            clauses.append("country = ?")
            params.append(country)
        if source is not None:
            if source.startswith("swic:"):
                clauses.append("source = 'swic' AND source_id = ?")
                params.append(source.split(":", 1)[1])
            else:
                clauses.append("source = ?")
                params.append(source)
        if phenomenon is not None:
            clauses.append("phenomenon = ?")
            params.append(phenomenon)
        if level is not None:
            clauses.append("level = ?")
            params.append(level)
        if min_level_rank is not None:
            clauses.append("level_rank >= ?")
            params.append(min_level_rank)
        if since_utc is not None:
            clauses.append(f"{_ARCHIVE_END} >= ?")
            params.append(since_utc)
        if until_utc is not None:
            clauses.append(f"{_ARCHIVE_START} <= ?")
            params.append(until_utc)
        if area and area.strip():
            code = _like_escaped(area.strip())
            clauses.append(r"(area_codes LIKE ? ESCAPE '\' OR area_codes LIKE ? ESCAPE '\' OR casefold(area_desc) LIKE ? ESCAPE '\')")
            params.extend([f"%,{code},%", f"%:{code},%", f"%{_like_escaped(area.strip().casefold())}%"])
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            conn.create_function("casefold", 1, _casefold, deterministic=True)
            total = int(conn.execute(f"SELECT COUNT(*) FROM vigilance_archive {where_sql}", tuple(params)).fetchone()[0])
            rows = conn.execute(
                f"""
                SELECT a.first_seen_utc, a.last_seen_utc, p.payload
                FROM vigilance_archive a JOIN vigilance_archive_payload p ON p.archive_rowid = a.rowid
                {where_sql}
                ORDER BY a.first_seen_utc DESC, a.rowid DESC
                LIMIT ? OFFSET ?
                """,
                (*params, max(1, int(limit)), max(0, int(offset))),
            ).fetchall()
        return total, [(int(r[0]), int(r[1]), bytes(r[2])) for r in rows]

    def vigilance_reads(
        self, country: Optional[str], first_day: str, last_day: str, limit: int = 2000
    ) -> list[tuple[str, str, str, int, int, int]]:
        """``(source_key, country, day, first_utc, last_utc, hours)`` of the sources read between two UTC days (``YYYY-MM-DD``)."""
        clauses = ["day >= ?", "day <= ?"]
        params: list[Any] = [first_day, last_day]
        if country is not None:
            clauses.append("country = ?")
            params.append(country)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT source_key, country, day, first_utc, last_utc, hours FROM vigilance_reads
                WHERE {' AND '.join(clauses)} ORDER BY day DESC, country, source_key LIMIT ?
                """,
                (*params, int(limit)),
            ).fetchall()
        return [(str(r[0]), str(r[1]), str(r[2]), int(r[3]), int(r[4]), int(r[5])) for r in rows]

    def vigilance_archive_summary(self) -> tuple[int, Optional[int], Optional[int]]:
        """How many warnings the archive holds, its first sighting and its last."""
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*), MIN(first_seen_utc), MAX(last_seen_utc) FROM vigilance_archive").fetchone()
        return int(row[0]), (None if row[1] is None else int(row[1])), (None if row[2] is None else int(row[2]))

    def vigilance_archive_days(self, since_utc: int) -> tuple[list[tuple[str, int, int, int]], dict[str, tuple[int, int]], int, int]:
        """What the archive took in per UTC day since ``since_utc``, and what it weighs.

        Returns the days ``(day, warnings first seen, bytes of their payloads, countries)``, the reads per day
        ``{day: (sources, countries)}``, the bytes of every payload and the bytes of every shape.
        """
        with self._connect() as conn:
            days = conn.execute(
                """
                SELECT date(a.first_seen_utc, 'unixepoch') AS day, COUNT(*), SUM(LENGTH(p.payload)), COUNT(DISTINCT a.country)
                FROM vigilance_archive a JOIN vigilance_archive_payload p ON p.archive_rowid = a.rowid
                WHERE a.first_seen_utc >= ? GROUP BY day ORDER BY day DESC
                """,
                (since_utc,),
            ).fetchall()
            reads = conn.execute(
                """
                SELECT day, COUNT(DISTINCT source_key), COUNT(DISTINCT country) FROM vigilance_reads
                WHERE day >= date(?, 'unixepoch') GROUP BY day
                """,
                (since_utc,),
            ).fetchall()
            payload_bytes = conn.execute("SELECT COALESCE(SUM(LENGTH(payload)), 0) FROM vigilance_archive_payload").fetchone()[0]
            shape_bytes = conn.execute("SELECT COALESCE(SUM(LENGTH(wkb)), 0) FROM vigilance_shapes").fetchone()[0]
        return (
            [(str(r[0]), int(r[1]), int(r[2] or 0), int(r[3])) for r in days],
            {str(r[0]): (int(r[1]), int(r[2])) for r in reads},
            int(payload_bytes),
            int(shape_bytes),
        )

    def database_bytes(self) -> int:
        """The size of the database on disk: the file and its write-ahead log."""
        total = 0
        for path in (self.db_path, Path(f"{self.db_path}-wal")):
            try:
                total += path.stat().st_size
            except OSError:
                continue
        return total

    def prune_vigilance_archive(self, before_utc: int) -> int:
        """Forget the warnings last seen before ``before_utc``, the shapes no warning uses any more and the older reads."""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM vigilance_archive_payload WHERE archive_rowid IN (SELECT rowid FROM vigilance_archive WHERE last_seen_utc < ?)",
                (before_utc,),
            )
            removed = conn.execute("DELETE FROM vigilance_archive WHERE last_seen_utc < ?", (before_utc,)).rowcount
            conn.execute("DELETE FROM vigilance_shapes WHERE hash NOT IN (SELECT shape_hash FROM vigilance_archive WHERE shape_hash IS NOT NULL)")
            conn.execute("DELETE FROM vigilance_reads WHERE day < date(?, 'unixepoch')", (before_utc,))
            conn.commit()
        return int(removed)


# The columns of a warning in the archive, in the order of the rows ``archive_vigilances`` writes.
ARCHIVE_COLUMNS: tuple[str, ...] = (
    "id",
    "identifier",
    "source",
    "source_id",
    "country",
    "area_desc",
    "area_codes",
    "phenomenon",
    "phenomenon_original",
    "level",
    "level_rank",
    "level_original",
    "status",
    "message_type",
    "sent_utc",
    "effective_utc",
    "onset_utc",
    "expires_utc",
    "ends_utc",
    "first_seen_utc",
    "last_seen_utc",
    "has_texts",
    "shape_hash",
    "min_lon",
    "min_lat",
    "max_lon",
    "max_lat",
)
_ARCHIVE_SIGHTINGS = ("id", "identifier", "first_seen_utc", "last_seen_utc")
_ARCHIVE_UPSERT = f"""
    INSERT INTO vigilance_archive ({", ".join(ARCHIVE_COLUMNS)}) VALUES ({", ".join("?" for _ in ARCHIVE_COLUMNS)})
    ON CONFLICT(id, identifier) DO UPDATE SET
        {", ".join(f"{column} = excluded.{column}" for column in ARCHIVE_COLUMNS if column not in _ARCHIVE_SIGHTINGS)},
        first_seen_utc = MIN(first_seen_utc, excluded.first_seen_utc),
        last_seen_utc = MAX(last_seen_utc, excluded.last_seen_utc)
"""
# When a warning applies: from its onset (else its effective time, its time of issue, its first sighting) to the later of its end and
# its expiry (else its last sighting). SQLite's max() of several values is null when one of them is, hence the fall-backs.
_ARCHIVE_START = "COALESCE(onset_utc, effective_utc, sent_utc, first_seen_utc)"
_ARCHIVE_END = "COALESCE(MAX(ends_utc, expires_utc), ends_utc, expires_utc, last_seen_utc)"


def _like_escaped(text: str) -> str:
    """``text`` with the wildcards of ``LIKE`` escaped (``ESCAPE '\\'``)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _casefold(text: Any) -> Any:
    """SQLite's ``lower()`` folds ASCII only: an area named "Île-de-France" is found with "île"."""
    return text.casefold() if isinstance(text, str) else text
