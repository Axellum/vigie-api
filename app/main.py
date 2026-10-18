"""Application principale FastAPI (OpenAPI 3.1) — Vigie Mondiale API (`alerte_mondiale_api`).

Endpoints exposés :
- `GET  /v1/health`          : Santé globale du backend et résumé des collecteurs.
- `GET  /v1/providers`       : État détaillé du Circuit Breaker et du cache de chaque agence.
- `GET  /v1/point`           : Météo hyper-locale GPS (`lat, lon`), pluie 15 min, radar, marine (`marine=true`),
                               météo spatiale et catastrophes proches avec cap + ETA tsunami.
- `GET  /v1/snapshot`        : Snapshot multi-zones + alertes mondiales optimisé `< 12 Ko`
                               pour toute la gamme d'écrans (`waveshare-7b`, `tab5`, `marine`, `web`).
- `GET  /v1/geojson/hazards` : Standard international IETF RFC 7946 GeoJSON (`FeatureCollection`).
- `GET  /v1/cap/alerts`      : Standard international OASIS CAP 1.2 (`Common Alerting Protocol`).
- `POST /v1/simulate`        : Injection de scénarios de crise réalistes et diffusion WebSocket.
- `WS   /v1/stream`          : Flux temps réel WebSocket pour les terminaux connectés.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.models.schemas import (
    CAPFeedResponse,
    DeviceType,
    GeoJSONFeatureCollection,
    PointResponse,
    ProviderHealth,
    SimulationRequest,
    SimulationResponse,
    SnapshotResponse,
)
from app.services.aggregator import AggregatorService


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

app = FastAPI(
    title="Vigie Mondiale API — Multi-Provider Weather, Marine & Global Disasters",
    version="1.1.0",
    openapi_version="3.1.0",
    description=(
        "API industrielle multi-providers de météo hyper-locale par coordonnées GPS/Marine "
        "(Open-Meteo HD + Fallback MET Norway + RainViewer + Open-Meteo Marine) et d'alertes "
        "catastrophes naturelles mondiales dédupliquées (USGS + EMSC + GDACS + NOAA NWS + NOAA SWPC). "
        "Compatible IETF RFC 7946 GeoJSON, OASIS CAP 1.2 et profils embarqués ESP32-P4 (< 12 Ko) "
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


@app.get("/v1/health", tags=["Monitoring & Resilience"])
async def get_health() -> dict[str, Any]:
    """Retourne l'état de santé global du serveur et la synthèse des collecteurs."""
    providers = aggregator.get_providers_health()
    healthy_count = sum(1 for p in providers if p.healthy)
    return {
        "status": "ok" if healthy_count > 0 else "degraded",
        "version": "1.1.0",
        "server_utc": int(time.time()),
        "uptime_seconds": max(0, int(time.time()) - aggregator.started_at_utc),
        "simulation_active": aggregator.simulation_active,
        "active_scenario": aggregator.active_scenario.value,
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
    """Fournit la météo hyper-locale (15 min), les conditions marines et les alertes autour d'un point GPS."""
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
