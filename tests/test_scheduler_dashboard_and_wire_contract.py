"""Tests unitaires et d'intégration de la Vague 3 (`tests/test_scheduler_dashboard_and_wire_contract.py`).

Vérifie :
1. L'alignement exact colonne par colonne de `serialize_embedded_frame` avec le décodeur C++
   `vigie_decoder_trame_api` de `alerte_mondiale_7b` (`HEAD`, `ZONE`, `RAIN5M`, `DAY`, `HAZ`, `END|CRC32`).
2. Le moteur de détection différentielle (`detect_new_or_upgraded_hazards`) et le scheduler
   temps réel (`BackgroundAlertScheduler`, `GET /v1/scheduler/status`, `POST /v1/scheduler/trigger`).
3. Le Dashboard Web Pro de Supervision & Salle de Crise (`GET /dashboard`).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest

from app.collectors.weather import OpenMeteoCollector
from app.main import aggregator, app, scheduler, ws_hub
from app.models.schemas import (
    HazardAlert,
    HazardCategory,
    SeverityLevel,
    SimulationRequest,
    SimulationScenario,
)
from app.services.embedded_frame import verify_embedded_frame_crc
from app.services.scheduler import detect_new_or_upgraded_hazards
from app.services.storage_wal import SQLiteWALStorage


# ============================================================================
# 1. Tests du Contrat de Trame C++ (`vigie_decoder_trame_api`)
# ============================================================================


@pytest.fixture()
def v3_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Client FastAPI isolé pour la Vague 3."""
    test_store = SQLiteWALStorage(db_path=tmp_path / "v3_wal.db")
    monkeypatch.setattr(aggregator, "storage", test_store)
    aggregator.refresh_zones_from_storage()

    sample_om_payload = {
        "utc_offset_seconds": 3600,
        "current": {
            "temperature_2m": 19.4,
            "apparent_temperature": 18.8,
            "relative_humidity_2m": 66,
            "surface_pressure": 1015.2,
            "wind_speed_10m": 24.0,
            "wind_gusts_10m": 38.5,
            "wind_direction_10m": 250,
            "precipitation": 0.6,
            "weather_code": 61,
        },
        "minutely_15": {"precipitation": [0.0, 0.3, 0.8, 0.2]},
        "daily": {
            "time": ["2026-10-18", "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"],
            "weather_code": [61, 2, 1, 3, 63],
            "temperature_2m_min": [12.0, 11.5, 10.0, 11.0, 13.0],
            "temperature_2m_max": [19.5, 20.5, 21.0, 18.5, 17.0],
            "precipitation_sum": [3.2, 0.0, 0.0, 0.5, 6.2],
            "wind_speed_10m_max": [34.0, 20.0, 18.0, 25.0, 42.0],
            "uv_index_max": [3.5, 4.0, 4.2, 3.0, 2.5],
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
    scheduler._known_hazards.clear()
    scheduler.cycles_count = 0
    return TestClient(app)


def test_cpp_wire_frame_exact_columns_and_crc32(v3_client: TestClient) -> None:
    """Vérifie le nombre et l'ordre exacts des colonnes de chaque ligne pour `vigie_decoder_trame_api`."""
    v3_client.post("/v1/simulate", json={"scenario": "mega_tsunami_japan"})

    resp = v3_client.get("/v1/embedded/frame?device_type=waveshare-7b&lat=48.3904&lon=-4.4861&marine=true")
    assert resp.status_code == 200
    frame_text = resp.text
    assert verify_embedded_frame_crc(frame_text) is True

    lines = [ln for ln in frame_text.strip().split("\n") if ln]

    # 1. HEAD|<server_utc>|<solar_kp:.1f>|0|<version>|<device_type>|<max_severity>|<red_count>|<orange_count>|<yellow_count>|<gps_degraded_0_1>|<hf_risk_0_1>|<sim_0_1>
    head_cols = lines[0].split("|")
    assert head_cols[0] == "HEAD"
    assert len(head_cols) == 13
    assert int(head_cols[1]) > 1700000000  # server_utc en 1er champ
    assert float(head_cols[2]) >= 0.0      # solar_kp en 2e champ
    assert head_cols[3] == "0"             # zone_active_idx en 3e champ
    assert head_cols[4] == "1.1"
    assert head_cols[5] == "waveshare-7b"
    assert head_cols[6] == "RED"

    # 2. Première ligne ZONE = GPS
    zone_lines = [ln.split("|") for ln in lines if ln.startswith("ZONE|")]
    assert len(zone_lines) == 6
    gps_zone = zone_lines[0]
    assert len(gps_zone) == 24
    assert gps_zone[1] == "GPS"
    assert gps_zone[2] == "Position GPS / Bateau"
    assert gps_zone[3] == "48.3904"
    assert gps_zone[4] == "-4.4861"
    # Colonne 18 : 4 valeurs de pluie 15m séparées par virgule
    c15_vals = gps_zone[18].split(",")
    assert len(c15_vals) == 4

    # 3. Lignes RAIN5M : 12 valeurs séparées par virgule
    rain5m_lines = [ln.split("|") for ln in lines if ln.startswith("RAIN5M|")]
    assert len(rain5m_lines) == 6
    assert rain5m_lines[0][1] == "GPS"
    assert len(rain5m_lines[0][2].split(",")) == 12

    # 4. Lignes DAY : 10 colonnes
    day_lines = [ln.split("|") for ln in lines if ln.startswith("DAY|")]
    assert len(day_lines) == 30  # 6 zones * 5 jours
    assert len(day_lines[0]) == 10
    assert day_lines[0][1] == "GPS"

    # 5. Lignes HAZ : 12 colonnes
    # HAZ|<id>|<category>|<severity>|<lat:.4f>|<lon:.4f>|<value_primary:.1f>|<depth_km:.1f>|<tsunami_0_1>|<timestamp_utc>|<sources_plus>|<title>
    haz_lines = [ln.split("|") for ln in lines if ln.startswith("HAZ|")]
    assert len(haz_lines) >= 2
    h0 = haz_lines[0]
    assert len(h0) == 12
    assert h0[0] == "HAZ"
    assert h0[2] in {"EARTHQUAKE", "TSUNAMI"}
    assert h0[3] == "RED"
    assert h0[8] == "1"  # tsunami_0_1
    assert int(h0[9]) > 1700000000  # timestamp_utc


# ============================================================================
# 2. Tests du Détecteur Différentiel & du Scheduler Temps Réel
# ============================================================================


def test_detect_new_or_upgraded_hazards_logic() -> None:
    """Vérifie la détection d'une nouvelle alerte ou d'une aggravation de sévérité."""
    h_yellow = HazardAlert(
        id="eq_1",
        category=HazardCategory.EARTHQUAKE,
        severity=SeverityLevel.YELLOW,
        title="M 5.2 - Test",
        source="USGS",
        sources=["USGS"],
        timestamp_utc=1792334000,
        lat=35.0,
        lon=139.0,
        value_primary=5.2,
        value_unit="Mw",
    )

    # 1er cycle : l'alerte est nouvelle
    diff1 = detect_new_or_upgraded_hazards({}, [h_yellow])
    assert len(diff1) == 1

    # 2e cycle identique : aucun changement
    prev_map = {"eq_1": h_yellow}
    diff2 = detect_new_or_upgraded_hazards(prev_map, [h_yellow])
    assert len(diff2) == 0

    # 3e cycle : aggravation YELLOW -> RED et hausse de magnitude
    h_red = h_yellow.model_copy(update={"severity": SeverityLevel.RED, "value_primary": 7.1, "tsunami_warning": True})
    diff3 = detect_new_or_upgraded_hazards(prev_map, [h_red])
    assert len(diff3) == 1
    assert diff3[0].severity == SeverityLevel.RED


def test_scheduler_endpoints_and_websocket_push(v3_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Vérifie `GET /v1/scheduler/status` et `POST /v1/scheduler/trigger` avec push WebSocket."""
    broadcast_mock = AsyncMock()
    monkeypatch.setattr(ws_hub, "broadcast", broadcast_mock)

    st_init = v3_client.get("/v1/scheduler/status")
    assert st_init.status_code == 200
    assert st_init.json()["cycles_count"] == 0

    # Injecter une crise puis déclencher le scheduler
    v3_client.post("/v1/simulate", json={"scenario": "cat5_hurricane_caribbean"})
    broadcast_mock.reset_mock()

    trig1 = v3_client.post("/v1/scheduler/trigger")
    assert trig1.status_code == 200
    t1_data = trig1.json()
    assert t1_data["status"] == "ok"
    assert t1_data["cycles_count"] == 1
    assert t1_data["new_or_upgraded_count"] == 1
    assert broadcast_mock.await_count == 1

    pushed_payload = broadcast_mock.await_args.args[0]
    assert pushed_payload["event"] == "alert_push"
    assert verify_embedded_frame_crc(pushed_payload["embedded_frame"]) is True

    # Deuxième déclenchement sans nouvelle alerte : pas de nouveau push redondant
    broadcast_mock.reset_mock()
    trig2 = v3_client.post("/v1/scheduler/trigger")
    assert trig2.status_code == 200
    assert trig2.json()["new_or_upgraded_count"] == 0
    assert broadcast_mock.await_count == 0


# ============================================================================
# 3. Test du Dashboard Web Pro de Supervision (`GET /dashboard`)
# ============================================================================


def test_crisis_supervision_dashboard_html(v3_client: TestClient) -> None:
    """Vérifie que `GET /dashboard` retourne le centre de supervision HTML5 complet."""
    resp = v3_client.get("/dashboard")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    html = resp.text
    assert "VIGIE MONDIALE PRO" in html
    assert "mega_tsunami_japan" in html
    assert "/v1/embedded/frame" in html
    assert "waveshare-7b" in html
    assert "tab5" in html
