"""Application principale FastAPI (OpenAPI 3.1) — Vigie Mondiale API (`alerte_mondiale_api`).

Endpoints exposés :
- `GET    /dashboard`           : Salle de Crise & Supervision Web (Mode Sombre Tactique autonome).
- `GET    /v1/health`           : Santé globale du backend, mode SQLite WAL et résumé des collecteurs.
- `GET    /v1/providers`        : État détaillé du Circuit Breaker et du cache de chaque agence.
- `GET    /v1/scheduler/status` : État du scheduler temps réel et compteur de cycles.
- `POST   /v1/scheduler/trigger`: Déclenchement immédiat d'un cycle de collecte et push différentiel.
- `GET    /v1/zones`            : Liste des zones mondiales surveillées (Watchlist SQLite WAL).
- `POST   /v1/zones`            : Ajout ou modification dynamique d'une zone surveillée.
- `DELETE /v1/zones/{zone_id}`  : Suppression d'une zone surveillée.
- `GET    /v1/history`          : Historique SQLite WAL des catastrophes mondiales avec filtres.
- `GET    /v1/point`            : Météo hyper-locale GPS (`lat, lon`), pluie 15 min, radar, marine (`marine=true`),
                                  crues GloFAS, qualité de l'air/cendres, météo spatiale et catastrophes proches.
- `GET    /v1/snapshot`         : Snapshot multi-zones + alertes mondiales optimisé `< 12 Ko`
                                  pour toute la gamme d'écrans (`waveshare-7b`, `tab5`, `marine`, `web`).
- `GET    /v1/embedded/frame`   : Trame texte ultra-compacte (`HEAD|...`, `ZONE|...`, `RAIN5M|...`, `DAY|...`, `HAZ|...`, `END|CRC32`)
                                  alignée à 100 % avec `vigie_decoder_trame_api` en C++ sans `malloc` sur ESP32-P4.
- `GET    /v1/geojson/hazards`  : Standard international IETF RFC 7946 GeoJSON (`FeatureCollection`).
- `GET    /v1/cap/alerts`       : Standard international OASIS CAP 1.2 (`Common Alerting Protocol`).
- `POST   /v1/simulate`         : Injection de scénarios de crise réalistes et diffusion WebSocket.
- `WS     /v1/stream`           : Flux temps réel WebSocket pour les terminaux connectés.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from app.models.schemas import (
    CAPFeedResponse,
    DeviceType,
    GeoJSONFeatureCollection,
    HazardCategory,
    HistoryResponse,
    MonitoredZoneConfig,
    MonitoredZoneUpsertRequest,
    PointResponse,
    ProviderHealth,
    SeverityLevel,
    SimulationRequest,
    SimulationResponse,
    SnapshotResponse,
)
from app.routers.dashboard import router as dashboard_router
from app.services.aggregator import AggregatorService
from app.services.scheduler import BackgroundAlertScheduler


class WebSocketHub:
    """Gestionnaire des connexions WebSocket temps réel (Waveshare 7B, M5Stack Tab5, Web)."""

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        stale: list[WebSocket] = []
        for conn in list(self.active_connections):
            try:
                await conn.send_json(message)
            except Exception:
                stale.append(conn)
        for s in stale:
            self.disconnect(s)


aggregator = AggregatorService()
ws_hub = WebSocketHub()
scheduler = BackgroundAlertScheduler(aggregator=aggregator, ws_hub=ws_hub, interval_seconds=90)

app = FastAPI(
    title="Vigie Mondiale API — Multi-Provider Weather, Marine & Global Disasters",
    version="1.1.0",
    openapi_version="3.1.0",
    description=(
        "API industrielle multi-providers de météo hyper-locale par coordonnées GPS/Marine "
        "(Open-Meteo HD + Fallback MET Norway + RainViewer + Open-Meteo Marine + Flood GloFAS + Air Quality/Dust) "
        "et d'alertes catastrophes naturelles mondiales dédupliquées (USGS + EMSC + GDACS + NOAA NWS + USGS Volcano + NOAA SWPC). "
        "Persistance SQLite WAL, Scheduler temps réel, standards IETF RFC 7946 GeoJSON, OASIS CAP 1.2, profils JSON < 12 Ko et trame C++ sans malloc "
        "pour les écrans Waveshare 7B (1024x600), M5Stack Tab5 (1280x720 + GPS Grove) et terminaux marins."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dashboard_router)


@app.get("/v1/health", tags=["Monitoring & Resilience"])
async def get_health() -> dict[str, Any]:
    """Retourne l'état de santé global du serveur, le mode SQLite WAL, le scheduler et la synthèse des collecteurs."""
    providers = aggregator.get_providers_health()
    healthy_count = sum(1 for p in providers if p.healthy)
    return {
        "status": "ok" if healthy_count > 0 else "degraded",
        "version": "1.1.0",
        "server_utc": int(time.time()),
        "uptime_seconds": max(0, int(time.time()) - aggregator.started_at_utc),
        "storage_journal_mode": aggregator.storage.get_journal_mode(),
        "monitored_zones_count": len(aggregator.monitored_zones),
        "simulation_active": aggregator.simulation_active,
        "active_scenario": aggregator.active_scenario.value,
        "scheduler": scheduler.get_status(),
        "supported_devices": [d.value for d in DeviceType],
        "providers_summary": {
            "total": len(providers),
            "healthy": healthy_count,
            "weather_fallback_active": aggregator.last_weather_fallback_used,
        },
        "providers": [p.model_dump() for p in providers],
    }


@app.get("/v1/providers", response_model=list[ProviderHealth], tags=["Monitoring & Resilience"])
async def get_providers() -> list[ProviderHealth]:
    """Retourne l'état détaillé du Circuit Breaker et du cache TTL de chaque provider."""
    return aggregator.get_providers_health()


@app.get("/v1/scheduler/status", tags=["Monitoring & Resilience"])
async def get_scheduler_status() -> dict[str, Any]:
    """Retourne l'état opérationnel du scheduler asynchrone et des alertes suivies."""
    return scheduler.get_status()


@app.post("/v1/scheduler/trigger", tags=["Monitoring & Resilience"])
async def post_scheduler_trigger() -> dict[str, Any]:
    """Déclenche immédiatement un cycle de collecte, détection différentielle et push WebSocket."""
    return await scheduler.run_single_cycle()


@app.get("/v1/zones", response_model=list[MonitoredZoneConfig], tags=["Watchlist & SQLite WAL"])
async def get_zones() -> list[MonitoredZoneConfig]:
    """Liste les zones géographiques surveillées persistées dans SQLite WAL."""
    return aggregator.list_zones()


@app.post("/v1/zones", response_model=MonitoredZoneConfig, tags=["Watchlist & SQLite WAL"])
async def upsert_zone(request: MonitoredZoneUpsertRequest) -> MonitoredZoneConfig:
    """Ajoute ou met à jour une zone surveillée dans SQLite WAL."""
    return aggregator.upsert_zone(request)


@app.delete("/v1/zones/{zone_id}", tags=["Watchlist & SQLite WAL"])
async def delete_zone(zone_id: str) -> dict[str, Any]:
    """Supprime une zone surveillée de la Watchlist SQLite WAL."""
    deleted = aggregator.delete_zone(zone_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Zone '{zone_id}' introuvable")
    return {"status": "deleted", "zone_id": zone_id}


@app.get("/v1/history", response_model=HistoryResponse, tags=["Watchlist & SQLite WAL"])
async def get_hazards_history(
    category: Optional[HazardCategory] = Query(default=None, description="Filtrer par catégorie de catastrophe"),
    min_severity: Optional[SeverityLevel] = Query(default=None, description="Sévérité minimale (GREEN, YELLOW, ORANGE, RED)"),
    limit: int = Query(default=50, ge=1, le=500, description="Nombre maximal d'entrées historiques"),
) -> HistoryResponse:
    """Interroge l'historique SQLite WAL des catastrophes mondiales dédupliquées."""
    return aggregator.get_history(category=category, min_severity=min_severity, limit=limit)


@app.get("/v1/point", response_model=PointResponse, tags=["Embedded & GPS Mobile / Marine"])
async def get_point_weather_and_hazards(
    lat: float = Query(..., ge=-90.0, le=90.0, description="Latitude GPS exacte (WGS84)"),
    lon: float = Query(..., ge=-180.0, le=180.0, description="Longitude GPS exacte (WGS84)"),
    marine: bool = Query(default=False, description="Activer la météo marine (vagues, houle, courants)"),
    radius_km: float = Query(default=500.0, gt=0.0, le=5000.0, description="Rayon de recherche d'alertes (km)"),
    sog_knots: Optional[float] = Query(default=None, ge=0.0, description="Vitesse fond du navire/véhicule (nœuds)"),
    cog_deg: Optional[float] = Query(default=None, ge=0.0, lt=360.0, description="Route fond (degrés)"),
    device_type: DeviceType = Query(
        default=DeviceType.TAB5,
        description="Profil d'écran ou de client (tab5, waveshare-7b, marine, web, generic)",
    ),
    max_hazards: Optional[int] = Query(default=None, ge=1, le=100, description="Nombre maximal d'alertes retournées"),
    compact: bool = Query(default=True, description="Profil compact (< 12 Ko) pour ESP32-P4"),
) -> PointResponse:
    """Fournit la météo hyper-locale (15 min), les conditions marines, crues GloFAS, air/poussières et alertes autour d'un point GPS."""
    return await aggregator.build_point_response(
        lat=lat,
        lon=lon,
        marine=marine,
        radius_km=radius_km,
        sog_knots=sog_knots,
        cog_deg=cog_deg,
        device_type=device_type,
        max_hazards=max_hazards,
        compact=compact,
    )


@app.get("/v1/snapshot", response_model=SnapshotResponse, tags=["Embedded & Multi-Display"])
async def get_snapshot(
    device_type: DeviceType = Query(
        default=DeviceType.WAVESHARE_7B,
        description="Profil d'écran cible (waveshare-7b, tab5, marine, web, generic)",
    ),
    max_hazards: Optional[int] = Query(
        default=None, ge=1, le=50, description="Nombre maximal d'alertes catastrophes dans le snapshot"
    ),
    compact: bool = Query(default=True, description="Garantit un payload < 12 Ko pour ESP32-P4"),
) -> SnapshotResponse:
    """Retourne le snapshot complet multi-zones + alertes mondiales dédupliquées (< 12 Ko)."""
    return await aggregator.build_snapshot(
        device_type=device_type,
        max_hazards=max_hazards,
        compact=compact,
    )


@app.get("/v1/embedded/frame", response_class=PlainTextResponse, tags=["Embedded & Multi-Display"])
async def get_embedded_frame(
    device_type: DeviceType = Query(
        default=DeviceType.WAVESHARE_7B,
        description="Profil matériel cible (waveshare-7b, tab5, marine)",
    ),
    lat: Optional[float] = Query(default=None, ge=-90.0, le=90.0, description="Latitude GPS optionnelle"),
    lon: Optional[float] = Query(default=None, ge=-180.0, le=180.0, description="Longitude GPS optionnelle"),
    marine: bool = Query(default=False, description="Inclure les vagues et l'état de la mer dans la zone GPS"),
    max_hazards: int = Query(default=12, ge=1, le=30, description="Nombre maximal de lignes HAZ"),
) -> PlainTextResponse:
    """Génère la trame texte compacte (`HEAD|...`, `ZONE|...`, `RAIN5M|...`, `DAY|...`, `HAZ|...`, `END|CRC32`) décodable sans `malloc` en C++."""
    frame_str = await aggregator.build_embedded_frame(
        device_type=device_type,
        lat=lat,
        lon=lon,
        marine=marine,
        max_hazards=max_hazards,
    )
    return PlainTextResponse(content=frame_str, media_type="text/plain; charset=utf-8")


@app.get("/v1/geojson/hazards", response_model=GeoJSONFeatureCollection, tags=["International Standards"])
async def get_geojson_hazards() -> GeoJSONFeatureCollection:
    """Exporte toutes les catastrophes actives au format standard IETF RFC 7946 GeoJSON."""
    return await aggregator.build_geojson_feature_collection()


@app.get("/v1/cap/alerts", response_model=CAPFeedResponse, tags=["International Standards"])
async def get_cap_alerts() -> CAPFeedResponse:
    """Exporte toutes les alertes actives au format standard OASIS CAP 1.2 (Common Alerting Protocol)."""
    return await aggregator.build_cap_feed()


@app.post("/v1/simulate", response_model=SimulationResponse, tags=["Crisis Simulator"])
async def post_simulate(request: SimulationRequest) -> SimulationResponse:
    """Injecte un scénario de crise réaliste et notifie instantanément les clients WebSocket."""
    response = aggregator.trigger_simulation(request)
    await ws_hub.broadcast(
        {
            "event": "simulation_update",
            "scenario": response.scenario.value,
            "simulation_active": response.simulation_active,
            "hazards": [h.model_dump() for h in response.injected_hazards],
        }
    )
    return response


@app.websocket("/v1/stream")
async def websocket_stream(websocket: WebSocket) -> None:
    """Flux temps réel WebSocket poussant l'état initial puis les mises à jour d'alertes."""
    await ws_hub.connect(websocket)
    try:
        initial_snap = await aggregator.build_snapshot()
        await websocket.send_json(
            {
                "event": "initial_snapshot",
                "server_utc": initial_snap.server_utc,
                "global_status": initial_snap.global_status.model_dump(),
                "hazards_count": len(initial_snap.hazards),
            }
        )
        while True:
            msg = await websocket.receive_text()
            if msg.strip().lower() == "ping":
                await websocket.send_json({"event": "pong", "server_utc": int(time.time())})
    except WebSocketDisconnect:
        ws_hub.disconnect(websocket)
    except Exception:
        ws_hub.disconnect(websocket)
