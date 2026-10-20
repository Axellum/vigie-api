"""Day-long simulation of many clients against a simulated upstream (no network, no real sleeping).

The simulation counts the HTTP requests the application would really send to each provider:

- The upstream is an ``httpx.MockTransport``: every request is answered from Python and counted
  per source. The Open-Meteo weight of each request is computed here, independently of the
  application, from the published rule (see ``open_meteo_weight``).
- Time is a controllable clock (``SimClock``, installed by the ``sim_clock`` fixture): the whole day
  is played in a few seconds, with no real sleeping.
- The scheduler of the application runs with its default period and its default watched zones;
  its cycle is the body of its loop, run at each simulated period.
- The clients are display frames and web pages polling at random phases.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import math
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import httpx

from app.collectors.disasters import GDACSCollector, NOAANWSCollector
from app.collectors.dwd import DWDCollector
from app.collectors.eccc import ECCCCollector
from app.collectors.flood_air import (
    OpenMeteoAirQualityCollector,
    OpenMeteoFloodCollector,
    USGSVolcanoCollector,
)
from app.collectors.marine import OpenMeteoMarineCollector
from app.collectors.meteoalarm import MeteoAlarmCapCollector, MeteoAlarmCollector
from app.collectors.metservice import MetServiceCollector
from app.collectors.nhc import NHCCollector
from app.collectors.seismic import EMSCCollector, USGSCollector
from app.collectors.space_weather import NOAASWPCCollector
from app.collectors.tsunami import TsunamiCollector
from app.collectors.vigicrues import VigicruesCollector
from app.collectors.weather import MetNorwayCollector, OpenMeteoCollector, RainViewerCollector
from app.models.schemas import DeviceType, MonitoredZoneConfig
from app.services.aggregator import AggregatorService
from app.services.scheduler import BackgroundAlertScheduler
from app.services.storage_wal import DEFAULT_WATCHLIST_SEED
from tests import samples, storm_samples
from tests.helpers import SimClock, http_date

DAY_S = 86_400
SCHEDULER_PERIOD_S = 90

# Host of the upstream request -> provider id of the collector that sends it.
HOST_TO_SOURCE: dict[str, str] = {
    "api.open-meteo.com": "open_meteo_hd",
    "air-quality-api.open-meteo.com": "open_meteo_aqi",
    "flood-api.open-meteo.com": "open_meteo_flood",
    "marine-api.open-meteo.com": "open_meteo_marine",
    "api.met.no": "met_norway_fallback",
    "api.rainviewer.com": "rainviewer_radar",
    "earthquake.usgs.gov": "usgs_eq",
    "www.seismicportal.eu": "emsc_seismic",
    "www.gdacs.org": "gdacs_global",
    "api.weather.gov": "noaa_nws",
    "volcanoes.usgs.gov": "usgs_volcano_gvp",
    "services.swpc.noaa.gov": "noaa_swpc",
    "opendata.dwd.de": "dwd_cap",
    "api.weather.gc.ca": "eccc_alerts",
    "alerts.metservice.com": "metservice_cap",
    "www.vigicrues.gouv.fr": "vigicrues_flow",
    "www.tsunami.gov": "tsunami_warning_centers",
    "www.nhc.noaa.gov": "nhc_storms",
    # feeds.meteoalarm.org serves the Atom feeds and the CAP documents: see ``source_of``.
}

FIXTURES = Path(__file__).resolve().parent / "fixtures"
METEOALARM_FIXTURES = FIXTURES / "meteoalarm"

# The answers of the national services, recorded on 2026-10-20 (see the README of each folder). The simulated DWD and
# MetService send the validators they sent then, and answer 304 to the request that repeats them; ECCC and the Vigicrues
# flow send none, as the real ones.
#
# Vigicrues: the whole flow weighs 2,245,691 bytes on the wire (measured 2026-10-20; no compression), the recorded copy
# keeps 31 of the 337 sections, so the bytes of a flow are counted at the real weight. The national map image, whose
# head the collector asks, carries the validators of the publication; the flow is published at 07:55:15 and 13:55:15 UTC
# (10 a.m. and 4 p.m. in Paris in summer), the image regenerated 21 seconds later.
VIGICRUES_FLOW_BYTES = 2_245_691
VIGICRUES_HEAD_BYTES = len((FIXTURES / "vigicrues" / "img_france.headers.txt").read_bytes())
VIGICRUES_IMAGE_PATH = "/ftp/img_france.png"
VIGICRUES_PUBLICATION_HOURS_UTC = (7 * 3600 + 55 * 60 + 15, 13 * 3600 + 55 * 60 + 15)
DWD_ETAG = '"6ad75578-4005"'
DWD_LAST_MODIFIED = "Tue, 20 Oct 2026 11:50:16 GMT"
METSERVICE_LAST_MODIFIED = "Tue, 20 Oct 2026 11:55:21 GMT"

# The tsunami warning centres and the hurricane center, recorded on 2026-10-20 (see the README of tests/fixtures/tsunami and of
# tests/fixtures/nhc). tsunami.gov compresses everything it sends (the list: 16,140 bytes raw, 1,721 on the wire) and answers
# ``304`` to the list when its ``Last-Modified`` has not changed (239 bytes of head); the list is replayed as it was at each
# moment, from the recorded copy: an entry is there once its ``createdDB`` time has passed, and the list is modified 3 seconds
# after (10:45:23 -> 10:45:26 on 2026-10-20). The NHC answers ``304`` to ``CurrentStorms.json`` when its ETag is sent back (484
# bytes of head); that file is not compressed.
TSUNAMI_304_HEAD_BYTES = 239
NHC_304_HEAD_BYTES = 484

OPEN_METEO_SOURCES = ("open_meteo_hd", "open_meteo_aqi", "open_meteo_flood", "open_meteo_marine")

# Defaults of the Open-Meteo API when a request does not name the parameter.
_DEFAULT_FORECAST_DAYS = {"open_meteo_hd": 7, "open_meteo_aqi": 5, "open_meteo_flood": 92, "open_meteo_marine": 7}


def open_meteo_weight(source: str, params: dict[str, str]) -> float:
    """Weight of one Open-Meteo request, from the published rule.

    Rule (https://open-meteo.com/en/pricing, https://openmeteo.substack.com/p/weather-data-for-multiple-locations):
    ``weight = locations * max(1, variables / 10) * max(1, days / 14)``, the variables being the sum of
    the hourly, daily, current and 15-minute variables. This is a second, independent implementation: the
    application counts the same way, and the test compares both.
    """
    variables = 0
    for block in ("hourly", "daily", "current", "minutely_15"):
        value = params.get(block, "")
        variables += len([v for v in value.split(",") if v])
    days = int(params.get("forecast_days", _DEFAULT_FORECAST_DAYS[source])) + int(params.get("past_days", 0))
    locations = len(params.get("latitude", "").split(","))
    return locations * max(1.0, variables / 10.0) * max(1.0, days / 14.0)


def source_of(request: httpx.Request) -> Optional[str]:
    """The provider id a simulated request is for (MeteoAlarm has two: its feeds and its CAP documents)."""
    if request.url.host == "feeds.meteoalarm.org":
        return "meteoalarm_cap" if request.url.path.startswith("/api/v1/warnings/") else "meteoalarm_atom"
    return HOST_TO_SOURCE.get(request.url.host)


class Upstream:
    """Simulated providers behind an ``httpx.MockTransport``; counts every request it receives."""

    def __init__(self, clock: SimClock, latency_ticks: int = 3) -> None:
        self.clock = clock
        self.latency_ticks = latency_ticks
        self.calls: Counter[str] = Counter()
        self.weight: Counter[str] = Counter()
        self.per_hour: Counter[int] = Counter()  # Open-Meteo weight per hour
        self.per_minute: Counter[int] = Counter()  # Open-Meteo weight per minute
        self.requests: list[tuple[str, str]] = []  # (source, url) when ``record_urls`` is set
        self.record_urls = False
        # What the national services answer, when a test wants something other than the recordings of 2026-10-20.
        self.bodies: dict[str, bytes] = {}
        self.not_modified: Counter[str] = Counter()  # requests answered 304 (they are in ``calls`` too)
        # Bytes the providers send back (the body; plus the head for a HEAD). The Vigicrues flow is counted at its real weight.
        self.bytes: Counter[str] = Counter()
        self.methods: Counter[tuple[str, str]] = Counter()  # (source, HTTP method)
        # The times (seconds on the simulated clock) at which Vigicrues publishes: by default 07:55:15 and 13:55:15 UTC every day.
        self.vigicrues_publications: list[float] = self._daily_publications()

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handler))

    def _daily_publications(self) -> list[float]:
        """The publications of Vigicrues from two days before the start of the simulation to two days after."""
        first_day = int(self.clock.time() // DAY_S) - 2
        return [
            day * DAY_S + hour - self.clock.time() + self.clock.now
            for day in range(first_day, first_day + 6)
            for hour in VIGICRUES_PUBLICATION_HOURS_UTC
        ]

    def _last_publication(self) -> tuple[int, float]:
        """Index and time (simulated clock) of the latest publication of Vigicrues."""
        done = [(i, t) for i, t in enumerate(self.vigicrues_publications) if t <= self.clock.now]
        return done[-1] if done else (-1, self.vigicrues_publications[0] - DAY_S)

    def _vigicrues_flow(self) -> bytes:
        """The recorded flow (or the one a test set) stamped with the latest publication, as the real one is at each."""
        index, at = self._last_publication()
        payload = json.loads(self.bodies.get("vigicrues_flow") or (FIXTURES / "vigicrues" / "InfoVigiCru_subset.geojson").read_bytes())
        published = datetime.fromtimestamp(self.clock.EPOCH + at, tz=timezone.utc)
        payload["RefInfoVigiCru"] = f"{published:%d%m%Y_%H}"
        payload["DtHrInfoVigiCru"] = published.isoformat(timespec="seconds")
        return json.dumps(payload).encode()

    def _vigicrues(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == VIGICRUES_IMAGE_PATH:
            index, at = self._last_publication()
            self.bytes["vigicrues_flow"] += VIGICRUES_HEAD_BYTES
            stamp = self.clock.EPOCH + at + 21  # the image is regenerated 21 seconds after the flow
            return httpx.Response(200, headers={"Last-Modified": http_date(stamp), "ETag": f'"{index}-{int(stamp)}"'})
        self.bytes["vigicrues_flow"] += VIGICRUES_FLOW_BYTES
        return httpx.Response(200, content=self._vigicrues_flow())

    def _tsunami(self, request: httpx.Request) -> httpx.Response:
        source = "tsunami_warning_centers"
        path = request.url.path
        if path == storm_samples.TSUNAMI_LISTING_PATH:
            body, modified = storm_samples.tsunami_listing_at(self.clock.time())
            if modified and request.headers.get("If-Modified-Since") == http_date(modified):
                self.not_modified[source] += 1
                self.bytes[source] += TSUNAMI_304_HEAD_BYTES
                return httpx.Response(304, headers={"Last-Modified": http_date(modified)})
            wire = gzip.compress(body)
            self.bytes[source] += len(wire)
            headers = {"Last-Modified": http_date(modified)} if modified else {}
            return httpx.Response(200, content=wire, headers={**headers, "Content-Encoding": "gzip", "Content-Type": "application/javascript"})
        recorded = storm_samples.tsunami_document(path)
        if recorded is not None:
            wire = gzip.compress(recorded)
            self.bytes[source] += len(wire)
            return httpx.Response(200, content=wire, headers={"Content-Encoding": "gzip"})
        return httpx.Response(404, text="Not found")

    def _nhc(self, request: httpx.Request) -> httpx.Response:
        source = "nhc_storms"
        path = request.url.path
        if path == "/CurrentStorms.json":
            body = self.bodies.get(source) or (FIXTURES / "nhc" / "CurrentStorms_empty.json").read_bytes()
            etag = '"' + hashlib.sha1(body).hexdigest()[:13] + '"'
            if request.headers.get("If-None-Match") == etag:
                self.not_modified[source] += 1
                self.bytes[source] += NHC_304_HEAD_BYTES
                return httpx.Response(304, headers={"ETag": etag})
            self.bytes[source] += len(body)
            return httpx.Response(200, content=body, headers={"ETag": etag, "Content-Type": "application/json"})
        if path.startswith("/gis/forecast/archive/") and path.endswith(".zip"):
            bundle = (FIXTURES / "nhc" / "al092026_5day_013A.zip").read_bytes()
            self.bytes[source] += len(bundle)
            return httpx.Response(200, content=bundle, headers={"Content-Type": "application/zip"})
        return httpx.Response(404, text="Not found")

    async def _handler(self, request: httpx.Request) -> httpx.Response:
        source = source_of(request)
        if source is None:
            return httpx.Response(404, json={"error": f"unknown simulated host {request.url.host}"})
        params = dict(request.url.params)
        self.calls[source] += 1
        self.methods[(source, request.method)] += 1
        if source in OPEN_METEO_SOURCES:
            weight = open_meteo_weight(source, params)
            self.per_hour[int(self.clock.now // 3600)] += weight
            self.per_minute[int(self.clock.now // 60)] += weight
        else:
            weight = 1.0
        self.weight[source] += weight
        if self.record_urls:
            self.requests.append((source, str(request.url)))
        # The answer takes a few event-loop turns: concurrent requests overlap, as on a real network.
        for _ in range(self.latency_ticks):
            await asyncio.sleep(0)
        if source == "meteoalarm_atom":
            slug = request.url.path.rsplit("-", 1)[-1]
            name = slug if (METEOALARM_FIXTURES / f"atom_{slug}.xml").exists() else "empty"
            return httpx.Response(200, content=(METEOALARM_FIXTURES / f"atom_{name}.xml").read_bytes())
        if source == "meteoalarm_cap":
            return httpx.Response(200, content=(METEOALARM_FIXTURES / "cap_france_b77d3bc0.xml").read_bytes())
        if source == "dwd_cap":
            if request.headers.get("If-None-Match") == DWD_ETAG:
                self.not_modified[source] += 1
                return httpx.Response(304)
            return httpx.Response(
                200,
                content=self.bodies.get(source) or (FIXTURES / "dwd" / "status_district_mul.zip").read_bytes(),
                headers={"ETag": DWD_ETAG, "Last-Modified": DWD_LAST_MODIFIED},
            )
        if source == "eccc_alerts":
            return httpx.Response(200, content=self.bodies.get(source) or (FIXTURES / "eccc" / "weather_alerts_items.json").read_bytes())
        if source == "metservice_cap":
            if request.headers.get("If-Modified-Since") == METSERVICE_LAST_MODIFIED:
                self.not_modified[source] += 1
                return httpx.Response(304)
            return httpx.Response(
                200,
                content=self.bodies.get(source) or (FIXTURES / "metservice" / "rss_empty.xml").read_bytes(),
                headers={"Last-Modified": METSERVICE_LAST_MODIFIED},
            )
        if source == "vigicrues_flow":
            return self._vigicrues(request)
        if source == "tsunami_warning_centers":
            return self._tsunami(request)
        if source == "nhc_storms":
            return self._nhc(request)
        return httpx.Response(200, json=self._payload(source, params))

    def _payload(self, source: str, params: dict[str, str]) -> Any:
        stamp_ms = int(self.clock.time() * 1000)
        if source == "open_meteo_hd":
            full = samples.open_meteo_payload()
            payload: dict[str, Any] = {"utc_offset_seconds": full["utc_offset_seconds"]}
            for block in ("current", "minutely_15", "daily"):
                if block in params:
                    payload[block] = full[block]
            return payload
        if source == "open_meteo_aqi":
            return {"current": {"european_aqi": 31, "us_aqi": 42, "pm10": 14.0, "pm2_5": 8.0, "uv_index": 3.0, "dust": 1.0}}
        if source == "open_meteo_flood":
            return {
                "daily": {
                    "river_discharge": [42.0, 43.0, 44.0],
                    "river_discharge_mean": [40.0, 40.0, 40.0],
                    "river_discharge_max": [46.0, 47.0, 48.0],
                }
            }
        if source == "open_meteo_marine":
            return samples.marine_payload()
        if source == "met_norway_fallback":
            return samples.metno_payload()
        if source == "rainviewer_radar":
            return {
                "host": "https://tilecache.rainviewer.com",
                "radar": {"past": [{"time": int(self.clock.time()) // 600 * 600, "path": "/v2/radar/1"}]},
            }
        if source == "usgs_eq":
            return {
                "features": [
                    {
                        "id": "us1",
                        "properties": {"mag": 5.4, "place": "Test place", "time": stamp_ms - 3_600_000, "tsunami": 0},
                        "geometry": {"coordinates": [142.8, 38.1, 24.0]},
                    }
                ]
            }
        if source in ("emsc_seismic", "gdacs_global", "noaa_nws"):
            return {"features": []}
        if source == "usgs_volcano_gvp":
            return []
        if source == "noaa_swpc":
            return [{"time_tag": "2026-10-14T09:00:00", "Kp": 2.33}]
        raise AssertionError(source)


class FakeStorage:
    """In-memory stand-in for ``SQLiteWALStorage``: the simulation is about provider calls, not disk writes."""

    def __init__(self, zones: Optional[list[dict[str, Any]]] = None) -> None:
        self._zones = [MonitoredZoneConfig(**zone) for zone in (zones or DEFAULT_WATCHLIST_SEED)]

    def list_zones(self) -> list[MonitoredZoneConfig]:
        return list(self._zones)

    def record_hazards(self, hazards: Any) -> int:
        return len(list(hazards))

    def save_snapshot(self, snapshot: Any) -> None:
        return None

    def get_journal_mode(self) -> str:
        return "memory"


class NullHub:
    """WebSocket hub without clients."""

    async def broadcast(self, message: dict[str, Any]) -> None:
        return None


def build_world_points(count: int = 50, seed: int = 2026) -> list[tuple[float, float]]:
    """``count`` positions at least 0.5 degree apart from each other and from the default zones.

    Every position falls in a different cell of every grid (0.05 and 0.1 degree), so the simulation
    does not benefit from the cells being shared: it measures the other mechanisms alone.
    """
    rng = random.Random(seed)
    taken: list[tuple[float, float]] = [(z["lat"], z["lon"]) for z in DEFAULT_WATCHLIST_SEED]
    points: list[tuple[float, float]] = []
    while len(points) < count:
        lat = round(rng.uniform(-55.0, 65.0), 4)
        lon = round(rng.uniform(-170.0, 175.0), 4)
        if all(math.hypot(lat - p_lat, lon - p_lon) >= 0.5 for p_lat, p_lon in taken):
            points.append((lat, lon))
            taken.append((lat, lon))
    return points


@dataclass
class Client:
    """One display or page that polls the API."""

    index: int
    point: tuple[float, float]
    kind: str  # "frame", "frame-marine" or "web"
    phase_s: int


@dataclass
class Scenario:
    """The load of the simulation."""

    clients: int = 1000
    points: int = 50
    harbour_points: int = 10
    poll_period_s: int = 1800
    startup_spread_s: int = 60
    web_clients_per_land_point: int = 2
    seed: int = 7


def build_clients(scenario: Scenario) -> list[Client]:
    """Spread the clients evenly over the points.

    The first ``harbour_points`` points are harbours: all their clients ask for the marine conditions.
    On the other points a few clients are web pages (full forecast, 10 days); the others are frames.
    """
    rng = random.Random(scenario.seed)
    points = build_world_points(scenario.points)
    clients: list[Client] = []
    per_point = scenario.clients // scenario.points
    for p_index, point in enumerate(points):
        for slot in range(per_point):
            if p_index < scenario.harbour_points:
                kind = "frame-marine"
            elif slot < scenario.web_clients_per_land_point:
                kind = "web"
            else:
                kind = "frame"
            clients.append(Client(len(clients), point, kind, rng.randrange(scenario.poll_period_s)))
    return clients


@dataclass
class DayResult:
    calls: Counter[str] = field(default_factory=Counter)
    weight: Counter[str] = field(default_factory=Counter)
    per_hour: Counter[int] = field(default_factory=Counter)
    per_minute: Counter[int] = field(default_factory=Counter)
    requests_served: int = 0
    scheduler_cycles: int = 0
    failures: int = 0


class Simulation:
    """Plays one simulated day: clients and the scheduler of the application against the simulated upstream."""

    def __init__(self, clock: SimClock, scenario: Optional[Scenario] = None, zones: Optional[list[dict[str, Any]]] = None) -> None:
        # ``clock`` must be installed as the time source of the collectors (the ``sim_clock`` fixture).
        self.clock = clock
        self.scenario = scenario or Scenario()
        self.upstream = Upstream(clock)
        http = self.upstream.client()
        self.http = http
        self.aggregator = AggregatorService(
            usgs=USGSCollector(http_client=http),
            emsc=EMSCCollector(http_client=http),
            gdacs=GDACSCollector(http_client=http),
            noaa_nws=NOAANWSCollector(http_client=http),
            volcano=USGSVolcanoCollector(http_client=http),
            open_meteo=OpenMeteoCollector(http_client=http),
            met_norway=MetNorwayCollector(http_client=http),
            rainviewer=RainViewerCollector(http_client=http),
            marine=OpenMeteoMarineCollector(http_client=http),
            flood=OpenMeteoFloodCollector(http_client=http),
            air_quality=OpenMeteoAirQualityCollector(http_client=http),
            swpc=NOAASWPCCollector(http_client=http),
            meteoalarm=MeteoAlarmCollector(http_client=http),
            meteoalarm_cap=MeteoAlarmCapCollector(http_client=http),
            dwd=DWDCollector(http_client=http),
            eccc=ECCCCollector(http_client=http),
            metservice=MetServiceCollector(http_client=http),
            vigicrues=VigicruesCollector(http_client=http),
            nhc=NHCCollector(http_client=http),
            tsunami=TsunamiCollector(http_client=http),
            storage=FakeStorage(zones),  # type: ignore[arg-type]
        )
        self.scheduler = BackgroundAlertScheduler(
            self.aggregator, NullHub(), interval_seconds=SCHEDULER_PERIOD_S
        )
        self.result = DayResult()

    async def _client_request(self, client: Client) -> None:
        lat, lon = client.point
        try:
            if client.kind == "web":
                await self.aggregator.build_snapshot(device_type=DeviceType.WEB, compact=False)
                await self.aggregator.build_point_response(
                    lat=lat, lon=lon, device_type=DeviceType.WEB, compact=False
                )
            else:
                await self.aggregator.build_embedded_frame(
                    device_type=DeviceType.WAVESHARE_7B, lat=lat, lon=lon, marine=client.kind == "frame-marine"
                )
            self.result.requests_served += 1
        except Exception:
            self.result.failures += 1

    async def _scheduler_cycle(self) -> None:
        outcome = await self.scheduler.run_single_cycle()
        self.result.scheduler_cycles += 1
        if outcome.get("status") != "ok":
            self.result.failures += 1

    def build_events(self, duration_s: int = DAY_S) -> dict[int, list[Callable[[], Awaitable[None]]]]:
        """Map each simulated second to the actions that start in it."""
        scenario = self.scenario
        events: dict[int, list[Callable[[], Awaitable[None]]]] = {}

        def add(second: int, action: Callable[[], Awaitable[None]]) -> None:
            events.setdefault(second, []).append(action)

        for second in range(0, duration_s, SCHEDULER_PERIOD_S):
            add(second, self._scheduler_cycle)

        rng = random.Random(scenario.seed + 1)
        for client in build_clients(scenario):
            # Cold start: the API has just started and every display reconnects within a minute.
            add(rng.randrange(scenario.startup_spread_s), lambda c=client: self._client_request(c))
            for second in range(client.phase_s, duration_s, scenario.poll_period_s):
                add(second, lambda c=client: self._client_request(c))
        return events

    async def run(
        self, duration_s: int = DAY_S, extra: Optional[dict[int, list[Callable[[], Awaitable[None]]]]] = None
    ) -> DayResult:
        """Play the day. ``extra`` adds actions of a test (a second -> the actions that start in it) to the clients' and the scheduler's."""
        events = self.build_events(duration_s)
        for second, actions in (extra or {}).items():
            events.setdefault(second, []).extend(actions)
        for second in sorted(events):
            self.clock.now = float(second)
            await asyncio.gather(*(action() for action in events[second]))
        self.clock.now = float(duration_s)
        await self.http.aclose()
        self.result.calls = Counter(self.upstream.calls)
        self.result.weight = Counter(self.upstream.weight)
        self.result.per_hour = Counter(self.upstream.per_hour)
        self.result.per_minute = Counter(self.upstream.per_minute)
        return self.result


def open_meteo_total(result: DayResult) -> tuple[int, float]:
    """Calls and weight sent to the four Open-Meteo APIs."""
    return (
        sum(result.calls[s] for s in OPEN_METEO_SOURCES),
        round(sum(result.weight[s] for s in OPEN_METEO_SOURCES), 1),
    )


def open_meteo_peaks(result: DayResult) -> tuple[float, float]:
    """The heaviest hour and the heaviest minute of Open-Meteo weight."""
    return (
        round(max(result.per_hour.values(), default=0.0), 1),
        round(max(result.per_minute.values(), default=0.0), 1),
    )


def _print_table(result: DayResult) -> None:
    """Print the calls and the weight sent to each source, as the README table does."""
    print(f"{'source':22s} {'calls':>7s} {'weight':>9s}")
    for source in sorted(result.calls):
        print(f"{source:22s} {result.calls[source]:7d} {result.weight[source]:9.1f}")
    calls, weight = open_meteo_total(result)
    hour, minute = open_meteo_peaks(result)
    print(f"{'Open-Meteo (4 APIs)':22s} {calls:7d} {weight:9.1f}   (quota: 10,000 a day)")
    print(f"heaviest Open-Meteo hour: {hour} (quota 5,000), minute: {minute} (quota 600)")


def main() -> None:
    """Play the day against the code of this checkout and print the calls per source.

    ``python -m tests.simulation`` (about 20 seconds). The pytest test ``tests/test_call_sobriety.py``
    plays the same day and checks it against the Open-Meteo quota.
    """
    from app import clock as app_clock

    clock = SimClock()
    app_clock._monotonic = clock.monotonic
    app_clock._wall = clock.time
    result = asyncio.run(Simulation(clock).run())
    print(f"{result.requests_served} requests from 1,000 clients, {result.scheduler_cycles} scheduler cycles")
    _print_table(result)


if __name__ == "__main__":
    main()
