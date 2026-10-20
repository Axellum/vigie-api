"""Tests of the SQLite storage (watchlist, hazard history, snapshot cache)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.models.schemas import (
    DeviceType,
    GlobalStatus,
    HazardAlert,
    HazardCategory,
    MonitoredZoneUpsertRequest,
    SeverityLevel,
    SnapshotResponse,
    ZoneWeather,
)
from app.services import storage_wal
from app.services.storage_wal import SQLiteWALStorage


def _hazard(hazard_id: str, category: HazardCategory, severity: SeverityLevel, value: float | None) -> HazardAlert:
    return HazardAlert(
        id=hazard_id,
        category=category,
        severity=severity,
        title=hazard_id,
        source="USGS",
        sources=["USGS"],
        timestamp_utc=1792334000,
        lat=8.15,
        lon=126.30,
        value_primary=value,
        value_unit="Mw",
    )


def test_wal_zones_history_and_snapshot(tmp_path: Path) -> None:
    store = SQLiteWALStorage(db_path=tmp_path / "wal.db")
    assert store.get_journal_mode() == "wal"
    assert len(store.list_zones()) == 5  # initial watchlist

    new_zone = store.upsert_zone(
        MonitoredZoneUpsertRequest(
            id="z_brest",
            name="Brest, FR",
            lat=48.3904,
            lon=-4.4861,
            utc_offset_s=3600,
            alert_radius_km=400.0,
            is_marine=True,
        )
    )
    assert new_zone.id == "z_brest" and new_zone.is_marine is True
    assert len(store.list_zones()) == 6
    assert store.delete_zone("z_brest") is True
    assert store.delete_zone("z_brest") is False
    assert len(store.list_zones()) == 5

    quake = _hazard("hist_eq_1", HazardCategory.EARTHQUAKE, SeverityLevel.RED, 7.5)
    volcano = _hazard("hist_vo_2", HazardCategory.VOLCANO, SeverityLevel.ORANGE, 3.0)
    assert store.record_hazards([quake, volcano]) == 2

    assert store.query_history(limit=10)[0].id == "hist_eq_1"  # RED first
    assert [h.id for h in store.query_history(min_severity=SeverityLevel.RED)] == ["hist_eq_1"]
    assert [h.id for h in store.query_history(category=HazardCategory.VOLCANO)] == ["hist_vo_2"]


def test_a_hazard_without_value_is_stored_as_unknown(tmp_path: Path) -> None:
    store = SQLiteWALStorage(db_path=tmp_path / "wal.db")
    store.record_hazards([_hazard("noaa_1", HazardCategory.TORNADO, SeverityLevel.RED, None)])
    assert store.query_history()[0].value_primary is None
    with sqlite3.connect(tmp_path / "wal.db") as conn:
        assert conn.execute("SELECT value_primary FROM hazards_history").fetchone() == (None,)


def test_old_database_with_not_null_value_is_migrated(tmp_path: Path) -> None:
    db_file = tmp_path / "old.db"
    with sqlite3.connect(db_file) as conn:
        conn.executescript(
            """
            CREATE TABLE hazards_history (
                id TEXT PRIMARY KEY, category TEXT NOT NULL, severity TEXT NOT NULL,
                severity_rank INTEGER NOT NULL, timestamp_utc INTEGER NOT NULL,
                lat REAL NOT NULL, lon REAL NOT NULL, value_primary REAL NOT NULL,
                source TEXT NOT NULL, payload_json TEXT NOT NULL, updated_utc INTEGER NOT NULL
            );
            INSERT INTO hazards_history VALUES
                ('old_1', 'EARTHQUAKE', 'RED', 3, 1792334000, 1.0, 2.0, 7.0, 'USGS', '{}', 1792334000);
            """
        )
    store = SQLiteWALStorage(db_path=db_file)
    store.record_hazards([_hazard("noaa_2", HazardCategory.TORNADO, SeverityLevel.RED, None)])
    with sqlite3.connect(db_file) as conn:
        rows = dict(conn.execute("SELECT id, value_primary FROM hazards_history").fetchall())
    assert rows == {"old_1": 7.0, "noaa_2": None}  # the old row survived the migration


def test_snapshot_round_trip_keeps_unknown_values(tmp_path: Path) -> None:
    store = SQLiteWALStorage(db_path=tmp_path / "wal.db")
    snapshot = SnapshotResponse(
        server_utc=1792334000,
        device_type=DeviceType.TAB5,
        global_status=GlobalStatus(),
        zones=[ZoneWeather(id="z0", name="Paris, FR", lat=48.8566, lon=2.3522)],
        hazards=[],
    )
    store.save_snapshot(snapshot)
    loaded = store.load_last_snapshot(DeviceType.TAB5)
    assert loaded is not None
    assert loaded.zones[0].current is None
    assert loaded.zones[0].rain_next_60m is None
    assert loaded.global_status.max_severity is None
    assert store.load_last_snapshot(DeviceType.MARINE) is None


def test_default_database_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIGIE_DB_PATH", str(tmp_path / "custom.db"))
    assert storage_wal.default_db_path() == tmp_path / "custom.db"

    monkeypatch.delenv("VIGIE_DB_PATH")
    default = storage_wal.default_db_path()
    assert default.parent == Path(storage_wal.__file__).resolve().parents[2]  # inside the repository
    assert default.suffix == ".db"  # ignored by .gitignore (*.db)
