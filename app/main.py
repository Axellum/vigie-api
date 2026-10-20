"""FastAPI application (OpenAPI 3.1) of the Vigie API.

Endpoints:

- ``GET    /dashboard``            : Web supervision dashboard.
- ``GET    /v1/health``            : Global health, SQLite mode and collector summary.
- ``GET    /v1/providers``         : Circuit breaker, cache state, calls, weight and quota of every provider.
- ``GET    /v1/scheduler/status``  : Scheduler state and cycle counter.
- ``POST   /v1/scheduler/trigger`` : Run one collection cycle and differential push now.
- ``GET    /v1/zones``             : Watched zones (SQLite watchlist).
- ``POST   /v1/zones``             : Add or update a watched zone.
- ``DELETE /v1/zones/{zone_id}``   : Delete a watched zone.
- ``GET    /v1/history``           : Hazard history from SQLite, with filters.
- ``GET    /v1/point``             : Weather at a GPS position (``lat, lon``): 15-minute rain, radar,
                                     marine (``marine=true``), river discharge, air quality / ash,
                                     space weather and nearby hazards.
- ``GET    /v1/snapshot``          : Multi-zone snapshot plus world hazards, under 12 KiB, for every
                                     display profile (``waveshare-7b``, ``tab5``, ``marine``, ``web``).
- ``GET    /v1/embedded/frame``    : Compact text frame for the ESP32-P4 displays
                                     (contract: ``docs/embedded-frame.md``).
- ``GET    /v1/geojson/hazards``   : IETF RFC 7946 GeoJSON (``FeatureCollection``).
- ``GET    /v1/cap/alerts``        : OASIS CAP 1.2 (Common Alerting Protocol).
- ``POST   /v1/simulate``          : Inject a simulated scenario (always labelled as a simulation). Off unless
                                     ``VIGIE_ENABLE_SIMULATION=1``: it answers 403 otherwise.
- ``WS     /v1/stream``            : Real-time WebSocket stream for connected terminals.
"""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from app import __version__
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
    """Manager of the real-time WebSocket connections (Waveshare 7B, M5Stack Tab5, web)."""

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


def _env_flag(name: str) -> bool:
    """True when the environment variable ``name`` is ``1`` / ``true`` / ``yes`` / ``on`` (default: off)."""
    return os.environ.get(name, "0").strip().lower() in {"1", "true", "yes", "on"}


def scheduler_enabled() -> bool:
    """The background scheduler runs only when ``VIGIE_SCHEDULER`` is ``1`` / ``true`` / ``yes`` / ``on``."""
    return _env_flag("VIGIE_SCHEDULER")


def simulation_enabled() -> bool:
    """``POST /v1/simulate`` works only when ``VIGIE_ENABLE_SIMULATION`` is ``1`` / ``true`` / ``yes`` / ``on``.

    The route has no authentication and writes simulated alerts into the hazard history, so an
    instance that is reachable by others must keep it off (the default). Read at each request.
    """
    return _env_flag("VIGIE_ENABLE_SIMULATION")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Start the background scheduler when enabled and stop it cleanly on shutdown."""
    if scheduler_enabled():
        scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()


app = FastAPI(
    title="Vigie API",
    version=__version__,
    lifespan=lifespan,
    description=(
        "Aggregates world natural hazards (USGS, EMSC, GDACS, NOAA NWS, USGS Volcano, NOAA SWPC) "
        "and weather (Open-Meteo, MET Norway fallback, RainViewer radar, marine, river discharge, "
        "air quality) for the Vigie displays. A value no provider supplied is reported as unknown "
        "(null, or an empty field in the embedded frame); nothing is ever made up. "
        "Standards: IETF RFC 7946 GeoJSON, OASIS CAP 1.2. "
        "The code is MIT-licensed; the data belongs to each provider under its own terms (SOURCES.md), and every "
        "block of data carries an `attribution` field with the credit its source asks for. "
        "This is not an official warning service: GDACS alerts are automatic and do not replace official warnings."
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


@app.get("/v1/health", tags=["Monitoring"])
async def get_health() -> dict[str, Any]:
    """Return the global health, the SQLite mode, the scheduler and the collector summary."""
    providers = aggregator.get_providers_health()
    healthy_count = sum(1 for p in providers if p.healthy)
    return {
        "status": "ok" if healthy_count > 0 else "degraded",
        "version": __version__,
        "server_utc": int(time.time()),
        "uptime_seconds": max(0, int(time.time()) - aggregator.started_at_utc),
        "storage_journal_mode": aggregator.storage.get_journal_mode(),
        "monitored_zones_count": len(aggregator.monitored_zones),
        "simulation_enabled": simulation_enabled(),
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


@app.get("/v1/providers", response_model=list[ProviderHealth], tags=["Monitoring"])
async def get_providers() -> list[ProviderHealth]:
    """Return the circuit breaker and TTL cache state of each provider, and its calls, weight and quota over 24 hours."""
    return aggregator.get_providers_health()


@app.get("/v1/scheduler/status", tags=["Monitoring"])
async def get_scheduler_status() -> dict[str, Any]:
    """Return the state of the asynchronous scheduler and the tracked alerts."""
    return scheduler.get_status()


@app.post("/v1/scheduler/trigger", tags=["Monitoring"])
async def post_scheduler_trigger() -> dict[str, Any]:
    """Run one collection, differential detection and WebSocket push cycle now."""
    return await scheduler.run_single_cycle()


@app.get("/v1/zones", response_model=list[MonitoredZoneConfig], tags=["Watchlist"])
async def get_zones() -> list[MonitoredZoneConfig]:
    """List the watched geographic zones stored in SQLite."""
    return aggregator.list_zones()


@app.post("/v1/zones", response_model=MonitoredZoneConfig, tags=["Watchlist"])
async def upsert_zone(request: MonitoredZoneUpsertRequest) -> MonitoredZoneConfig:
    """Add or update a watched zone in SQLite."""
    return aggregator.upsert_zone(request)


@app.delete("/v1/zones/{zone_id}", tags=["Watchlist"])
async def delete_zone(zone_id: str) -> dict[str, Any]:
    """Delete a watched zone from the SQLite watchlist."""
    deleted = aggregator.delete_zone(zone_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Zone '{zone_id}' not found")
    return {"status": "deleted", "zone_id": zone_id}


@app.get("/v1/history", response_model=HistoryResponse, tags=["Watchlist"])
async def get_hazards_history(
    category: Optional[HazardCategory] = Query(default=None, description="Filter by hazard category"),
    min_severity: Optional[SeverityLevel] = Query(default=None, description="Minimum severity (GREEN, YELLOW, ORANGE, RED)"),
    limit: int = Query(default=50, ge=1, le=500, description="Maximum number of history entries"),
) -> HistoryResponse:
    """Query the SQLite history of de-duplicated world hazards."""
    return aggregator.get_history(category=category, min_severity=min_severity, limit=limit)


@app.get("/v1/point", response_model=PointResponse, tags=["Embedded & GPS"])
async def get_point_weather_and_hazards(
    lat: float = Query(..., ge=-90.0, le=90.0, description="Exact GPS latitude (WGS84)"),
    lon: float = Query(..., ge=-180.0, le=180.0, description="Exact GPS longitude (WGS84)"),
    marine: bool = Query(default=False, description="Include marine weather (waves, swell, currents)"),
    radius_km: float = Query(default=500.0, gt=0.0, le=5000.0, description="Hazard search radius (km)"),
    sog_knots: Optional[float] = Query(default=None, ge=0.0, description="Speed over ground of the vessel or vehicle (knots)"),
    cog_deg: Optional[float] = Query(default=None, ge=0.0, lt=360.0, description="Course over ground (degrees)"),
    device_type: DeviceType = Query(
        default=DeviceType.TAB5,
        description="Display or client profile (tab5, waveshare-7b, marine, web, generic)",
    ),
    max_hazards: Optional[int] = Query(default=None, ge=1, le=100, description="Maximum number of hazards returned"),
    compact: bool = Query(default=True, description="Compact profile (under 12 KiB) for the ESP32-P4"),
) -> PointResponse:
    """Weather at one position (15-minute rain, marine, river discharge, air, space weather) plus nearby hazards."""
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


@app.get("/v1/snapshot", response_model=SnapshotResponse, tags=["Embedded & GPS"])
async def get_snapshot(
    device_type: DeviceType = Query(
        default=DeviceType.WAVESHARE_7B,
        description="Target display profile (waveshare-7b, tab5, marine, web, generic)",
    ),
    max_hazards: Optional[int] = Query(
        default=None, ge=1, le=50, description="Maximum number of hazards in the snapshot"
    ),
    compact: bool = Query(default=True, description="Keep the payload under 12 KiB for the ESP32-P4"),
) -> SnapshotResponse:
    """Return the full multi-zone snapshot plus the de-duplicated world hazards (under 12 KiB)."""
    return await aggregator.build_snapshot(
        device_type=device_type,
        max_hazards=max_hazards,
        compact=compact,
    )


@app.get("/v1/embedded/frame", response_class=PlainTextResponse, tags=["Embedded & GPS"])
async def get_embedded_frame(
    device_type: DeviceType = Query(
        default=DeviceType.WAVESHARE_7B,
        description="Target hardware profile (waveshare-7b, tab5, marine)",
    ),
    lat: Optional[float] = Query(default=None, ge=-90.0, le=90.0, description="Optional GPS latitude"),
    lon: Optional[float] = Query(default=None, ge=-180.0, le=180.0, description="Optional GPS longitude"),
    marine: bool = Query(default=False, description="Include waves and sea state in the GPS zone"),
    max_hazards: int = Query(default=12, ge=1, le=30, description="Maximum number of HAZ lines"),
) -> PlainTextResponse:
    """Return the compact text frame (HEAD, ZONE, RAIN5M, DAY, HAZ, END with CRC32); see docs/embedded-frame.md."""
    frame_str = await aggregator.build_embedded_frame(
        device_type=device_type,
        lat=lat,
        lon=lon,
        marine=marine,
        max_hazards=max_hazards,
    )
    return PlainTextResponse(content=frame_str, media_type="text/plain; charset=utf-8")


@app.get("/v1/geojson/hazards", response_model=GeoJSONFeatureCollection, tags=["Standards"])
async def get_geojson_hazards() -> GeoJSONFeatureCollection:
    """Export every active hazard as IETF RFC 7946 GeoJSON."""
    return await aggregator.build_geojson_feature_collection()


@app.get("/v1/cap/alerts", response_model=CAPFeedResponse, tags=["Standards"])
async def get_cap_alerts() -> CAPFeedResponse:
    """Export every active alert as OASIS CAP 1.2 (Common Alerting Protocol)."""
    return await aggregator.build_cap_feed()


@app.post(
    "/v1/simulate",
    response_model=SimulationResponse,
    tags=["Simulator"],
    responses={403: {"description": "The simulator is off (VIGIE_ENABLE_SIMULATION is not set to 1)."}},
)
async def post_simulate(request: SimulationRequest) -> SimulationResponse:
    """Inject a simulated scenario (labelled as a simulation) and notify the WebSocket clients.

    Off by default: the route has no authentication and writes the simulated alerts into the hazard
    history. Set ``VIGIE_ENABLE_SIMULATION=1`` on an instance that only trusted clients can reach.
    """
    if not simulation_enabled():
        raise HTTPException(
            status_code=403,
            detail="The simulator is disabled on this instance. Set VIGIE_ENABLE_SIMULATION=1 to enable it.",
        )
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
    """Real-time WebSocket stream: the initial state, then alert updates."""
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
