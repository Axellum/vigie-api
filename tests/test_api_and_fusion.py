"""Tests unitaires et d'intégration de Vigie Mondiale API (`alerte_mondiale_api`).

Vérifie :
1. La géodésie exacte (`haversine_km`, `initial_bearing_deg`, `bearing_to_cardinal`, `estimate_tsunami_eta_minutes`).
2. La déduplication spatiotemporelle tri-providers (`USGS + EMSC + GDACS`).
3. Le Circuit Breaker (`CLOSED -> OPEN -> HALF_OPEN`) et la bascule automatique sur le
   filet de sécurité météo `MET Norway` lorsque le primaire `Open-Meteo` échoue.
4. Les parsers des collecteurs (`USGS`, `EMSC`, `GDACS`, `NOAA NWS`, `Open-Meteo`, `MET Norway`,
   `RainViewer`, `Open-Meteo Marine`, `NOAA SWPC`).
5. Les endpoints FastAPI (`/v1/health`, `/v1/providers`, `/v1/point`, `/v1/snapshot`,
   `/v1/geojson/hazards`, `/v1/cap/alerts`, `/v1/simulate`, `WS /v1/stream`), les déclinaisons
   matérielles (`waveshare-7b`, `tab5`, `marine`, `web`) et l'invariant `< 12 Ko`.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest

from app.collectors.base import CollectorUnavailableError
from app.collectors.disasters import GDACSCollector, NOAANWSCollector
from app.collectors.marine import OpenMeteoMarineCollector
from app.collectors.seismic import EMSCCollector, USGSCollector
from app.collectors.space_weather import NOAASWPCCollector
from app.collectors.weather import MetNorwayCollector, OpenMeteoCollector, RainViewerCollector
from app.main import aggregator, app
from app.models.schemas import (
    CircuitState,
    DeviceType,
    HazardAlert,
    HazardCategory,
    SeverityLevel,
    SimulationRequest,
    SimulationScenario,
)
from app.services.aggregator import AggregatorService
from app.services.geodesy import (
    bearing_to_cardinal,
    deduplicate_hazards,
    estimate_tsunami_eta_minutes,
    haversine_km,
    initial_bearing_deg,
)


# ============================================================================
# 1. Tests Géodésie (Haversine, Relèvement/Cap, ETA Tsunami)
# ============================================================================


def test_haversine_and_bearing_accuracy() -> None:
    """Vérifie la précision orthodromique Haversine et le calcul de cap cardinal."""
    # Distance nulle
    assert haversine_km(48.8566, 2.3522, 48.8566, 2.3522) == 0.0
    assert initial_bearing_deg(48.8566, 2.3522, 48.8566, 2.3522) == 0.0

    # Paris (48.8566, 2.3522) -> Tokyo (35.6762, 139.6503) ≈ 9713 km, cap initial NE
    dist_paris_tokyo = haversine_km(48.8566, 2.3522, 35.6762, 139.6503)
    assert 9700.0 <= dist_paris_tokyo <= 9730.0

    bearing_pt = initial_bearing_deg(48.8566, 2.3522, 35.6762, 139.6503)
    assert 30.0 <= bearing_pt <= 40.0
    assert bearing_to_cardinal(bearing_pt) == "NE"

    # Cap plein Nord, Est, Sud, Ouest
    assert bearing_to_cardinal(initial_bearing_deg(0.0, 0.0, 10.0, 0.0)) == "N"
    assert bearing_to_cardinal(initial_bearing_deg(0.0, 0.0, 0.0, 10.0)) == "E"
    assert bearing_to_cardinal(initial_bearing_deg(10.0, 0.0, 0.0, 0.0)) == "S"
    assert bearing_to_cardinal(initial_bearing_deg(0.0, 10.0, 0.0, 0.0)) == "W"


def test_tsunami_eta_estimation() -> None:
    """Vérifie la formule physique d'ETA tsunami (c = sqrt(g * d) ≈ 713 km/h à 4000 m)."""
    assert estimate_tsunami_eta_minutes(0.0) == 0
    # À 713 km en océan profond (4000m), l'onde met environ 60 minutes
    eta_713km = estimate_tsunami_eta_minutes(713.0, avg_depth_m=4000.0)
    assert 58 <= eta_713km <= 62
    # À 356 km -> ~30 minutes
    eta_356km = estimate_tsunami_eta_minutes(356.5, avg_depth_m=4000.0)
    assert 29 <= eta_356km <= 31


# ============================================================================
# 2. Tests Déduplication Multi-Providers (USGS + EMSC + GDACS)
# ============================================================================


def test_deduplicate_hazards_tri_providers() -> None:
    """Vérifie qu'un même séisme vu par USGS, EMSC et GDACS est fusionné en une seule alerte enrichie."""
    base_ts = 1792334500

    eq_usgs = HazardAlert(
        id="usgs_us7000sendai",
        category=HazardCategory.EARTHQUAKE,
        severity=SeverityLevel.ORANGE,
        title="M 7.3 - Off the coast of Miyagi, Japan",
        source="USGS",
        sources=["USGS"],
        timestamp_utc=base_ts,
        lat=38.10,
        lon=142.85,
        value_primary=7.3,
        value_unit="Mw",
        depth_km=32.0,
        tsunami_warning=True,
    )

    # Même séisme détecté 18 secondes plus tard par EMSC à 12 km d'écart avec magnitude 7.4
    eq_emsc = HazardAlert(
        id="emsc_20261018_001",
        category=HazardCategory.EARTHQUAKE,
        severity=SeverityLevel.ORANGE,
        title="M 7.4 - NEAR EAST COAST OF HONSHU, JAPAN",
        source="EMSC",
        sources=["EMSC"],
        timestamp_utc=base_ts + 18,
        lat=38.15,
        lon=142.92,
        value_primary=7.4,
        value_unit="Mw",
        depth_km=30.0,
        tsunami_warning=False,
    )

    # Même séisme évalué par GDACS en alerte RED 45 secondes plus tard
    eq_gdacs = HazardAlert(
        id="gdacs_eq_140293",
        category=HazardCategory.EARTHQUAKE,
        severity=SeverityLevel.RED,
        title="Earthquake in Japan",
        source="GDACS",
        sources=["GDACS"],
        timestamp_utc=base_ts + 45,
        lat=38.08,
        lon=142.80,
        value_primary=7.2,
        value_unit="Mw",
        depth_km=None,
        tsunami_warning=True,
    )

    # Un autre séisme distinct au Chili
    eq_chile = HazardAlert(
        id="usgs_us7000chile",
        category=HazardCategory.EARTHQUAKE,
        severity=SeverityLevel.YELLOW,
        title="M 5.4 - Antofagasta, Chile",
        source="USGS",
        sources=["USGS"],
        timestamp_utc=base_ts - 600,
        lat=-23.65,
        lon=-70.40,
        value_primary=5.4,
        value_unit="Mw",
        depth_km=45.0,
        tsunami_warning=False,
    )

    fused = deduplicate_hazards([eq_emsc, eq_usgs, eq_gdacs, eq_chile])
    assert len(fused) == 2

    japan_alert = fused[0]
    assert japan_alert.severity == SeverityLevel.RED
    assert japan_alert.value_primary == 7.4  # Meilleure magnitude conservée
    assert japan_alert.tsunami_warning is True
    assert set(japan_alert.sources) == {"USGS", "EMSC", "GDACS"}
    assert "USGS" in japan_alert.source and "EMSC" in japan_alert.source and "GDACS" in japan_alert.source


# ============================================================================
# 3. Tests Parsers Multi-Providers
# ============================================================================


def test_collectors_parsers() -> None:
    """Vérifie les parseurs unitaires de USGS, EMSC, GDACS, NOAA NWS, Open-Meteo, MET Norway, Marine et SWPC."""
    usgs_data = {
        "features": [
            {
                "id": "us1234",
                "properties": {"mag": 6.8, "place": "Sendai, Japan", "time": 1792334500000, "tsunami": 1, "alert": "red"},
                "geometry": {"coordinates": [142.85, 38.10, 25.0]},
            }
        ]
    }
    parsed_usgs = USGSCollector.parse_geojson(usgs_data)
    assert len(parsed_usgs) == 1
    assert parsed_usgs[0].severity == SeverityLevel.RED
    assert parsed_usgs[0].tsunami_warning is True

    emsc_data = {
        "features": [
            {
                "id": "emsc99",
                "properties": {"mag": 5.3, "flynn_region": "CRETE, GREECE", "time": "2026-10-18T12:00:00Z", "magtype": "ML"},
                "geometry": {"coordinates": [25.1, 35.2, -15.0]},
            }
        ]
    }
    parsed_emsc = EMSCCollector.parse_geojson(emsc_data)
    assert len(parsed_emsc) == 1
    assert parsed_emsc[0].depth_km == 15.0
    assert parsed_emsc[0].severity == SeverityLevel.YELLOW

    gdacs_data = {
        "features": [
            {
                "properties": {
                    "eventtype": "TC",
                    "eventid": "9988",
                    "alertlevel": "Red",
                    "name": "Cyclone Milton",
                    "country": "United States",
                    "severitydata": {"severity": 250.0, "severityunit": "km/h"},
                    "fromdate": "2026-10-18T10:00:00Z",
                },
                "geometry": {"coordinates": [-82.5, 27.5]},
            }
        ]
    }
    parsed_gdacs = GDACSCollector.parse_geojson(gdacs_data)
    assert len(parsed_gdacs) == 1
    assert parsed_gdacs[0].category == HazardCategory.HURRICANE
    assert parsed_gdacs[0].value_primary == 250.0

    noaa_data = {
        "features": [
            {
                "id": "urn:oid:2.49.0.1.840.0.1234",
                "properties": {
                    "event": "Tornado Warning",
                    "severity": "Extreme",
                    "areaDesc": "Oklahoma County, OK; Canadian County, OK",
                    "sent": "2026-10-18T13:00:00+00:00",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-97.6, 35.4], [-97.4, 35.4], [-97.4, 35.6], [-97.6, 35.6], [-97.6, 35.4]]],
                },
            }
        ]
    }
    parsed_noaa = NOAANWSCollector.parse_alerts(noaa_data)
    assert len(parsed_noaa) == 1
    assert parsed_noaa[0].category == HazardCategory.TORNADO
    assert parsed_noaa[0].severity == SeverityLevel.RED

    marine_data = {
        "current": {
            "wave_height": 4.2,
            "wave_direction": 275,
            "wave_period": 11.5,
            "swell_wave_height": 3.8,
            "swell_wave_direction": 280,
            "swell_wave_period": 13.0,
            "ocean_current_velocity": 2.4,
            "ocean_current_direction": 95,
        }
    }
    parsed_marine = OpenMeteoMarineCollector.parse_response(marine_data)
    assert parsed_marine.sea_state_label == "HIGH"
    assert parsed_marine.navigation_warning is True

    swpc_data = [
        ["time_tag", "Kp", "Kp_fraction", "a_running", "station_count"],
        ["2026-10-18 12:00:00.000", "6.33", "6.33", "80", "8"],
    ]
    parsed_swpc = NOAASWPCCollector.parse_kp_json(swpc_data)
    assert parsed_swpc.solar_kp == 6.3
    assert parsed_swpc.solar_storm_level == "G2"
    assert parsed_swpc.gps_degraded is True
    assert parsed_swpc.hf_radio_blackout_risk is True

    rv_data = {
        "host": "https://tilecache.rainviewer.com",
        "radar": {
            "past": [{"time": 1792330000, "path": "/v2/radar/1792330000"}],
            "nowcast": [{"time": 1792330600, "path": "/v2/radar/nowcast_abc"}],
        },
    }
    parsed_rv = RainViewerCollector.parse_maps(rv_data)
    assert parsed_rv["radar_timestamp_utc"] == 1792330600
    assert "{z}/{x}/{y}" in parsed_rv["radar_tile_url_template"]


# ============================================================================
# 4. Tests Circuit Breaker & Bascule Automatique Météo (Open-Meteo -> MET Norway)
# ============================================================================


@pytest.mark.asyncio
async def test_weather_fallback_when_open_meteo_fails() -> None:
    """Vérifie que si Open-Meteo tombe en panne, l'orchestrateur bascule automatiquement sur MET Norway."""
    om_collector = OpenMeteoCollector()
    metno_collector = MetNorwayCollector()

    # Forcer Open-Meteo à échouer 3 fois pour ouvrir son Circuit Breaker
    om_collector._collect = AsyncMock(side_effect=RuntimeError("Open-Meteo HTTP 503 Timeout"))  # type: ignore[method-assign]

    metno_sample_payload: dict[str, Any] = {
        "properties": {
            "timeseries": [
                {
                    "time": "2026-10-18T14:00:00Z",
                    "data": {
                        "instant": {
                            "details": {
                                "air_temperature": 14.8,
                                "relative_humidity": 81.0,
                                "air_pressure_at_sea_level": 1009.5,
                                "wind_speed": 6.0,
                                "wind_from_direction": 255.0,
                            }
                        },
                        "next_1_hours": {
                            "summary": {"symbol_code": "lightrain"},
                            "details": {"precipitation_amount": 1.6},
                        },
                    },
                }
            ]
        }
    }
    metno_collector._collect = AsyncMock(  # type: ignore[method-assign]
        return_value=MetNorwayCollector.parse_response(metno_sample_payload, days=5)
    )

    service = AggregatorService(open_meteo=om_collector, met_norway=metno_collector)

    for _ in range(3):
        bundle, fallback_used = await service.fetch_weather_with_fallback(48.8566, 2.3522, days=5)
        assert fallback_used is True
        assert "MET Norway" in bundle["provider_name"]
        assert bundle["current"].temp_c == 14.8
        assert bundle["current"].rain_1h_mm == 1.6

    # Après 3 échecs consécutifs, le Circuit Breaker d'Open-Meteo doit être à l'état OPEN
    assert om_collector.circuit_state == CircuitState.OPEN
    assert om_collector.consecutive_failures >= 3
    assert metno_collector.circuit_state == CircuitState.CLOSED


# ============================================================================
# 5. Tests d'Intégration FastAPI : Endpoints, Multi-Écrans & Invariant < 12 Ko
# ============================================================================


@pytest.fixture()
def isolated_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Configure l'application FastAPI avec des mocks rapides et déterministes sans appels externes bloquants."""
    sample_om_payload = {
        "utc_offset_seconds": 3600,
        "current": {
            "temperature_2m": 18.2,
            "apparent_temperature": 17.6,
            "relative_humidity_2m": 68,
            "surface_pressure": 1016.4,
            "wind_speed_10m": 19.5,
            "wind_gusts_10m": 31.0,
            "wind_direction_10m": 245,
            "precipitation": 0.4,
            "weather_code": 61,
        },
        "minutely_15": {"precipitation": [0.0, 0.2, 0.5, 0.1]},
        "daily": {
            "time": ["2026-10-18", "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"],
            "weather_code": [61, 2, 1, 3, 63],
            "temperature_2m_min": [12.0, 11.5, 10.0, 11.0, 13.0],
            "temperature_2m_max": [19.0, 20.5, 21.0, 18.5, 17.0],
            "precipitation_sum": [2.4, 0.0, 0.0, 0.5, 6.2],
            "wind_speed_10m_max": [32.0, 20.0, 18.0, 25.0, 42.0],
            "uv_index_max": [3.5, 4.0, 4.2, 3.0, 2.5],
        },
    }
    parsed_weather = OpenMeteoCollector.parse_response(sample_om_payload)

    eq_base = HazardAlert(
        id="usgs_us7000test",
        category=HazardCategory.EARTHQUAKE,
        severity=SeverityLevel.ORANGE,
        title="M 6.5 - Crete, Greece",
        source="USGS",
        sources=["USGS"],
        timestamp_utc=1792334500,
        lat=35.15,
        lon=25.20,
        value_primary=6.5,
        value_unit="Mw",
        depth_km=18.0,
        tsunami_warning=True,
    )
    eq_dup_emsc = HazardAlert(
        id="emsc_test_dup",
        category=HazardCategory.EARTHQUAKE,
        severity=SeverityLevel.ORANGE,
        title="M 6.6 - CRETE, GREECE",
        source="EMSC",
        sources=["EMSC"],
        timestamp_utc=1792334515,
        lat=35.18,
        lon=25.24,
        value_primary=6.6,
        value_unit="Mw",
        depth_km=16.0,
        tsunami_warning=False,
    )

    monkeypatch.setattr(aggregator.usgs, "fetch", AsyncMock(return_value=[eq_base]))
    monkeypatch.setattr(aggregator.emsc, "fetch", AsyncMock(return_value=[eq_dup_emsc]))
    monkeypatch.setattr(aggregator.gdacs, "fetch", AsyncMock(return_value=[]))
    monkeypatch.setattr(aggregator.noaa_nws, "fetch", AsyncMock(return_value=[]))
    monkeypatch.setattr(aggregator.volcano, "fetch", AsyncMock(return_value=[]))
    monkeypatch.setattr(aggregator.open_meteo, "fetch_point", AsyncMock(return_value=parsed_weather))
    monkeypatch.setattr(
        aggregator.rainviewer,
        "fetch",
        AsyncMock(
            return_value={
                "radar_timestamp_utc": 1792334400,
                "radar_tile_url_template": "https://tilecache.rainviewer.com/v2/radar/1792334400/256/{z}/{x}/{y}/2/1_1.png",
            }
        ),
    )

    # Remettre la simulation à zéro avant chaque test
    aggregator.trigger_simulation(SimulationRequest(scenario=SimulationScenario.CLEAR))
    return TestClient(app)


def test_endpoints_health_and_providers(isolated_client: TestClient) -> None:
    """Vérifie `/v1/health` et `/v1/providers`."""
    r_health = isolated_client.get("/v1/health")
    assert r_health.status_code == 200
    data = r_health.json()
    assert data["status"] == "ok"
    assert data["storage_journal_mode"] == "wal"
    assert "waveshare-7b" in data["supported_devices"]
    assert "tab5" in data["supported_devices"]
    assert data["providers_summary"]["total"] == 12

    r_prov = isolated_client.get("/v1/providers")
    assert r_prov.status_code == 200
    prov_list = r_prov.json()
    assert len(prov_list) == 12
    ids = {p["provider_id"] for p in prov_list}
    assert {
        "usgs_eq",
        "emsc_seismic",
        "gdacs_global",
        "usgs_volcano_gvp",
        "open_meteo_hd",
        "met_norway_fallback",
        "open_meteo_marine",
        "open_meteo_flood",
        "open_meteo_aqi",
    } <= ids


def test_endpoint_point_gps_marine_and_tab5(isolated_client: TestClient) -> None:
    """Vérifie `/v1/point` en mode GPS Bateau/Marine et M5Stack Tab5."""
    resp = isolated_client.get(
        "/v1/point?lat=47.75&lon=-3.36&marine=true&radius_km=3500&sog_knots=8.2&cog_deg=215&device_type=marine"
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["device_type"] == "marine"
    assert payload["coordinates"]["lat"] == 47.75
    assert payload["coordinates"]["lon"] == -3.36
    assert payload["coordinates"]["sog_knots"] == 8.2
    assert payload["rain_nowcast"]["interval_minutes"] == 15
    assert len(payload["rain_nowcast"]["mm_per_step"]) == 4
    assert payload["rain_nowcast"]["rain_expected_60m"] is True
    assert payload["marine"] is not None
    assert "wave_height_m" in payload["marine"]

    # Vérifier la présence de l'alerte fusionnée USGS+EMSC avec cap, cardinal et ETA tsunami
    hazards = payload["nearby_hazards"]
    assert len(hazards) >= 1
    h0 = hazards[0]
    assert h0["source"] == "USGS+EMSC"
    assert h0["value_primary"] == 6.6
    assert h0["bearing_deg"] is not None
    assert h0["bearing_cardinal"] in {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}
    assert h0["tsunami_eta_minutes"] is not None and h0["tsunami_eta_minutes"] > 0


def test_simulate_snapshot_size_under_12kb_and_standards(isolated_client: TestClient) -> None:
    """Vérifie `/v1/simulate`, `/v1/snapshot` (< 12 Ko), `/v1/geojson/hazards` (RFC 7946) et `/v1/cap/alerts` (CAP 1.2)."""
    # 1. Injecter une crise majeure (Séisme M7.8 + Tsunami Japon)
    sim_resp = isolated_client.post(
        "/v1/simulate",
        json={"scenario": "mega_tsunami_japan"},
    )
    assert sim_resp.status_code == 200
    sim_data = sim_resp.json()
    assert sim_data["simulation_active"] is True
    assert sim_data["injected_hazards_count"] == 2

    # 2. Vérifier le Snapshot pour Waveshare 7B et M5Stack Tab5 et la taille < 12 Ko (12 288 octets)
    for dev in ("waveshare-7b", "tab5"):
        snap_resp = isolated_client.get(f"/v1/snapshot?device_type={dev}&compact=true")
        assert snap_resp.status_code == 200
        raw_bytes = json.dumps(snap_resp.json(), separators=(",", ":")).encode("utf-8")
        assert len(raw_bytes) < 12288, f"Payload trop lourd pour {dev}: {len(raw_bytes)} octets"
        snap_json = snap_resp.json()
        assert snap_json["device_type"] == dev
        assert snap_json["simulation_active"] is True
        assert snap_json["global_status"]["max_severity"] == "RED"
        assert snap_json["global_status"]["active_red_count"] >= 2

    # 3. Vérifier le standard IETF RFC 7946 GeoJSON (/v1/geojson/hazards)
    geo_resp = isolated_client.get("/v1/geojson/hazards")
    assert geo_resp.status_code == 200
    geo = geo_resp.json()
    assert geo["type"] == "FeatureCollection"
    assert isinstance(geo["bbox"], list) and len(geo["bbox"]) == 4
    assert len(geo["features"]) >= 3
    first_feat = geo["features"][0]
    assert first_feat["type"] == "Feature"
    assert first_feat["geometry"]["type"] == "Point"
    assert len(first_feat["geometry"]["coordinates"]) >= 2

    # 4. Vérifier le standard OASIS CAP 1.2 (/v1/cap/alerts)
    cap_resp = isolated_client.get("/v1/cap/alerts")
    assert cap_resp.status_code == 200
    cap = cap_resp.json()
    assert cap["protocol"] == "OASIS CAP 1.2"
    assert cap["alert_count"] >= 3
    first_alert = cap["alerts"][0]
    assert first_alert["xmlns"] == "urn:oasis:names:tc:emergency:cap:1.2"
    assert first_alert["status"] == "Exercise"  # Car simulation active
    info0 = first_alert["info"][0]
    assert info0["severity"] in {"Extreme", "Severe", "Moderate", "Minor"}
    assert info0["urgency"] in {"Immediate", "Expected", "Future", "Past"}
    assert len(info0["area"]) == 1
    assert " " in info0["area"][0]["circle"]

    # 5. Désactiver la simulation
    clear_resp = isolated_client.post("/v1/simulate", json={"scenario": "clear"})
    assert clear_resp.status_code == 200
    assert clear_resp.json()["simulation_active"] is False


def test_websocket_stream(isolated_client: TestClient) -> None:
    """Vérifie la connexion temps réel WebSocket `/v1/stream` (snapshot initial + ping/pong)."""
    with isolated_client.websocket_connect("/v1/stream") as ws:
        init_msg = ws.receive_json()
        assert init_msg["event"] == "initial_snapshot"
        assert "global_status" in init_msg
        ws.send_text("ping")
        pong_msg = ws.receive_json()
        assert pong_msg["event"] == "pong"
