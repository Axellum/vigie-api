"""Tests unitaires et d'intégration de la Vague 2 (`tests/test_storage_flood_and_frame.py`).

Vérifie :
1. Les collecteurs complémentaires :
   - `OpenMeteoFloodCollector` (GloFAS Copernicus : `river_discharge`, anomalie et niveau de crue).
   - `OpenMeteoAirQualityCollector` (`european_aqi`, `us_aqi`, `pm2_5`, `pm10`, `uv_index`, `dust` / cendres).
   - `USGSVolcanoCollector` (codes d'alerte aviation OACI `RED`/`ORANGE`/`YELLOW` et VEI).
2. La persistance SQLite en mode WAL (`SQLiteWALStorage`) :
   - Vérification de `PRAGMA journal_mode = wal`.
   - CRUD complet de la Watchlist des zones mondiales (`/v1/zones` : `GET`, `POST`, `DELETE`).
   - Historisation dédupliquée des catastrophes et filtrage (`/v1/history?category=...&min_severity=...`).
   - Sauvegarde et rechargement à froid du dernier snapshot (`snapshot_cache`).
3. Le générateur de Trame Compacte Embarquée (`GET /v1/embedded/frame`) :
   - Structure des lignes `HEAD|...`, `GPS|...`, `ZONE|...`, `HAZ|...`, `END|<CRC32>`.
   - Vérification mathématique de la somme de contrôle `CRC32` IEEE 802.3 (`verify_embedded_frame_crc`).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest

from app.collectors.flood_air import (
    OpenMeteoAirQualityCollector,
    OpenMeteoFloodCollector,
    USGSVolcanoCollector,
)
from app.collectors.weather import OpenMeteoCollector
from app.main import aggregator, app
from app.models.schemas import (
    DeviceType,
    HazardAlert,
    HazardCategory,
    MonitoredZoneUpsertRequest,
    SeverityLevel,
    SimulationRequest,
    SimulationScenario,
)
from app.services.embedded_frame import verify_embedded_frame_crc
from app.services.storage_wal import SQLiteWALStorage


# ============================================================================
# 1. Tests des Collecteurs Flood (GloFAS), Air Quality (Dust/Ash) & Volcano
# ============================================================================


def test_open_meteo_flood_glofas_parser() -> None:
    """Vérifie le calcul d'anomalie de débit fluvial et de niveau de crue GloFAS."""
    # Cas 1 : Crue majeure (débit max 420 m³/s pour une moyenne de 65 m³/s -> ratio 6.46 -> RED)
    flood_red_payload = {
        "daily": {
            "river_discharge": [180.0, 310.0, 420.0],
            "river_discharge_mean": [65.0, 65.0, 65.0],
            "river_discharge_max": [210.0, 350.0, 420.0],
        }
    }
    res_red = OpenMeteoFloodCollector.parse_response(flood_red_payload)
    assert res_red.river_discharge_m3s == 180.0
    assert res_red.river_discharge_max_m3s == 420.0
    assert res_red.anomaly_ratio >= 5.0
    assert res_red.flood_alert_level == SeverityLevel.RED

    # Cas 2 : Débit normal (ratio ~1.1 -> GREEN)
    flood_green_payload = {
        "daily": {
            "river_discharge": [42.0, 44.0],
            "river_discharge_mean": [40.0, 40.0],
            "river_discharge_max": [45.0, 46.0],
        }
    }
    res_green = OpenMeteoFloodCollector.parse_response(flood_green_payload)
    assert res_green.flood_alert_level == SeverityLevel.GREEN


def test_open_meteo_air_quality_and_dust_parser() -> None:
    """Vérifie la détection des panaches de poussières / cendres volcaniques et l'indice AQI/UV."""
    payload = {
        "current": {
            "european_aqi": 115,
            "us_aqi": 142,
            "pm2_5": 48.5,
            "pm10": 165.0,
            "uv_index": 6.4,
            "dust": 185.0,
        }
    }
    status = OpenMeteoAirQualityCollector.parse_response(payload)
    assert status.european_aqi == 115
    assert status.us_aqi == 142
    assert status.pm2_5_ugm3 == 48.5
    assert status.pm10_ugm3 == 165.0
    assert status.uv_index == 6.4
    assert status.dust_ugm3 == 185.0
    assert status.ash_or_dust_warning is True


def test_usgs_volcano_collector_parser() -> None:
    """Vérifie le parsing des codes d'alerte aviation volcanique (RED, ORANGE, YELLOW)."""
    payload = [
        {
            "vnum": "311240",
            "v_name": "Kilauea",
            "latitude": 19.421,
            "longitude": -155.287,
            "color_code": "RED",
            "alert_level": "WARNING",
            "vei": 4,
        },
        {
            "vnum": "371020",
            "v_name": "Svartsengi / Reykjanes",
            "latitude": 63.88,
            "longitude": -22.43,
            "color_code": "ORANGE",
            "alert_level": "WATCH",
        },
    ]
    alerts = USGSVolcanoCollector.parse_volcanoes(payload)
    assert len(alerts) == 2
    assert alerts[0].category == HazardCategory.VOLCANO
    assert alerts[0].severity == SeverityLevel.RED
    assert alerts[0].value_primary == 4.0
    assert alerts[0].value_unit == "VEI"
    assert alerts[1].severity == SeverityLevel.ORANGE


# ============================================================================
# 2. Tests SQLite WAL Storage (Watchlist, Historique, Snapshot Cache)
# ============================================================================


def test_sqlite_wal_storage_zones_history_and_snapshot(tmp_path: Path) -> None:
    """Teste en isolation une base SQLite WAL temporaire (Watchlist, History, Snapshot Cache)."""
    db_file = tmp_path / "test_vigie_wal.db"
    store = SQLiteWALStorage(db_path=db_file)

    # 1. Vérifier que le mode WAL est bien actif
    assert store.get_journal_mode() == "wal"

    # 2. Vérifier le seed initial de 5 zones mondiales
    zones = store.list_zones()
    assert len(zones) == 5

    # 3. Ajouter une nouvelle zone maritime (Brest, FR)
    new_zone = store.upsert_zone(
        MonitoredZoneUpsertRequest(
            id="z_brest",
            name="Rade de Brest, FR",
            lat=48.3904,
            lon=-4.4861,
            utc_offset_s=3600,
            alert_radius_km=400.0,
            is_marine=True,
        )
    )
    assert new_zone.id == "z_brest"
    assert new_zone.is_marine is True
    assert len(store.list_zones()) == 6

    # 4. Supprimer la zone ajoutée
    assert store.delete_zone("z_brest") is True
    assert store.delete_zone("z_brest") is False
    assert len(store.list_zones()) == 5

    # 5. Enregistrer et requêter l'historique des catastrophes
    h1 = HazardAlert(
        id="hist_eq_1",
        category=HazardCategory.EARTHQUAKE,
        severity=SeverityLevel.RED,
        title="M 7.5 - Mindanao, Philippines",
        source="USGS+EMSC",
        sources=["USGS", "EMSC"],
        timestamp_utc=1792334000,
        lat=8.15,
        lon=126.30,
        value_primary=7.5,
        value_unit="Mw",
        tsunami_warning=True,
    )
    h2 = HazardAlert(
        id="hist_vo_2",
        category=HazardCategory.VOLCANO,
        severity=SeverityLevel.ORANGE,
        title="Volcan Etna (Code Aviation ORANGE)",
        source="USGS_VOLCANO",
        sources=["USGS_VOLCANO"],
        timestamp_utc=1792334100,
        lat=37.75,
        lon=14.99,
        value_primary=3.0,
        value_unit="VEI",
        tsunami_warning=False,
    )
    count = store.record_hazards([h1, h2])
    assert count == 2

    all_hist = store.query_history(limit=10)
    assert len(all_hist) == 2
    assert all_hist[0].id == "hist_eq_1"  # RED en premier

    red_only = store.query_history(min_severity=SeverityLevel.RED)
    assert len(red_only) == 1
    assert red_only[0].category == HazardCategory.EARTHQUAKE

    volcano_only = store.query_history(category=HazardCategory.VOLCANO)
    assert len(volcano_only) == 1
    assert volcano_only[0].id == "hist_vo_2"


# ============================================================================
# 3. Tests Endpoints REST (/v1/zones, /v1/history, /v1/embedded/frame)
# ============================================================================


@pytest.fixture()
def wal_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Client FastAPI raccordé à une base SQLite WAL de test isolée."""
    test_store = SQLiteWALStorage(db_path=tmp_path / "api_wal.db")
    monkeypatch.setattr(aggregator, "storage", test_store)
    aggregator.refresh_zones_from_storage()

    sample_om_payload = {
        "utc_offset_seconds": 3600,
        "current": {
            "temperature_2m": 17.5,
            "apparent_temperature": 17.0,
            "relative_humidity_2m": 74,
            "surface_pressure": 1014.0,
            "wind_speed_10m": 22.0,
            "wind_gusts_10m": 34.0,
            "wind_direction_10m": 260,
            "precipitation": 0.2,
            "weather_code": 2,
        },
        "minutely_15": {"precipitation": [0.0, 0.1, 0.2, 0.0]},
        "daily": {
            "time": ["2026-10-18", "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"],
            "weather_code": [2, 1, 3, 61, 2],
            "temperature_2m_min": [11.0, 12.0, 10.5, 11.5, 12.0],
            "temperature_2m_max": [18.5, 19.0, 17.5, 16.0, 18.0],
            "precipitation_sum": [0.4, 0.0, 0.0, 3.2, 0.0],
            "wind_speed_10m_max": [28.0, 22.0, 19.0, 35.0, 24.0],
            "uv_index_max": [3.2, 4.0, 3.8, 2.5, 3.5],
        },
    }
    parsed_weather = OpenMeteoCollector.parse_response(sample_om_payload)

    monkeypatch.setattr(aggregator.usgs, "fetch", AsyncMock(return_value=[]))
    monkeypatch.setattr(aggregator.emsc, "fetch", AsyncMock(return_value=[]))
    monkeypatch.setattr(aggregator.gdacs, "fetch", AsyncMock(return_value=[]))
    monkeypatch.setattr(aggregator.noaa_nws, "fetch", AsyncMock(return_value=[]))
    monkeypatch.setattr(aggregator.volcano, "fetch", AsyncMock(return_value=[]))
    monkeypatch.setattr(aggregator.open_meteo, "fetch_point", AsyncMock(return_value=parsed_weather))

    aggregator.trigger_simulation(SimulationRequest(scenario=SimulationScenario.CLEAR))
    return TestClient(app)


def test_api_zones_crud_and_history(wal_client: TestClient) -> None:
    """Vérifie les routes `/v1/zones` (GET, POST, DELETE) et `/v1/history`."""
    # 1. Liste initiale
    r_list = wal_client.get("/v1/zones")
    assert r_list.status_code == 200
    assert len(r_list.json()) == 5

    # 2. Création d'une zone surveillée
    r_post = wal_client.post(
        "/v1/zones",
        json={
            "id": "z_lille",
            "name": "Lille, FR",
            "lat": 50.6292,
            "lon": 3.0573,
            "utc_offset_s": 3600,
            "alert_radius_km": 350.0,
            "is_marine": False,
        },
    )
    assert r_post.status_code == 200
    assert r_post.json()["id"] == "z_lille"
    assert len(wal_client.get("/v1/zones").json()) == 6

    # 3. Suppression de la zone
    r_del = wal_client.delete("/v1/zones/z_lille")
    assert r_del.status_code == 200
    assert len(wal_client.get("/v1/zones").json()) == 5

    # 4. Injection d'une simulation et vérification dans `/v1/history`
    wal_client.post("/v1/simulate", json={"scenario": "volcano_eruption_iceland"})
    r_hist = wal_client.get("/v1/history?category=VOLCANO&min_severity=RED")
    assert r_hist.status_code == 200
    hist_data = r_hist.json()
    assert hist_data["count"] >= 1
    assert hist_data["items"][0]["category"] == "VOLCANO"


def test_embedded_frame_endpoint_and_crc32_verification(wal_client: TestClient) -> None:
    """Vérifie que `GET /v1/embedded/frame` génère une trame C++ compacte valide avec CRC32 exact."""
    wal_client.post("/v1/simulate", json={"scenario": "mega_tsunami_japan"})

    resp = wal_client.get("/v1/embedded/frame?device_type=tab5&lat=35.68&lon=139.69&marine=true")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]

    frame_text = resp.text
    assert len(frame_text.encode("utf-8")) < 8192  # Ultra-léger (< 8 Ko)
    assert verify_embedded_frame_crc(frame_text) is True

    lines = [line for line in frame_text.strip().split("\n") if line]
    assert lines[0].startswith("HEAD|")
    assert "|0|1.1|tab5|RED|" in lines[0]
    assert any(line.startswith("ZONE|GPS|Position GPS / Bateau|35.6800|139.6900|") for line in lines)
    assert sum(1 for line in lines if line.startswith("ZONE|")) == 6  # 1 GPS + 5 zones surveillées
    assert sum(1 for line in lines if line.startswith("RAIN5M|")) == 6
    assert sum(1 for line in lines if line.startswith("DAY|")) >= 6
    assert sum(1 for line in lines if line.startswith("HAZ|")) >= 2
    assert lines[-1].startswith("END|") and len(lines[-1].split("|")[1]) == 8

    # Vérifier également que le dernier snapshot a été persisté dans SQLite WAL
    cached_snap = aggregator.storage.load_last_snapshot(DeviceType.TAB5)
    assert cached_snap is not None
    assert cached_snap.device_type == DeviceType.TAB5
    assert cached_snap.global_status.max_severity == SeverityLevel.RED

