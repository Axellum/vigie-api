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
- ``GET    /v1/watched-points``    : The points the user watches (a place, a fault, a volcano...). Personal: they are listed
                                     here and in their status, never in the hazards.
- ``POST   /v1/watched-points``    : Add or update a watched point. Off unless ``VIGIE_ENABLE_WATCHED_POINTS_WRITE=1``:
                                     it answers 403 otherwise.
- ``DELETE /v1/watched-points/{point_id}`` : Delete a watched point (same switch).
- ``GET    /v1/watched-points/status`` : For each point, the events of the last days within its radius, and "quiet" only when
                                     every hazard source answered and has been read without a gap.
- ``GET    /v1/history``           : Hazard history from SQLite, with filters.
- ``GET    /v1/point``             : Weather at a GPS position (``lat, lon``): 15-minute rain, radar,
                                     marine (``marine=true``), river discharge, air quality / ash,
                                     space weather and nearby hazards.
- ``GET    /v1/snapshot``          : Multi-zone snapshot plus world hazards, under 16 KiB, for every
                                     display profile (``waveshare-7b``, ``tab5``, ``marine``, ``web``).
- ``GET    /v1/embedded/frame``    : Compact text frame for the ESP32-P4 displays
                                     (contract: ``docs/embedded-frame.md``).
- ``GET    /v1/earthquakes``       : The earthquakes (USGS, EMSC) within a radius of a position over the last hours, strongest
                                     first; no weather provider is asked. Not an official warning.
- ``GET    /v1/sky/aurora``        : NOAA SWPC's aurora data (OVATION summary, Kp observed and predicted, solar wind, geomagnetic
                                     messages) and, for a position, Vigie's estimate of the coming night (ADR 0012, not SWPC's).
- ``GET    /v1/sky/aurora.geojson``: SWPC's OVATION grid as map bands (RFC 7946).
- ``GET    /v1/experimental/prealert`` : EXPERIMENTAL, UNOFFICIAL: the pre-alert levels Vigie computes for a province of Turkey
                                     (ADR 0011); 404 unless ``VIGIE_PREALERT_TR`` is on. Not an official warning.
- ``GET    /v1/geojson/hazards``   : IETF RFC 7946 GeoJSON (``FeatureCollection``): one point per hazard.
- ``GET    /v1/geojson/hazards/{id}/forecast`` : the forecast of a hazard that has one (``has_forecast``): the cone and track
                                     of a tropical cyclone, the arrival times of a tsunami, as GeoJSON.
- ``GET    /v1/fires``             : the active fires (NASA FIRMS hotspots) of a ``bbox`` and of the last hours, as GeoJSON.
- ``GET    /v1/fires/grid``        : the same detections counted per cell, for small zooms. Both are off with ``VIGIE_ENABLE_FIRMS=0``.
- ``GET    /v1/cap/alerts``        : OASIS CAP 1.2 (Common Alerting Protocol).
- ``GET    /v1/vigilances``        : Official national weather warnings (MeteoAlarm, DWD, ECCC, MetService, NWS, the Vigicrues
                                     river floods of France, and the national services of the WMO registry) for a point
                                     (``lat, lon``) or a country (``country``), and the tsunami messages of the U.S.
                                     warning centres (``source=tsunami``).
- ``GET    /v1/vigilances.geojson``: The same, as GeoJSON.
- ``GET    /v1/vigilances/countries``: The countries this instance has a source for, with the sources and whether their
                                     reuse terms were verified; with ``uncovered=true``, the other countries too, each with
                                     the reason nothing is read for it.
- ``GET    /v1/vigilances/archive``: Every official warning this instance saw, kept as it was served, filtered by country, area,
                                     source, phenomenon, level and period, with the hours each source was read.
- ``GET    /v1/vigilances/archive/stats``: What the archive took in per day and what it weighs on disk.
- ``POST   /v1/simulate``          : Inject a simulated scenario (always labelled as a simulation). Off unless
                                     ``VIGIE_ENABLE_SIMULATION=1``: it answers 403 otherwise.
- ``WS     /v1/stream``            : Real-time WebSocket stream for connected terminals.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from app import __version__
from app.models.earthquakes import EarthquakesQuery, EarthquakesResponse
from app.models.fires import FiresCollection, FiresGrid
from app.models.forecast import HazardForecast
from app.models.places import WatchedPoint, WatchedPointUpsertRequest
from app.models.prealert import PrealertResponse
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
from app.models.sky import AuroraOvalCollection, AuroraResponse
from app.models.vigilance import (
    UncoveredCountry,
    VigilanceArchiveQuery,
    VigilanceArchiveResponse,
    VigilanceArchiveStats,
    VigilanceCountry,
    VigilanceGeoJSONCollection,
    VigilancePhenomenon,
    VigilancesResponse,
)
from app.models.watch import WatchedPointsStatus
from app.routers.dashboard import router as dashboard_router
from app.security import AdminTokenMiddleware, admin_token, document_admin_token
from app.services import earthquakes as quakes
from app.services.aggregator import AggregatorService
from app.services.fires import (
    CELL_SIZES,
    CELLS_DEFAULT,
    CELLS_MAX,
    FEATURES_DEFAULT,
    FEATURES_MAX,
    NEARBY_RADIUS_DEFAULT_KM,
    NEARBY_RADIUS_MAX_KM,
    WINDOW_DEFAULT_H,
    WINDOW_MAX_H,
    FiresOff,
    FiresUnavailable,
    FiresWindowTooLong,
    parse_bbox,
)
from app.services.geoindex import DEFAULT_RIVER_RADIUS_KM, MAX_RIVER_RADIUS_KM
from app.services.scheduler import BackgroundAlertScheduler
from app.services.storage_wal import WatchedPointsLimitError
from app.services.vigilance import FOLLOW_DECIMALS, TSUNAMI_SOURCE, parse_languages

# The most positions one WebSocket connection can follow for official warnings.
MAX_FOLLOWED_POINTS_PER_CONNECTION = 10


class WebSocketHub:
    """Manager of the real-time WebSocket connections (Waveshare 7B, M5Stack Tab5, web).

    A client can also follow positions (``{"action": "follow", "lat": .., "lon": ..}``): the scheduler then pushes it the
    changes of the official warnings of those positions (`vigilance_push`). The positions live in memory, per
    connection, and are forgotten with it.
    """

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []
        self.following: dict[WebSocket, set[tuple[float, float]]] = {}

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        self.following.pop(websocket, None)

    def follow(self, websocket: WebSocket, lat: float, lon: float) -> Optional[tuple[float, float]]:
        """Follow a position (rounded to ``FOLLOW_DECIMALS`` decimals); ``None`` when the connection follows too many."""
        key = (round(lat, FOLLOW_DECIMALS), round(lon, FOLLOW_DECIMALS))
        points = self.following.setdefault(websocket, set())
        if key not in points and len(points) >= MAX_FOLLOWED_POINTS_PER_CONNECTION:
            return None
        points.add(key)
        return key

    def unfollow(self, websocket: WebSocket, lat: float, lon: float) -> None:
        self.following.get(websocket, set()).discard((round(lat, FOLLOW_DECIMALS), round(lon, FOLLOW_DECIMALS)))

    def followed_points(self) -> set[tuple[float, float]]:
        """Every position that some connection follows."""
        return {point for points in self.following.values() for point in points}

    async def send_to_followers(self, point: tuple[float, float], message: dict[str, Any]) -> None:
        """Send a message to the connections that follow ``point``."""
        stale: list[WebSocket] = []
        for conn, points in list(self.following.items()):
            if point in points:
                try:
                    await conn.send_json(message)
                except Exception:
                    stale.append(conn)
        for s in stale:
            self.disconnect(s)

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


def watched_points_write_enabled() -> bool:
    """``POST`` and ``DELETE`` of ``/v1/watched-points`` work only when ``VIGIE_ENABLE_WATCHED_POINTS_WRITE`` is ``1`` / ``true`` / ``yes`` / ``on``.

    The routes have no authentication and a watched point is personal (a home, for example), so an instance that others can
    reach keeps them off (the default) or puts a reverse proxy in front that lets nobody write. The reads always work.
    Read at each request.
    """
    return _env_flag("VIGIE_ENABLE_WATCHED_POINTS_WRITE")


def require_watched_points_write() -> None:
    """Refuse a write of the watched points (403) while ``VIGIE_ENABLE_WATCHED_POINTS_WRITE`` is not on; it runs before the body is read."""
    if not watched_points_write_enabled():
        raise HTTPException(
            status_code=403,
            detail=(
                "Writing watched points is disabled on this instance. Set VIGIE_ENABLE_WATCHED_POINTS_WRITE=1 to enable it, "
                "only on an instance that trusted clients alone can reach (the routes have no authentication)."
            ),
        )


def _make_prealert_pilot() -> Optional[Any]:
    """The unofficial pre-alert pilot for Turkey (ADR 0011 and its amendment), when ``VIGIE_PREALERT_TR`` is on (read at start)."""
    from app.prealert.shadow import enabled

    if not enabled():
        return None
    from app.prealert.shadow import EcmwfOpenDataCollector, ShadowPilot, ShadowStore

    return ShadowPilot(EcmwfOpenDataCollector(ShadowStore()))


prealert_pilot = _make_prealert_pilot()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Load the bundled fault traces, start the background scheduler when enabled and stop it cleanly on shutdown."""
    aggregator.places.faults()  # read once at the start, not by the first request that needs them
    if scheduler_enabled():
        scheduler.start()
    aggregator.fires.warm_up()  # the first read of the fires (unless the instance turned FIRMS off) starts now, not at the first visitor
    if prealert_pilot is not None:
        prealert_pilot.start()
    try:
        yield
    finally:
        await scheduler.stop()
        if prealert_pilot is not None:
            await prealert_pilot.stop()


app = FastAPI(
    title="Vigie API",
    version=__version__,
    lifespan=lifespan,
    description=(
        "Aggregates world natural hazards (USGS, EMSC, GDACS, NOAA NWS, USGS Volcano, NOAA SWPC), the official "
        "national weather warnings (MeteoAlarm, DWD, Environment and Climate Change Canada, MetService, NOAA NWS, "
        "Vigicrues) "
        "and weather (Open-Meteo, MET Norway fallback, RainViewer radar, marine, river discharge, "
        "air quality) for the Vigie displays. A value no provider supplied is reported as unknown "
        "(null, or an empty field in the embedded frame); nothing is ever made up. "
        "Standards: IETF RFC 7946 GeoJSON, OASIS CAP 1.2. "
        "The code is MIT-licensed; the data belongs to each provider under its own terms (SOURCES.md), and every "
        "block of data carries an `attribution` field with the credit its source asks for. "
        "This is not an official warning service: GDACS alerts are automatic and do not replace official warnings."
    ),
)

# The admin token is checked inside the CORS middleware (the CORS one is added after it, so it is the outermost): a 401 carries the CORS headers a browser needs to read it.
app.add_middleware(AdminTokenMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dashboard_router)

_default_openapi = app.openapi


def openapi_with_admin_token() -> dict[str, Any]:
    """The OpenAPI document, with the optional admin token documented on every write operation."""
    return document_admin_token(_default_openapi())


app.openapi = openapi_with_admin_token  # type: ignore[method-assign]


@app.exception_handler(RequestValidationError)
async def refuse_invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
    """The 422 FastAPI answers, with one change: a number JSON cannot carry (``NaN``, ``Infinity``, which Python's parser accepts)
    is written as text in the error. Without it the error itself could not be encoded and the client got a 500."""
    errors = jsonable_encoder(exc.errors(), custom_encoder={float: lambda value: value if math.isfinite(value) else str(value)})
    return JSONResponse(status_code=422, content={"detail": errors})


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
        "watched_points_count": len(aggregator.watched_points),
        "active_faults_loaded": len(faults) if (faults := aggregator.places.faults()) is not None else None,
        "watched_points_write_enabled": watched_points_write_enabled(),
        "admin_token_set": admin_token() is not None,
        "simulation_enabled": simulation_enabled(),
        "simulation_active": aggregator.simulation_active,
        "active_scenario": aggregator.active_scenario.value,
        "scheduler": scheduler.get_status(),
        "vigilance_archive": aggregator.vigilance_archive.health(),
        "disabled_sources": aggregator.disabled_sources(),
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


@app.get("/v1/watched-points", response_model=list[WatchedPoint], tags=["Watchlist"])
async def get_watched_points() -> list[WatchedPoint]:
    """List the points the instance watches. The list is not protected (like `/v1/zones`): it is public on a public instance."""
    return aggregator.watched_points


@app.post(
    "/v1/watched-points",
    response_model=WatchedPoint,
    tags=["Watchlist"],
    dependencies=[Depends(require_watched_points_write)],
    responses={
        403: {"description": "Writing watched points is off (`VIGIE_ENABLE_WATCHED_POINTS_WRITE` is not set to 1)."},
        409: {"description": "The instance already keeps the largest number of watched points (`VIGIE_WATCHED_POINTS_MAX`)."},
    },
)
async def upsert_watched_point(request: WatchedPointUpsertRequest) -> WatchedPoint:
    """Add or update a watched point: a name, a kind (`place`, `fault`, `volcano`, `custom`), a position and a radius.

    `/v1/watched-points/status` lists what happened near it. The point is personal: its name is shown by these two routes
    only, never in the hazards. **Off unless `VIGIE_ENABLE_WATCHED_POINTS_WRITE=1`** (403 otherwise). The route has no
    authentication: on an instance that others can reach, keep it off or block `POST` and `DELETE` of `/v1/watched-points`
    in a reverse proxy, like `/v1/zones` and `/v1/simulate`.
    """
    try:
        return aggregator.upsert_watched_point(request)
    except WatchedPointsLimitError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.delete(
    "/v1/watched-points/{point_id}",
    tags=["Watchlist"],
    dependencies=[Depends(require_watched_points_write)],
    responses={403: {"description": "Writing watched points is off (`VIGIE_ENABLE_WATCHED_POINTS_WRITE` is not set to 1)."}},
)
async def delete_watched_point(point_id: str) -> dict[str, Any]:
    """Delete a watched point. Same switch as the `POST`: off unless `VIGIE_ENABLE_WATCHED_POINTS_WRITE=1`."""
    if not aggregator.delete_watched_point(point_id):
        raise HTTPException(status_code=404, detail=f"Watched point '{point_id}' not found")
    return {"status": "deleted", "point_id": point_id}


@app.get(
    "/v1/watched-points/status",
    response_model=WatchedPointsStatus,
    tags=["Watchlist"],
    description=(
        "For each watched point, the events within its radius in the last `days` days (7 by default), taken from the hazards the "
        "API saw (an earthquake by the time it happened, any other hazard while it was active). A point with no event is "
        "`quiet` only when **every hazard source answered at the last reading and has been read without a gap** since "
        "`quiet_since_utc`; otherwise it is `unknown` and `unreachable_sources` says which source did not answer. The API only "
        "knows what it saw: an instance started an hour ago cannot say nothing happened in the last week. The state is "
        "computed by Vigie, not reported by a source, and is not an official warning."
    ),
)
async def get_watched_points_status(
    days: int = Query(default=7, ge=1, le=30, description="Length of the window the events are looked for in, in days"),
) -> WatchedPointsStatus:
    """Events near each watched point, and whether the API may say nothing happened."""
    return await aggregator.watched_points_status(days)


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
    compact: bool = Query(default=True, description="Compact profile (under 16 KiB)"),
    fire_radius_km: float = Query(
        default=NEARBY_RADIUS_DEFAULT_KM,
        gt=0.0,
        le=NEARBY_RADIUS_MAX_KM,
        description="Radius (km) of the `fires_nearby` summary of active fires (NASA FIRMS; null when the instance turned the source off)",
    ),
) -> PointResponse:
    """Weather at one position (15-minute rain, marine, river discharge, air, space weather), nearby hazards and the fires detected nearby."""
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
        fire_radius_km=fire_radius_km,
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
    compact: bool = Query(default=True, description="Compact profile: keep the payload under 16 KiB (5 days per zone, credits included)"),
) -> SnapshotResponse:
    """Return the full multi-zone snapshot plus the de-duplicated world hazards (compact: under 16 KiB)."""
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


@app.get(
    "/v1/earthquakes",
    response_model=EarthquakesResponse,
    tags=["Embedded & GPS"],
    description=(
        "The earthquakes the seismic sources reported (USGS and EMSC, one event when both report it) within `radius_km` of a "
        "position over the last `hours` hours, the strongest first. The feeds keep a short window (USGS the last day, EMSC its "
        "last 40 events) and list nothing below magnitude 4.5 (`feeds_min_magnitude`); an earthquake they no longer list comes "
        "from the history the instance kept (`listed_now` false), so an instance started recently knows less. `sources` says "
        "whether each source answered and since when it has been read without a gap. No weather provider is asked: this route "
        "costs no call per client. A magnitude is not an official warning."
    ),
)
async def get_earthquakes(
    lat: float = Query(..., ge=-90.0, le=90.0, description="Latitude of the position (WGS84)"),
    lon: float = Query(..., ge=-180.0, le=180.0, description="Longitude of the position (WGS84)"),
    radius_km: float = Query(
        default=quakes.DEFAULT_RADIUS_KM, gt=0.0, le=quakes.MAX_RADIUS_KM, description="Radius around the position (km)"
    ),
    min_magnitude: float = Query(
        default=quakes.FEEDS_MIN_MAGNITUDE, ge=0.0, le=10.0, description="Keep the earthquakes of at least this magnitude"
    ),
    hours: float = Query(
        default=quakes.DEFAULT_HOURS, gt=0.0, le=quakes.MAX_HOURS, description="Earthquakes of the last `hours` hours"
    ),
    limit: int = Query(default=quakes.DEFAULT_LIMIT, ge=1, le=quakes.MAX_LIMIT, description="Most earthquakes returned"),
) -> EarthquakesResponse:
    """The earthquakes near a position, strongest first."""
    query = EarthquakesQuery(lat=round(lat, 4), lon=round(lon, 4), radius_km=radius_km, min_magnitude=min_magnitude, hours=hours)
    return await aggregator.earthquakes_near(query, limit)


@app.get(
    "/v1/sky/aurora",
    response_model=AuroraResponse,
    tags=["Sky"],
    description=(
        "The aurora. **SWPC's data as SWPC gives it**: the latest OVATION forecast (30 to 90 minutes ahead; its map is "
        "`/v1/sky/aurora.geojson`), the planetary Kp by 3-hour interval (observed, estimated, predicted for three days), the solar "
        "wind at L1 and SWPC's geomagnetic messages of the last 72 hours, their text untouched. With `lat` and `lon`, for that place: "
        "what OVATION says within 1,000 km, and **Vigie's estimate** of the chance to see an aurora hour by hour over the coming "
        "night and its best window (`place.estimate`). The estimate is ours, not SWPC's and not an official forecast: the magnetic "
        "latitude, the Kp, the darkness, the Moon and the forecast clouds, combined by the method of ADR 0012 and measured on past "
        "nights against the reports of people who looked (with the Kp observed afterwards, without the clouds). The clouds come from "
        "the forecast request a place already costs; SWPC's products are read once for the whole instance."
    ),
)
async def get_sky_aurora(
    lat: Optional[float] = Query(default=None, ge=-90.0, le=90.0, description="Latitude of the place (WGS84); with `lon`"),
    lon: Optional[float] = Query(default=None, ge=-180.0, le=180.0, description="Longitude of the place (WGS84); with `lat`"),
) -> AuroraResponse:
    """SWPC's aurora data and, for a place, the estimate of the coming night."""
    if (lat is None) != (lon is None):
        raise HTTPException(status_code=422, detail="Give both lat and lon, or neither.")
    if lat is None or lon is None:
        return await aggregator.aurora.aurora()
    return await aggregator.aurora.aurora(round(lat, 4), round(lon, 4))


@app.get(
    "/v1/sky/aurora.geojson",
    response_model=AuroraOvalCollection,
    tags=["Sky"],
    description=(
        "SWPC's OVATION aurora forecast (30 to 90 minutes ahead, both hemispheres) as RFC 7946 bands: each feature holds the grid "
        "cells whose value is in `[min, max)`. The values are SWPC's (an intensity, from which SWPC says a viewing probability in % "
        "can be derived linearly); the levels of the bands are ours. Empty when SWPC could not be read and nothing is cached."
    ),
)
async def get_sky_aurora_geojson() -> AuroraOvalCollection:
    """The OVATION grid as map bands."""
    return await aggregator.aurora.oval_geojson()


@app.get(
    "/v1/experimental/prealert",
    response_model=PrealertResponse,
    tags=["Experimental"],
    description=(
        "**Experimental and unofficial**, for testers (ADR 0011 and its amendment): the pre-alert levels Vigie computes for the "
        "province of Turkey a position is in, from the ECMWF forecast and thresholds of its own, for today and the next days. "
        "**Not an official warning**: the official warnings of Turkey are the MGM's (`official_warnings_url`). Answers 404 on an "
        "instance that does not run the pilot (`VIGIE_PREALERT_TR`) and 422 outside Turkey. It is in no other answer."
    ),
)
async def get_experimental_prealert(
    lat: float = Query(..., ge=-90.0, le=90.0, description="Latitude of the position (WGS84)"),
    lon: float = Query(..., ge=-180.0, le=180.0, description="Longitude of the position (WGS84)"),
) -> PrealertResponse:
    """The experimental pre-alert of the province of Turkey at a position."""
    if prealert_pilot is None:
        raise HTTPException(status_code=404, detail="This instance does not run the experimental pre-alert (VIGIE_PREALERT_TR).")
    from app.prealert.view import OutsideTurkey, prealert_at

    try:
        return await asyncio.to_thread(prealert_at, prealert_pilot.store, lat, lon)
    except OutsideTurkey as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/v1/geojson/hazards", response_model=GeoJSONFeatureCollection, tags=["Standards"])
async def get_geojson_hazards() -> GeoJSONFeatureCollection:
    """Export every active hazard as IETF RFC 7946 GeoJSON."""
    return await aggregator.build_geojson_feature_collection()


@app.get(
    "/v1/geojson/hazards/{hazard_id}/forecast",
    response_model=HazardForecast,
    tags=["Standards"],
    responses={404: {"description": "No active hazard has this id (it may have ended)."}},
)
async def get_hazard_forecast(hazard_id: str) -> HazardForecast:
    """The forecast of a hazard of `/v1/geojson/hazards` whose `has_forecast` is true, as an RFC 7946 FeatureCollection.

    A tropical cyclone of the National Hurricane Center: `kind` `cone` (Polygon or MultiPolygon), `track` (LineString), `position`
    (Point: `valid_utc`, `wind_kt`, `pressure_hpa`...) and `watch_warning` (LineString). A tsunami of the U.S. warning centres:
    `epicentre` and `eta` (Point with `eta_utc`, the estimated arrival time at a place). The collection says who issued it
    (`source`, `issued_utc`, `advisory`, `advisory_url`) and the credit to show (`attribution`). A hazard with no forecast
    answers an empty collection and says why in `reason`; nothing is drawn that the source did not give.
    """
    forecast = await aggregator.build_hazard_forecast(hazard_id)
    if forecast is None:
        raise HTTPException(status_code=404, detail=f"No active hazard '{hazard_id}'")
    return forecast


CELL_SIZES_TEXT = ", ".join(f"{size:g}" for size in CELL_SIZES)
FIRES_DESCRIPTION = (
    "Active fires seen by the VIIRS instruments of NOAA-20 and NOAA-21, from NASA FIRMS (the near real-time product, 375 m pixels). "
    "A detection is a satellite pixel that looks like a fire: it is not an alert and not a confirmed fire, and no detection is not the "
    "absence of fire (a satellite passes a place a few times a day, clouds hide the ground, small fires are missed). The data is "
    "published about 3 hours after the observation (the ultra real-time detections of the United States and Canada are not in the files "
    "this API reads). The instance reads the static files of FIRMS, which need no key, and can go back as far as the file it reads (24 or "
    "48 hours: asking for more is a 422); an instance that turned the source off (`VIGIE_ENABLE_FIRMS=0`) answers 404 and `GET /v1/health` "
    "lists the source in `disabled_sources`. "
    "`bbox` is `west,south,east,north` in degrees; a west greater than the east crosses the antimeridian, and a span of 360 degrees or "
    "more is the whole globe. The credit (`attribution`) and the disclaimer NASA asks for are in the metadata of every answer."
)
FIRES_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"description": "This instance turned the FIRMS source off (`VIGIE_ENABLE_FIRMS=0`)."},
    422: {"description": "The box or a parameter is not valid, or `hours` is beyond the window of the files this instance reads."},
    503: {"description": "FIRMS has not been read yet and could not be read now."},
}
FIRES_HEADERS = {"Cache-Control": "public, max-age=60"}
FIRMS_UNAVAILABLE = "NASA FIRMS has not been read yet and could not be read now; try again later"


def _fires_box(bbox: str) -> Any:
    try:
        return parse_bbox(bbox)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@app.get(
    "/v1/fires",
    response_model=None,
    tags=["Fires"],
    description=FIRES_DESCRIPTION
    + " Each feature is one detection with the fields of the FIRMS file; at most `limit` are returned, the strongest (highest fire radiative "
    "power) first, and `metadata.truncated` says when there are more: ask a smaller box, or `GET /v1/fires/grid` at small zooms.",
    responses={200: {"model": FiresCollection, "description": "A GeoJSON FeatureCollection of detections"}, **FIRES_ERRORS},
)
async def get_fires(
    bbox: str = Query(..., description="`west,south,east,north` in degrees, e.g. `-6,35,10,45`"),
    hours: float = Query(default=WINDOW_DEFAULT_H, gt=0.0, le=WINDOW_MAX_H, description="Detections acquired in the last `hours` hours (the start rounded down to 5 minutes)"),
    min_confidence: Literal["low", "nominal", "high"] = Query(default="low", description="Keep the detections of this FIRMS confidence class or above (`low` keeps all)"),
    min_frp: float = Query(default=0.0, ge=0.0, description="Keep the detections whose fire radiative power is at least this (megawatts); one with no power does not pass"),
    limit: int = Query(default=FEATURES_DEFAULT, ge=1, le=FEATURES_MAX, description="Most features returned"),
) -> JSONResponse:
    """The active fires of a box, as GeoJSON points."""
    box = _fires_box(bbox)
    try:
        content = await aggregator.fires.features(box, hours, min_confidence, min_frp, limit)
    except FiresOff as exc:
        raise HTTPException(status_code=404, detail=exc.reason) from None
    except FiresWindowTooLong as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except FiresUnavailable:
        raise HTTPException(status_code=503, detail=FIRMS_UNAVAILABLE) from None
    return JSONResponse(content, headers=FIRES_HEADERS)


@app.get(
    "/v1/fires/grid",
    response_model=None,
    tags=["Fires"],
    description=FIRES_DESCRIPTION
    + f" The detections are counted per cell of `cell_deg` degrees ({CELL_SIZES_TEXT}), aligned on multiples "
    "of it from -90 and -180; each feature is the centre of a cell with its `count`, `max_frp_mw` and `latest_utc`. At most `limit` cells "
    "are returned, the busiest first. Use it at small zooms, and `GET /v1/fires` once a box holds few enough detections.",
    responses={200: {"model": FiresGrid, "description": "A GeoJSON FeatureCollection of cell centres"}, **FIRES_ERRORS},
)
async def get_fires_grid(
    bbox: str = Query(default="-180,-90,180,90", description="`west,south,east,north` in degrees; the whole globe by default"),
    cell_deg: float = Query(default=1.0, description=f"Side of a cell in degrees: one of {CELL_SIZES_TEXT}"),
    hours: float = Query(default=WINDOW_DEFAULT_H, gt=0.0, le=WINDOW_MAX_H, description="Detections acquired in the last `hours` hours (the start rounded down to 5 minutes)"),
    min_confidence: Literal["low", "nominal", "high"] = Query(default="low", description="Keep the detections of this FIRMS confidence class or above (`low` keeps all)"),
    min_frp: float = Query(default=0.0, ge=0.0, description="Keep the detections whose fire radiative power is at least this (megawatts)"),
    limit: int = Query(default=CELLS_DEFAULT, ge=1, le=CELLS_MAX, description="Most cells returned"),
) -> JSONResponse:
    """The active fires counted per cell, as GeoJSON points."""
    if cell_deg not in CELL_SIZES:
        raise HTTPException(status_code=422, detail=f"cell_deg must be one of {CELL_SIZES_TEXT}")
    box = _fires_box(bbox)
    try:
        content = await aggregator.fires.grid(box, hours, cell_deg, min_confidence, min_frp, limit)
    except FiresOff as exc:
        raise HTTPException(status_code=404, detail=exc.reason) from None
    except FiresWindowTooLong as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except FiresUnavailable:
        raise HTTPException(status_code=503, detail=FIRMS_UNAVAILABLE) from None
    return JSONResponse(content, headers=FIRES_HEADERS)


@app.get("/v1/cap/alerts", response_model=CAPFeedResponse, tags=["Standards"])
async def get_cap_alerts() -> CAPFeedResponse:
    """Export every active alert as OASIS CAP 1.2 (Common Alerting Protocol)."""
    return await aggregator.build_cap_feed()


VIGILANCE_DESCRIPTION = (
    "Ask either for a position (`lat` and `lon`: the warnings whose area contains that exact point) or for a country "
    "(`country`, ISO 3166-1 alpha-2: every warning of that country). Sources: the MeteoAlarm feeds of 39 European "
    "countries, the Deutscher Wetterdienst for Germany (preferred to MeteoAlarm's copies), Environment and Climate "
    "Change Canada, MetService for New Zealand, the Severe and Extreme alerts of the US National Weather Service, "
    "Vigicrues for the river floods of France, and the national services of the WMO Severe Weather Information Centre "
    "registry for the other countries (`GET /v1/vigilances/countries` lists what this instance reads; which services it reads "
    "depends on `VIGIE_WMO_SOURCES`). `source=tsunami` (alone) lists the tsunami warnings, watches, advisories and "
    "threat messages in force of the U.S. Tsunami Warning Centers; they name coasts by zone codes or countries, never by a "
    "shape, and `country=US` or `CA` lists them with that country's warnings. A river section is a line: for a position it counts when it lies within "
    "`river_radius_km` of it, and `distance_km` says how far. The words of the issuer are "
    "kept untouched in `texts` (one entry per language; `lang=fr,en` keeps only those languages); `level` and "
    "`phenomenon` are the API's own normalization and are listed in `derived`. `coverage` says what the API could "
    "not do: a warning whose area is given by a code without a shape on this instance cannot be matched to a point. "
    "The sources' terms ask for `disclaimer` to be published with the warnings. To be told when a warning appears, "
    "goes up or ends for a position, send `{\"action\": \"follow\", \"lat\": .., \"lon\": ..}` on the WebSocket "
    "`/v1/stream` (you get a `vigilance_state` at once, then a `vigilance_push` for each change; "
    "`{\"action\": \"unfollow\", ..}` stops it). This is not an official warning "
    "service: follow the warnings of your national authorities."
)


def _vigilance_request(lat: Optional[float], lon: Optional[float], country: Optional[str], source: Optional[str] = None) -> None:
    """Check that the query is a position, a country or a source, not several and not half a position."""
    if source is not None:
        if lat is not None or lon is not None or country is not None:
            raise HTTPException(status_code=422, detail="`source` is asked alone, without a position or a `country`")
        return
    if (lat is None) != (lon is None):
        raise HTTPException(status_code=422, detail="`lat` and `lon` go together")
    if lat is not None and country is not None:
        raise HTTPException(status_code=422, detail="Ask for a position (`lat`, `lon`) or for a `country`, not both")
    if lat is None and country is None:
        raise HTTPException(status_code=422, detail="Ask for a position (`lat` and `lon`), a `country` or `source=tsunami`")
    if country is not None and country.upper() not in aggregator.vigilances.supported_countries():
        why = aggregator.vigilances.uncovered_reason(country.upper())
        raise HTTPException(
            status_code=404,
            detail=(
                f"No official warning source for country '{country.upper()}'"
                + (f" ({why.name}): {why.reason}" if why is not None else "")
                + " on this instance. Countries with a source: GET /v1/vigilances/countries"
            ),
        )


async def _vigilances(
    lat: Optional[float],
    lon: Optional[float],
    country: Optional[str],
    lang: Optional[str],
    river_radius_km: float = DEFAULT_RIVER_RADIUS_KM,
    source: Optional[str] = None,
) -> VigilancesResponse:
    _vigilance_request(lat, lon, country, source)
    languages = parse_languages(lang)
    if source is not None:
        return await aggregator.vigilances.for_tsunami()
    if country is not None:
        return await aggregator.vigilances.for_country(country, languages)
    assert lat is not None and lon is not None
    return await aggregator.vigilances.for_point(lat, lon, languages, river_radius_km)


@app.get(
    "/v1/vigilances",
    response_model=VigilancesResponse,
    tags=["Vigilances"],
    description=VIGILANCE_DESCRIPTION,
    responses={
        404: {"description": "No source of official warnings for this country."},
        422: {"description": "Neither a position nor a country, or both."},
    },
)
async def get_vigilances(
    lat: Optional[float] = Query(default=None, ge=-90.0, le=90.0, description="Exact latitude (WGS84): the warnings covering that point"),
    lon: Optional[float] = Query(default=None, ge=-180.0, le=180.0, description="Exact longitude (WGS84)"),
    country: Optional[str] = Query(
        default=None, min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$", description="ISO 3166-1 alpha-2 country: every warning of that country"
    ),
    lang: Optional[str] = Query(
        default=None, max_length=64, description="Keep only the texts in these languages, e.g. `fr,en` (a prefix of the language tag). All by default"
    ),
    river_radius_km: float = Query(
        default=DEFAULT_RIVER_RADIUS_KM,
        gt=0.0,
        le=MAX_RIVER_RADIUS_KM,
        description="For a position: how far from it a river section (Vigicrues, France) still counts, in kilometres",
    ),
    source: Optional[str] = Query(
        default=None,
        pattern=f"^{TSUNAMI_SOURCE}$",
        description="`tsunami`: every tsunami message in force of the U.S. Tsunami Warning Centers (asked alone)",
    ),
) -> VigilancesResponse:
    """Official weather warnings for a point or a country (MeteoAlarm, DWD, ECCC, MetService, NWS, Vigicrues), or the tsunami messages."""
    return await _vigilances(lat, lon, country, lang, river_radius_km, source)


@app.get(
    "/v1/vigilances.geojson",
    response_model=VigilanceGeoJSONCollection,
    tags=["Vigilances"],
    description=VIGILANCE_DESCRIPTION
    + " Each feature is a warning; its geometry is the polygon of the source, or the shape of its code, or null.",
    responses={
        404: {"description": "No source of official warnings for this country."},
        422: {"description": "Neither a position nor a country, or both."},
    },
)
async def get_vigilances_geojson(
    lat: Optional[float] = Query(default=None, ge=-90.0, le=90.0, description="Exact latitude (WGS84)"),
    lon: Optional[float] = Query(default=None, ge=-180.0, le=180.0, description="Exact longitude (WGS84)"),
    country: Optional[str] = Query(default=None, min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$", description="ISO 3166-1 alpha-2 country"),
    lang: Optional[str] = Query(default=None, max_length=64, description="Keep only the texts in these languages, e.g. `fr,en`"),
    river_radius_km: float = Query(
        default=DEFAULT_RIVER_RADIUS_KM, gt=0.0, le=MAX_RIVER_RADIUS_KM, description="For a position: the radius for a river section (Vigicrues), in kilometres"
    ),
    source: Optional[str] = Query(default=None, pattern=f"^{TSUNAMI_SOURCE}$", description="`tsunami`: the tsunami messages in force (asked alone)"),
) -> VigilanceGeoJSONCollection:
    """The same warnings as GeoJSON (RFC 7946)."""
    return aggregator.vigilances.to_geojson(await _vigilances(lat, lon, country, lang, river_radius_km, source))


@app.get(
    "/v1/vigilances/countries",
    response_model=list[VigilanceCountry | UncoveredCountry],
    tags=["Vigilances"],
    description=(
        "The countries this instance has a source of official warnings for, with the settings it runs with: by default the "
        "MeteoAlarm countries, Germany, Canada, New Zealand (unless turned off), the United States and the services of the WMO "
        "registry whose reuse terms were verified; with `VIGIE_WMO_SOURCES=all` every service of the registry that is not excluded. "
        "`sources` names what is asked for the country (the id of a registry service is the one of `coverage[].source_id`); "
        "`licence_verified` is true when the reuse terms of every one of them were read and allow relaying its warnings (SOURCES.md). "
        "With `uncovered=true` the list holds every other country of the world too, sorted with the rest: an entry without a "
        "source has `covered: false`, its `name` and the reason nothing is read for it (`reason_code`, a stable code to translate, "
        "and `reason`, one English sentence). The entries of the countries with a source do not change."
    ),
)
async def get_vigilance_countries(
    uncovered: bool = Query(default=False, description="Also list the countries this instance reads no source for, each with the reason"),
) -> list[VigilanceCountry | UncoveredCountry]:
    """The countries with a source of official warnings on this instance (and, on request, the ones without, with the reason)."""
    await aggregator.vigilances.ensure_registry()
    return aggregator.vigilances.countries_listing(with_uncovered=uncovered)


ARCHIVE_SOURCE_PATTERN = r"^(meteoalarm|noaa_nws|dwd|eccc|metservice|vigicrues|tsunami|swic(:[A-Za-z0-9_.-]{1,64})?)$"


@app.get(
    "/v1/vigilances/archive",
    response_model=VigilanceArchiveResponse,
    tags=["Vigilances"],
    description=(
        "Every official warning this instance saw in a source's answer, in force or not (a cancellation, a warning past its end), kept "
        "as `GET /v1/vigilances` served it: the words of the issuer untouched in every language, `level` and `phenomenon` the API's own "
        "(listed in `derived`). One item per message and per area; `first_seen_utc` and `last_seen_utc` say when the instance saw it "
        "(the last to within ten minutes). The newest first seen come first. `since` and `until` keep the warnings whose period "
        "(onset, else effective time or time of issue, to the later of end and expiry) overlaps them. A source is read only for a "
        "country that is needed or asked for: `reads` says in which UTC hours of each day each source was read, so that a period "
        "without a warning is not taken for a period without danger when nobody read the source. A MeteoAlarm warning has its words "
        "when the instance had read its CAP document (the archive sends no request of its own). How long a warning is kept: "
        "`keep_days` (`VIGIE_ARCHIVE_KEEP_DAYS`, null: for ever). This is not an official warning service."
    ),
)
async def get_vigilance_archive(
    country: Optional[str] = Query(default=None, min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$", description="ISO 3166-1 alpha-2 country"),
    area: Optional[str] = Query(
        default=None, min_length=2, max_length=120, description="A code of the area (`NUTS3:FR101`, `FR101`, `EMMA_ID:FR075`) or a part of its name"
    ),
    source: Optional[str] = Query(
        default=None, pattern=ARCHIVE_SOURCE_PATTERN, description="The source: `meteoalarm`, `dwd`, `eccc`, `metservice`, `noaa_nws`, `vigicrues`, `tsunami`, `swic` or `swic:<service>`"
    ),
    phenomenon: Optional[VigilancePhenomenon] = Query(default=None, description="The phenomenon (the API's own, `derived`)"),
    level: Optional[SeverityLevel] = Query(default=None, description="Exactly this level (the API's own, `derived`)"),
    min_level: Optional[SeverityLevel] = Query(default=None, description="At least this level"),
    since: Optional[int] = Query(default=None, ge=0, description="Warnings whose period ends at or after this time (UTC epoch seconds)"),
    until: Optional[int] = Query(default=None, ge=0, description="Warnings whose period starts at or before this time (UTC epoch seconds)"),
    limit: int = Query(default=100, ge=1, le=500, description="Most warnings in the page"),
    offset: int = Query(default=0, ge=0, le=1_000_000, description="Warnings to skip (the next page starts at `offset + limit`)"),
) -> VigilanceArchiveResponse:
    """The official warnings this instance saw, filtered, and when each source was read."""
    if since is not None and until is not None and until < since:
        raise HTTPException(status_code=422, detail="`until` comes before `since`")
    query = VigilanceArchiveQuery(
        country=country.upper() if country else None,
        area=area.strip() if area else None,
        source=source,
        phenomenon=phenomenon,
        level=level,
        min_level=min_level,
        since_utc=since,
        until_utc=until,
        limit=limit,
        offset=offset,
    )
    return await aggregator.vigilance_archive.query(query)


@app.get(
    "/v1/vigilances/archive/stats",
    response_model=VigilanceArchiveStats,
    tags=["Vigilances"],
    description=(
        "What the archive of the official warnings took in per UTC day (warnings first seen, what they weigh compressed, the sources "
        "and countries read) and what the database weighs on disk."
    ),
)
async def get_vigilance_archive_stats(
    days: int = Query(default=30, ge=1, le=3660, description="The last `days` days"),
) -> VigilanceArchiveStats:
    """The size of the archive of the official warnings, per day."""
    return await aggregator.vigilance_archive.stats(days)


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


async def _handle_follow(websocket: WebSocket, text: str) -> None:
    """``{"action": "follow" | "unfollow", "lat": .., "lon": ..}``: follow the official warnings of a position.

    The client gets the warnings now (`vigilance_state`), then the scheduler (`VIGIE_SCHEDULER=1`) pushes what
    changes there: a warning that appears, whose level goes up, or that ends (`vigilance_push`). Any other text is ignored.
    """
    try:
        request = json.loads(text)
    except ValueError:
        return
    if not isinstance(request, dict) or request.get("action") not in ("follow", "unfollow"):
        return
    lat, lon = request.get("lat"), request.get("lon")
    valid = (
        isinstance(lat, (int, float))
        and isinstance(lon, (int, float))
        and not isinstance(lat, bool)
        and not isinstance(lon, bool)
        and -90.0 <= lat <= 90.0
        and -180.0 <= lon <= 180.0
    )
    if not valid:
        await websocket.send_json({"event": "error", "detail": "`lat` and `lon` must be numbers within the globe"})
        return
    if request["action"] == "unfollow":
        ws_hub.unfollow(websocket, float(lat), float(lon))
        await websocket.send_json({"event": "vigilance_unfollowed", "lat": lat, "lon": lon})
        return
    followed = ws_hub.follow(websocket, float(lat), float(lon))
    if followed is None:
        await websocket.send_json(
            {"event": "error", "detail": f"A connection follows {MAX_FOLLOWED_POINTS_PER_CONNECTION} positions at most"}
        )
        return
    try:
        state = await aggregator.vigilances.for_point(followed[0], followed[1])
    except Exception:  # the position stays followed: the scheduler will push what changes
        await websocket.send_json({"event": "error", "detail": "The warnings of this position could not be read now"})
        return
    await websocket.send_json({"event": "vigilance_state", "point": {"lat": followed[0], "lon": followed[1]}, **state.model_dump(mode="json")})


@app.websocket("/v1/stream")
async def websocket_stream(websocket: WebSocket) -> None:
    """Real-time WebSocket stream: the initial state, then alert updates.

    Besides the hazards (`alert_push`), a client can follow the official warnings of positions: see `_handle_follow`.
    """
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
            else:
                await _handle_follow(websocket, msg)
    except WebSocketDisconnect:
        ws_hub.disconnect(websocket)
    except Exception:
        ws_hub.disconnect(websocket)
