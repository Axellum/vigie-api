"""Measure whether the mean of several weather models beats Open-Meteo's ``best_match`` at 1 to 5 days.

The question (ADR 0010): ``OpenMeteoCollector`` asks Open-Meteo for its default blend, ``best_match``. Would the plain mean of
several models forecast the daily minimum, maximum, rain and wind better, at the same lead time, against real observations?
Nothing in ``app/`` changes before the numbers say so. This tool is the measure; the report is
``docs/measurements/forecast-models-2026-10.md`` and the results are ``docs/measurements/forecast-models-2026-10-results.json.gz``.

Each step can be restarted and none asks twice for what it already has::

    python tools/measure_forecast_models.py plan     # what would be asked and what it weighs (no network)
    python tools/measure_forecast_models.py probe    # which models answer at 1 and 5 days, and the probability of precipitation (about 6 calls)
    python tools/measure_forecast_models.py fetch    # the forecasts (Open-Meteo) and the observations (Meteo-France, IEM)
    python tools/measure_forecast_models.py score    # the scores and the bootstrap intervals, from the cache only

**What is compared.** The Previous Runs API (https://open-meteo.com/en/docs/previous-runs-api, host
``previous-runs-api.open-meteo.com``) gives for every hour what a model predicted 24 h x N earlier: ``temperature_2m_previous_day3``
is the value predicted 72 hours before. Every competitor is compared at the same lead time. The hourly values are folded into the
daily quantity over the same window as the observation:

- **France**: the daily files of Meteo-France (Licence Ouverte 2.0). The windows were found by rebuilding the daily values from the
  hourly files of four stations (mean absolute difference 0.000 over 360 station-days): TX from 06 UTC of D to 06 UTC of D+1, TN
  from 18 UTC of D-1 to 18 UTC of D, RR from 06 UTC of D to 06 UTC of D+1, FXY (the day's maximum of the 10-minute mean wind) over the UTC day.
- **World**: the METAR and SPECI reports of airports archived by the Iowa Environmental Mesonet (public domain), over the **local**
  calendar day of the station. The maximum of a series of reports is a little under the true maximum and the minimum a little
  above it; the bias is the same for every competitor, so the comparison holds, the absolute bias does not.

**The budget is a hard stop.** Every request to Open-Meteo is written to a ledger in the cache folder with its weight (the rule
of ``app/collectors/weights.py``, the models multiplying the variables as the Open-Meteo source does), BEFORE it leaves, and the
tool refuses a request that would pass ``--max-weight`` (1,500 by default, all the runs together since the ledger is kept). The
answers are cached on disk by URL. The pace is at most 300 weighted calls a minute and two requests a second to a host; IEM asks
for one a second. The User-Agent names the tool and carries no address.

The cache folder is outside the repository (the temporary folder by default). On Windows the time zones need ``pip install tzdata``.
The statistics are pure functions, run with no network by ``tests/test_measure_forecast_models.py``.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import sys
import tempfile
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.collectors.weather import OpenMeteoCollector  # noqa: E402
from app.collectors.weights import VARIABLE_PARAMETERS, open_meteo_weight  # noqa: E402

# ---------------------------------------------------------------------------------------------------------------------
# What is measured
# ---------------------------------------------------------------------------------------------------------------------

# Identifies the tool and carries no address of anyone (the API's own User-Agent is "vigie-api (personal project)").
USER_AGENT = "vigie-api measure_forecast_models (personal project)"

PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
MF_BASE = "https://meteofrance.s3.sbg.io.cloud.ovh.net/data/synchro_ftp/BASE"
IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"

LEADS = (1, 2, 3, 4, 5)
# The hourly variables asked: the temperature gives TX and TN, the precipitation the daily total, the wind the daily maximum.
HOURLY_VARIABLES = ("temperature_2m", "precipitation", "wind_speed_10m")

# The competitors. ``best_match`` is what the API serves today; the others are the models of the providers named in the
# plan, in their "seamless" form (the regional model first, then the global one, as Open-Meteo chains them).
BEST_MATCH = "best_match"
MODELS = (
    "ecmwf_ifs025",
    "ecmwf_aifs025_single",
    "icon_seamless",
    "gfs_seamless",
    "meteofrance_seamless",
    "ukmo_seamless",
    "gem_seamless",
    "jma_seamless",
)

# Usefulness thresholds, fixed before the first score was computed: below them a gain is noise for a person who reads the
# forecast, whatever the interval says. Temperature: 0.2 degC (see the report). The others are conventions of this project.
USEFUL_GAIN = {"tx": 0.2, "tn": 0.2, "rr": 0.2, "ws": 0.2}  # degC, degC, mm, m/s
RAIN_EVENT_MM = 1.0
MIN_WINDOW_COVERAGE = 0.9  # a daily value needs this share of its hourly values (forecasts) or hours (observations)

# Open-Meteo's own threshold between a "trace" and rain is not used: the event is the daily total >= RAIN_EVENT_MM.

KNOT_TO_MS = 0.514444
INCH_TO_MM = 25.4


@dataclass(frozen=True)
class Station:
    """An official station: Meteo-France (``source == "mf"``, key = the 8-digit NUM_POSTE) or an airport (``"iem"``)."""

    key: str
    name: str
    group: str  # "FR" or "WORLD"
    lat: float
    lon: float
    tz: str
    source: str
    mf_dept: str = ""
    note: str = ""


# Coordinates as given by the data provider (checked against the files by ``fetch``), rounded to 4 decimals.
STATIONS: tuple[Station, ...] = (
    Station("75114001", "Paris-Montsouris", "FR", 48.8217, 2.3378, "Europe/Paris", "mf", "75"),
    Station("69029001", "Lyon-Bron", "FR", 45.7213, 4.9492, "Europe/Paris", "mf", "69"),
    Station("13054001", "Marseille-Marignane", "FR", 43.4370, 5.2115, "Europe/Paris", "mf", "13"),
    Station("06088001", "Nice-Cote d'Azur", "FR", 43.6488, 7.2090, "Europe/Paris", "mf", "06"),
    Station("29075001", "Brest-Guipavas", "FR", 48.4538, -4.3912, "Europe/Paris", "mf", "29"),
    Station("67124001", "Strasbourg-Entzheim", "FR", 48.5495, 7.6403, "Europe/Paris", "mf", "67"),
    Station("31069001", "Toulouse-Blagnac", "FR", 43.6210, 1.3788, "Europe/Paris", "mf", "31"),
    Station("33281001", "Bordeaux-Merignac", "FR", 44.8307, -0.6913, "Europe/Paris", "mf", "33"),
    Station("59343001", "Lille-Lesquin", "FR", 50.5700, 3.0975, "Europe/Paris", "mf", "59"),
    Station("20148001", "Bastia", "FR", 42.5407, 9.4852, "Europe/Paris", "mf", "20"),
    Station("74056001", "Chamonix", "FR", 45.9295, 6.8775, "Europe/Paris", "mf", "74", "mountain valley, 1042 m"),
    Station("17300009", "La Rochelle-Ile de Re", "FR", 46.1780, -1.1932, "Europe/Paris", "mf", "17", "Atlantic coast"),
    Station("EDDF", "Frankfurt (Germany)", "WORLD", 50.0258, 8.5214, "Europe/Berlin", "iem"),
    Station("LEMD", "Madrid-Barajas (Spain)", "WORLD", 40.4667, -3.5556, "Europe/Madrid", "iem"),
    Station("EGLL", "London Heathrow (UK)", "WORLD", 51.4785, -0.4614, "Europe/London", "iem"),
    Station("JFK", "New York JFK (US East Coast)", "WORLD", 40.6386, -73.7622, "America/New_York", "iem", note="rain"),
    Station("CYYZ", "Toronto Pearson (Canada)", "WORLD", 43.6772, -79.6306, "America/Toronto", "iem"),
    Station("NZAA", "Auckland (New Zealand)", "WORLD", -37.0081, 174.7917, "Pacific/Auckland", "iem"),
    Station("RJTT", "Tokyo Haneda (Japan)", "WORLD", 35.5533, 139.7811, "Asia/Tokyo", "iem"),
    Station("TJSJ", "San Juan (Puerto Rico, tropical)", "WORLD", 18.4325, -66.0108, "America/Puerto_Rico", "iem", note="rain"),
)


# ---------------------------------------------------------------------------------------------------------------------
# Cache, ledger and requests
# ---------------------------------------------------------------------------------------------------------------------


def default_cache_dir() -> Path:
    """The folder of the cached answers and of the ledger: outside the repository, in the temporary folder."""
    return Path(tempfile.gettempdir()) / "vigie-forecast-models-cache"


class BudgetExceeded(RuntimeError):
    """The next request would pass the ceiling of weighted calls."""


def request_weight(params: Mapping[str, str]) -> float:
    """Weight of a request to Open-Meteo, in API calls.

    The rule of ``app/collectors/weights.py`` counts the names of the variables; the Open-Meteo source counts the variables
    times the models (``nVariablesTimesDomains``, ``Sources/App/Controllers/ForecastapiController.swift``), so each list of
    variables is repeated once per model before the rule is applied. A request without ``models`` is one model.
    """
    models = [name for name in params.get("models", "").split(",") if name.strip()] or [BEST_MATCH]
    expanded = dict(params)
    for parameter in VARIABLE_PARAMETERS:
        names = [name for name in params.get(parameter, "").split(",") if name.strip()]
        if names:
            expanded[parameter] = ",".join(f"{name}@{model}" for model in models for name in names)
    if "start_date" in params and "end_date" in params:
        # The rule counts ``forecast_days`` + ``past_days``; a request by dates covers the days from the first to the last.
        days = (date.fromisoformat(params["end_date"]) - date.fromisoformat(params["start_date"])).days + 1
        expanded["forecast_days"], expanded["past_days"] = str(days), "0"
    return open_meteo_weight(expanded, default_forecast_days=7)


def canonical_url(base: str, params: Mapping[str, str]) -> str:
    """The URL of a request with its parameters in a fixed order (so the cache key does not depend on a dict's order)."""
    query = "&".join(f"{key}={params[key]}" for key in sorted(params))
    return f"{base}?{query}"


class Throttle:
    """At most one request per ``min_interval_s`` to the same host."""

    def __init__(self, min_interval_s: float) -> None:
        self.min_interval_s = min_interval_s
        self._last: dict[str, float] = {}

    def wait(self, host: str) -> None:
        pause = self._last.get(host, 0.0) + self.min_interval_s - time.monotonic()
        if pause > 0:
            time.sleep(pause)
        self._last[host] = time.monotonic()


@dataclass
class Source:
    """A cache folder, a ledger of the weighted calls sent to Open-Meteo and an HTTP client."""

    cache_dir: Path
    max_weight: float
    max_weight_per_minute: float = 300.0  # the free API allows 600 a minute and another instance may share the address
    refresh: bool = False
    refresh_obs: bool = False
    dry_run: bool = False
    om_calls: int = 0  # requests really sent to Open-Meteo by this run
    om_hits: int = 0  # requests answered by the cache
    _client: Optional[httpx.Client] = field(default=None, repr=False)
    _throttle: Throttle = field(default_factory=lambda: Throttle(0.5), repr=False)
    _iem_throttle: Throttle = field(default_factory=lambda: Throttle(1.2), repr=False)
    _next_open_meteo: float = field(default=0.0, repr=False)

    def __post_init__(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def ledger_path(self) -> Path:
        return self.cache_dir / "open-meteo-ledger.jsonl"

    def ledger(self) -> list[dict[str, Any]]:
        if not self.ledger_path.exists():
            return []
        return [json.loads(line) for line in self.ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def spent(self) -> float:
        """Weighted calls already sent, over all the runs (every request that left, whatever the answer)."""
        return round(sum(float(entry["weight"]) for entry in self.ledger()), 4)

    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=httpx.Timeout(120.0, connect=20.0),
                follow_redirects=True,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _path(self, kind: str, key: str, suffix: str) -> Path:
        folder = self.cache_dir / kind
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{key}{suffix}"

    def open_meteo(self, params: Mapping[str, str], label: str) -> dict[str, Any]:
        """The JSON answer of the Previous Runs API for ``params``: from the cache, else asked once and kept.

        Raises ``BudgetExceeded`` before sending anything that would pass the ceiling.
        """
        url = canonical_url(PREVIOUS_RUNS_URL, params)
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        path = self._path("open-meteo", key, ".json")
        if path.exists() and not self.refresh:
            self.om_hits += 1
            return json.loads(path.read_text(encoding="utf-8"))
        weight = request_weight(params)
        if self.dry_run:
            raise BudgetExceeded(f"dry run: would send {label} (weight {weight})")
        if self.spent() + weight > self.max_weight + 1e-9:
            raise BudgetExceeded(
                f"refusing {label}: weight {weight} on top of {self.spent()} already spent passes the ceiling "
                f"of {self.max_weight}"
            )
        # The weight is booked BEFORE the request leaves, so that a timeout (which may still have been counted by the
        # server) never leaves it out; the answer is booked after, with a weight of 0.
        sent = {
            "event": "sent",
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "label": label,
            "weight": weight,
            "key": key,
        }
        with self.ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(sent) + "\n")
        pause = self._next_open_meteo - time.monotonic()
        if pause > 0:
            time.sleep(pause)
        started = time.time()
        response = self.client().get(PREVIOUS_RUNS_URL, params={k: params[k] for k in sorted(params)})
        # The next request waits as long as the weight of this one needs to stay under the weighted calls a minute.
        self._next_open_meteo = time.monotonic() + max(0.5, weight * 60.0 / self.max_weight_per_minute)
        answered = {
            "event": "answered",
            "label": label,
            "weight": 0.0,
            "status": response.status_code,
            "seconds": round(time.time() - started, 2),
            "key": key,
        }
        with self.ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(answered) + "\n")
        self.om_calls += 1
        if response.status_code != 200:
            raise RuntimeError(f"Open-Meteo answered HTTP {response.status_code} for {label}: {response.text[:300]}")
        payload = response.json()
        path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        return payload

    def download(
        self,
        kind: str,
        name: str,
        url: str,
        params: Optional[Sequence[tuple[str, str]]] = None,
        slow_host: bool = False,
    ) -> bytes:
        """A file of an observation source: from the cache, else downloaded once and kept (``--refresh-obs`` renews it).

        A ``<name>.meta.json`` beside the file keeps what the report quotes: the address, the moment, the server's
        ``Last-Modified`` and the size.
        """
        path = self._path(kind, name, "")
        if path.exists() and not self.refresh_obs:
            return path.read_bytes()
        if self.dry_run:
            raise BudgetExceeded(f"dry run: would download {url}")
        (self._iem_throttle if slow_host else self._throttle).wait(httpx.URL(url).host)
        response = self.client().get(url, params=list(params) if params else None, headers={"Accept": "*/*"})
        response.raise_for_status()
        path.write_bytes(response.content)
        meta = {
            "url": str(response.request.url),
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "last_modified": response.headers.get("last-modified"),
            "bytes": len(response.content),
            "sha256": hashlib.sha256(response.content).hexdigest(),
        }
        path.with_name(path.name + ".meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return response.content

    def download_meta(self, kind: str, name: str) -> dict[str, Any]:
        path = self._path(kind, name, "")
        meta_path = path.with_name(path.name + ".meta.json")
        return json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}


# ---------------------------------------------------------------------------------------------------------------------
# Days and windows
# ---------------------------------------------------------------------------------------------------------------------

UTC = timezone.utc
QUANTITIES = ("tx", "tn", "rr", "ws")
QUANTITY_LABEL = {
    "tx": "daily maximum temperature (degC)",
    "tn": "daily minimum temperature (degC)",
    "rr": "daily rain (mm)",
    "ws": "daily maximum 10 m wind (m/s)",
}
# The hourly variable each quantity is folded from.
QUANTITY_VARIABLE = {"tx": "temperature_2m", "tn": "temperature_2m", "rr": "precipitation", "ws": "wind_speed_10m"}


@lru_cache(maxsize=None)
def zone_of(name: str) -> ZoneInfo:
    """The time zone ``name``; Windows has no time zone database of its own: ``pip install tzdata`` there."""
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as error:
        raise SystemExit(f"no time zone database for {name!r} ({error}); on Windows run: pip install tzdata") from error


def utc_midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def window_bounds(station: Station, quantity: str, day: date) -> tuple[datetime, datetime]:
    """UTC bounds ``(start, end)`` of the window of ``quantity`` for the observation date ``day``.

    France follows the Meteo-France convention, checked against the hourly files: TX from 06 UTC of D to 06 UTC of D+1,
    TN from 18 UTC of D-1 to 18 UTC of D, RR from 06 UTC of D to 06 UTC of D+1, FXY over the UTC day. Everywhere else the
    window is the local calendar day of the station (23 or 25 hours on the days the clocks change).
    """
    if station.source == "mf":
        start = utc_midnight(day)
        if quantity == "tx" or quantity == "rr":
            return start + timedelta(hours=6), start + timedelta(hours=30)
        if quantity == "tn":
            return start - timedelta(hours=6), start + timedelta(hours=18)
        if quantity == "ws":
            return start, start + timedelta(hours=24)
        raise ValueError(quantity)
    zone = zone_of(station.tz)
    start = datetime(day.year, day.month, day.day, tzinfo=zone).astimezone(UTC)
    following = day + timedelta(days=1)
    end = datetime(following.year, following.month, following.day, tzinfo=zone).astimezone(UTC)
    return start, end


def window_hours(start: datetime, end: datetime, accumulated: bool) -> list[datetime]:
    """The hourly labels of a window: ``start <= t < end`` for an instantaneous value, ``start < t <= end`` for a sum.

    Open-Meteo labels a precipitation sum with the END of its hour, and an instantaneous value with its own instant.
    """
    if start.minute or start.second or end.minute or end.second:
        raise ValueError("the windows of this tool begin and end on the hour")
    count = int((end - start).total_seconds() // 3600)
    first = start + timedelta(hours=1) if accumulated else start
    return [first + timedelta(hours=k) for k in range(count)]


def date_range(first: date, last: date) -> list[date]:
    return [first + timedelta(days=k) for k in range((last - first).days + 1)]


# ---------------------------------------------------------------------------------------------------------------------
# Forecasts: the Previous Runs API
# ---------------------------------------------------------------------------------------------------------------------


def hourly_names(leads: Iterable[int] = LEADS) -> list[str]:
    return [f"{variable}_previous_day{lead}" for variable in HOURLY_VARIABLES for lead in leads]


def forecast_params(lat: float, lon: float, first: date, last: date, models: Optional[Sequence[str]]) -> dict[str, str]:
    """The query of one station. Without ``models`` the API answers its default blend, ``best_match``."""
    from app.collectors.grid import coord_str  # local import: the module is only needed to build a request

    params = {
        "latitude": coord_str(lat),
        "longitude": coord_str(lon),
        "start_date": first.isoformat(),
        "end_date": last.isoformat(),
        "hourly": ",".join(hourly_names()),
        "timezone": "GMT",
        "wind_speed_unit": "ms",
    }
    if models:
        params["models"] = ",".join(models)
    return params


def grid_point(station: Station, step_deg: Optional[float] = None) -> tuple[float, float]:
    """The point the API asks Open-Meteo for: the station snapped to the forecast grid (``app/collectors/grid.py``)."""
    from app.collectors.grid import DEFAULT_FORECAST_GRID_DEG, snap_to_grid

    return snap_to_grid(station.lat, station.lon, step_deg or DEFAULT_FORECAST_GRID_DEG)


class HourlySeries:
    """The hourly answers of one request, as arrays of floats (``nan`` for ``null``) on a regular hourly axis."""

    def __init__(self, payload: Mapping[str, Any], models: Sequence[str]) -> None:
        hourly = payload["hourly"]
        times = hourly["time"]
        self.t0 = datetime.fromisoformat(times[0]).replace(tzinfo=UTC)
        self.n = len(times)
        last = datetime.fromisoformat(times[-1]).replace(tzinfo=UTC)
        if last - self.t0 != timedelta(hours=self.n - 1):
            raise ValueError("the hourly axis of the answer has gaps")
        self.model_elevation = payload.get("elevation")
        self.cell = (payload.get("latitude"), payload.get("longitude"))
        self.data: dict[tuple[str, str, int], np.ndarray] = {}
        for model in models:
            for variable in HOURLY_VARIABLES:
                for lead in LEADS:
                    base = f"{variable}_previous_day{lead}"
                    # With several models every column carries the model's name; with one it may not.
                    column = hourly.get(f"{base}_{model}", hourly.get(base) if len(models) == 1 else None)
                    if column is None:
                        raise KeyError(f"the answer has no column {base}_{model}")
                    self.data[(model, variable, lead)] = np.array(column, dtype=float)

    def index(self, moment: datetime) -> int:
        return int((moment - self.t0).total_seconds() // 3600)

    def fold(self, model: str, quantity: str, lead: int, station: Station, day: date) -> float:
        """The daily quantity predicted ``lead`` days ahead for the observation date ``day``; ``nan`` when it cannot be."""
        start, end = window_bounds(station, quantity, day)
        hours = window_hours(start, end, accumulated=(quantity == "rr"))
        low, high = self.index(hours[0]), self.index(hours[-1])
        if low < 0 or high >= self.n:
            return math.nan
        values = self.data[(model, QUANTITY_VARIABLE[quantity], lead)][low : high + 1]
        # A forecast is all or nothing in practice; a window that is not complete counts as a missing forecast.
        return fold_values(values, quantity, min_coverage=1.0)


def fold_values(values: np.ndarray, quantity: str, min_coverage: float = MIN_WINDOW_COVERAGE) -> float:
    """Fold the hourly values of a window into the daily quantity; ``nan`` if too few of them are known."""
    known = values[np.isfinite(values)]
    if len(values) == 0 or len(known) < math.ceil(min_coverage * len(values)):
        return math.nan
    if quantity == "tx" or quantity == "ws":
        return float(known.max())
    if quantity == "tn":
        return float(known.min())
    if quantity == "rr":
        # A missing hour of rain is not a dry hour: the sum is only kept when the coverage is enough, and it is the sum
        # of the hours known (the hours missing are few and the check above bounds them).
        return float(known.sum())
    raise ValueError(quantity)


# ---------------------------------------------------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------------------------------------------------


def mf_url(dept: str) -> str:
    return f"{MF_BASE}/QUOT/Q_{dept}_latest-2025-2026_RR-T-Vent.csv.gz"


def parse_mf_daily(raw: bytes, num_poste: str) -> tuple[dict[date, dict[str, float]], dict[str, Any]]:
    """The daily rows of one Meteo-France station: ``{date: {tx, tn, rr, ws}}`` (``nan`` where the file is empty).

    ``ws`` is FXY, in m/s: the daily maximum of the hourly maxima of the wind averaged over 10 minutes at 10 m. A value
    whose quality code is ``2`` (doubtful, under verification) is dropped. Also returns the station's name and position
    as written in the file.
    """
    text = gzip.decompress(raw).decode("utf-8")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    rows: dict[date, dict[str, float]] = {}
    info: dict[str, Any] = {"dropped_doubtful": 0}
    columns = {"tx": ("TX", "QTX"), "tn": ("TN", "QTN"), "rr": ("RR", "QRR"), "ws": ("FXY", "QFXY")}
    for row in reader:
        if row["NUM_POSTE"] != num_poste:
            continue
        info.setdefault("name", row["NOM_USUEL"])
        info.setdefault("lat", float(row["LAT"]))
        info.setdefault("lon", float(row["LON"]))
        info.setdefault("alt", float(row["ALTI"]))
        stamp = row["AAAAMMJJ"]
        values: dict[str, float] = {}
        for quantity, (column, quality) in columns.items():
            text_value = (row.get(column) or "").strip()
            if not text_value or (row.get(quality) or "").strip() == "2":
                if text_value:
                    info["dropped_doubtful"] += 1
                values[quantity] = math.nan
            else:
                values[quantity] = float(text_value)
        rows[date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8]))] = values
    return rows, info


@dataclass(frozen=True)
class Report:
    """One METAR or SPECI: its UTC time, the temperature (degC) and the wind (m/s), ``nan`` when absent."""

    valid: datetime
    temp_c: float
    wind_ms: float


def iem_station_id(station: Station) -> str:
    return station.key


def iem_params(station: Station, first: date, last: date, rain: bool) -> list[tuple[str, str]]:
    """The query to the IEM ASOS/METAR service: temperature and wind of the routine reports and the specials (type 3 and 4).

    Rain is a request of its own: the routine reports only (type 3), one report an hour, whose ``p01i`` is the hourly
    accumulation. Summing it over the specials too would count the same rain twice.
    """
    start = first - timedelta(days=2)
    stop = last + timedelta(days=3)
    params: list[tuple[str, str]] = [("station", iem_station_id(station))]
    params += [("data", "p01i")] if rain else [("data", "tmpf"), ("data", "sknt")]
    params += [
        ("sts", f"{start.isoformat()}T00:00:00Z"),
        ("ets", f"{stop.isoformat()}T00:00:00Z"),
        ("tz", "UTC"),
        ("format", "onlycomma"),
        ("latlon", "yes"),
        ("elev", "yes"),
        ("missing", "M"),
        ("trace", "0.0001"),
    ]
    params += [("report_type", "3")] if rain else [("report_type", "3"), ("report_type", "4")]
    return params


def parse_iem(raw: bytes) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """The rows of an IEM answer, and the station's position as written in it."""
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
    info: dict[str, Any] = {}
    if rows:
        info = {"lat": float(rows[0]["lat"]), "lon": float(rows[0]["lon"]), "alt": float(rows[0]["elevation"])}
    return rows, info


def iem_reports(rows: Sequence[Mapping[str, str]]) -> list[Report]:
    reports = []
    for row in rows:
        valid = datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
        temp = row.get("tmpf", "M")
        wind = row.get("sknt", "M")
        reports.append(
            Report(
                valid,
                (float(temp) - 32.0) * 5.0 / 9.0 if temp != "M" else math.nan,
                float(wind) * KNOT_TO_MS if wind != "M" else math.nan,
            )
        )
    return reports


def iem_rain(rows: Sequence[Mapping[str, str]]) -> list[tuple[datetime, float]]:
    """``(time, mm)`` of the routine reports that carry an hourly accumulation (``p01i``, inches; a trace counts 0.0001 in)."""
    out = []
    for row in rows:
        if row["p01i"] == "M":
            continue
        out.append((datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=UTC), float(row["p01i"]) * INCH_TO_MM))
    return out


def obs_from_reports(station: Station, quantity: str, day: date, reports: Sequence[Report]) -> float:
    """TX, TN or the maximum wind of the local day from the reports, ``nan`` when too few hours of the day have a report.

    The maximum of reports is a little under the real maximum, and the minimum a little above: the bias is the same for
    every forecast compared with it.
    """
    start, end = window_bounds(station, quantity, day)
    inside = [r for r in reports if start <= r.valid < end]
    pick = (lambda r: r.temp_c) if quantity in ("tx", "tn") else (lambda r: r.wind_ms)
    known = [(r.valid, pick(r)) for r in inside if math.isfinite(pick(r))]
    hours = {moment.replace(minute=0, second=0, microsecond=0) for moment, _ in known}
    expected = int((end - start).total_seconds() // 3600)
    if len(hours) < math.ceil(MIN_WINDOW_COVERAGE * expected):
        return math.nan
    values = [v for _, v in known]
    return min(values) if quantity == "tn" else max(values)


def obs_rain(station: Station, day: date, rain: Sequence[tuple[datetime, float]]) -> float:
    """The rain of the local day: the sum of the hourly accumulations of the routine reports, ``nan`` if hours are missing.

    A routine report is made a few minutes before the hour (near H:51 to H:56) and covers the hour that ends there: it is
    given the hour it is nearest to, as the label of an hourly sum, and the day takes the labels in ``(start, end]``.
    """
    start, end = window_bounds(station, "rr", day)
    labelled = [((moment + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0), mm) for moment, mm in rain]
    inside = [(label, mm) for label, mm in labelled if start < label <= end]
    expected = int((end - start).total_seconds() // 3600)
    if len({label for label, _ in inside}) < math.ceil(MIN_WINDOW_COVERAGE * expected):
        return math.nan
    # Two reports in the same hour (a correction) would count it twice: keep the largest accumulation of the hour.
    per_hour: dict[datetime, float] = {}
    for label, mm in inside:
        per_hour[label] = max(per_hour.get(label, 0.0), mm)
    return float(sum(per_hour.values()))


# ---------------------------------------------------------------------------------------------------------------------
# The data set: forecasts and observations on the same days
# ---------------------------------------------------------------------------------------------------------------------

GRID_ENTRY = "best_match_grid"  # best_match asked at the point snapped to the API's forecast grid, as the API does
ENTRIES = (BEST_MATCH, GRID_ENTRY, *MODELS)
SUBSETS = ("all", "fr", "world")
BOOTSTRAP_RESAMPLES = 4000
BOOTSTRAP_SEED = 20261021


@dataclass
class Dataset:
    """Forecasts ``fc[q][station, entry, lead, day]`` and observations ``obs[q][station, day]``; ``nan`` where unknown."""

    stations: list[Station]
    days: list[date]
    fc: dict[str, np.ndarray]
    obs: dict[str, np.ndarray]
    meta: dict[str, Any]

    def station_mask(self, subset: str, quantity: str) -> np.ndarray:
        if subset == "all":
            mask = np.ones(len(self.stations), dtype=bool)
        elif subset == "fr":
            mask = np.array([s.group == "FR" for s in self.stations])
        elif subset == "world":
            mask = np.array([s.group == "WORLD" for s in self.stations])
        else:
            raise ValueError(subset)
        return mask


def check_position(station: Station, found: Mapping[str, Any], tolerance_deg: float) -> None:
    if abs(found["lat"] - station.lat) > tolerance_deg or abs(found["lon"] - station.lon) > tolerance_deg:
        raise ValueError(
            f"{station.key}: the table says {station.lat}, {station.lon} but the provider says {found['lat']}, {found['lon']}"
        )


def load_observations(source: Source, stations: Sequence[Station], days: Sequence[date]) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """The daily observations of every station on ``days``, read from the cache (downloaded the first time)."""
    obs = {q: np.full((len(stations), len(days)), math.nan) for q in QUANTITIES}
    provenance: dict[str, Any] = {}
    mf_cache: dict[str, bytes] = {}
    for index, station in enumerate(stations):
        if station.source == "mf":
            if station.mf_dept not in mf_cache:
                mf_cache[station.mf_dept] = source.download("mf", f"Q_{station.mf_dept}.csv.gz", mf_url(station.mf_dept))
            rows, info = parse_mf_daily(mf_cache[station.mf_dept], station.key)
            if not rows:
                raise ValueError(f"{station.key} is not in the file of department {station.mf_dept}")
            check_position(station, info, 0.001)
            for d_index, day in enumerate(days):
                for quantity in QUANTITIES:
                    obs[quantity][index, d_index] = rows.get(day, {}).get(quantity, math.nan)
            provenance[station.key] = {
                "source": "Meteo-France daily climatological data",
                "name_in_file": info["name"],
                "altitude_m": info["alt"],
                "dropped_doubtful": info["dropped_doubtful"],
                "file": source.download_meta("mf", f"Q_{station.mf_dept}.csv.gz"),
            }
            continue
        first, last = days[0], days[-1]
        kinds = [("main", False)] + ([("rain", True)] if station.note == "rain" else [])
        metas = {}
        for kind, rain in kinds:
            params = iem_params(station, first, last, rain)
            name = f"{station.key}_{kind}_{first.isoformat()}_{last.isoformat()}.csv"
            raw = source.download("iem", name, IEM_URL, params, slow_host=True)
            rows_iem, info = parse_iem(raw)
            if not rows_iem:
                raise ValueError(f"IEM has no report for {station.key} ({kind})")
            check_position(station, info, 0.01)
            metas[kind] = source.download_meta("iem", name)
            if rain:
                rain_rows = iem_rain(rows_iem)
                for d_index, day in enumerate(days):
                    obs["rr"][index, d_index] = obs_rain(station, day, rain_rows)
            else:
                reports = iem_reports(rows_iem)
                for d_index, day in enumerate(days):
                    for quantity in ("tx", "tn", "ws"):
                        obs[quantity][index, d_index] = obs_from_reports(station, quantity, day, reports)
        provenance[station.key] = {
            "source": "IEM ASOS/METAR archive",
            "altitude_m": info["alt"],
            "files": metas,
        }
    return obs, provenance


def load_forecasts(source: Source, stations: Sequence[Station], request_days: tuple[date, date], days: Sequence[date]) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Fold the cached answers into ``fc[q][station, entry, lead, day]``."""
    first, last = request_days
    fc = {q: np.full((len(stations), len(ENTRIES), len(LEADS), len(days)), math.nan) for q in QUANTITIES}
    details: dict[str, Any] = {}
    for s_index, station in enumerate(stations):
        main_params = forecast_params(station.lat, station.lon, first, last, (BEST_MATCH, *MODELS))
        grid_lat, grid_lon = grid_point(station)
        grid_params = forecast_params(grid_lat, grid_lon, first, last, None)
        main = HourlySeries(source.open_meteo(main_params, f"{station.key} main"), (BEST_MATCH, *MODELS))
        grid = HourlySeries(source.open_meteo(grid_params, f"{station.key} grid"), (BEST_MATCH,))
        details[station.key] = {
            "requested": [station.lat, station.lon],
            "grid_point": [grid_lat, grid_lon],
            "model_elevation_m": main.model_elevation,
            "grid_elevation_m": grid.model_elevation,
        }
        for e_index, entry in enumerate(ENTRIES):
            series, model = (grid, BEST_MATCH) if entry == GRID_ENTRY else (main, entry)
            for quantity in QUANTITIES:
                for l_index, lead in enumerate(LEADS):
                    for d_index, day in enumerate(days):
                        fc[quantity][s_index, e_index, l_index, d_index] = series.fold(model, quantity, lead, station, day)
    return fc, details


def build_dataset(source: Source, stations: Sequence[Station], first_scored: date, last_scored: date) -> Dataset:
    days = date_range(first_scored, last_scored)
    request_days = (first_scored - timedelta(days=1), last_scored + timedelta(days=1))
    fc, details = load_forecasts(source, stations, request_days, days)
    obs, provenance = load_observations(source, stations, days)
    return Dataset(
        list(stations),
        days,
        fc,
        obs,
        {"request_days": [d.isoformat() for d in request_days], "forecast_details": details, "observations": provenance},
    )


# ---------------------------------------------------------------------------------------------------------------------
# Statistics (pure: no network, no files)
# ---------------------------------------------------------------------------------------------------------------------


def make_resamples(n_days: int, n_boot: int = BOOTSTRAP_RESAMPLES, seed: int = BOOTSTRAP_SEED) -> np.ndarray:
    """Indices ``(n_boot, n_days)`` of whole days drawn with replacement.

    Whole days, every station of the day together: the stations of a day share the weather of the day, so resampling
    station-days one by one would make the interval narrower than it is. The same draw serves every metric, which keeps the
    metrics paired.
    """
    return np.random.default_rng(seed).integers(0, n_days, size=(n_boot, n_days))


def interval(components: Mapping[str, np.ndarray], statistic: Any, resamples: np.ndarray) -> tuple[float, float, float]:
    """``(estimate, low, high)``: the statistic of the sums of the per-day components, and its 95 % bootstrap interval.

    ``components`` are arrays with one value per day (sums over the stations of that day); ``statistic`` takes a dict of
    totals (floats, or arrays of the resamples) and returns the statistic from them.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        point = float(statistic({name: float(values.sum()) for name, values in components.items()}))
        totals = {name: values[resamples].sum(axis=1) for name, values in components.items()}
        draws = np.asarray(statistic(totals), dtype=float)
    draws = draws[np.isfinite(draws)]
    if not math.isfinite(point) or len(draws) < 10:
        return math.nan, math.nan, math.nan
    low, high = np.percentile(draws, [2.5, 97.5])
    return point, float(low), float(high)


def blend_members(values: np.ndarray, how: str) -> tuple[np.ndarray, np.ndarray]:
    """Mean or median over the members (axis 0) of the days where they exist, and how many members each day had.

    A day on which a model is missing is the mean of the models present; a day with no member at all stays ``nan``.
    """
    present = np.isfinite(values)
    counts = present.sum(axis=0)
    with np.errstate(all="ignore"):
        if how == "mean":
            blended = np.where(counts > 0, np.nansum(values, axis=0) / np.maximum(counts, 1), np.nan)
        elif how == "median":
            blended = np.nanmedian(np.where(present, values, np.nan), axis=0)
        else:
            raise ValueError(how)
    return blended, counts


def weighted_blend(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """The mean of the members weighted by ``weights`` (axis 0), renormalised over the members present that day."""
    present = np.isfinite(values)
    shape = (-1,) + (1,) * (values.ndim - 1)
    w = np.where(present, weights.reshape(shape), 0.0)
    total = w.sum(axis=0)
    with np.errstate(all="ignore"):
        out = np.where(total > 0, np.nansum(np.where(present, values, 0.0) * w, axis=0) / np.where(total > 0, total, 1.0), np.nan)
    return out


def learn_weights(errors_abs: np.ndarray) -> np.ndarray:
    """Weights proportional to 1 / MAE^2 of each member (axis 0 = member), from the errors of the learning half only."""
    flat = errors_abs.reshape(errors_abs.shape[0], -1)
    known = np.isfinite(flat)
    with np.errstate(all="ignore"):
        mae = np.where(known.sum(axis=1) > 0, np.where(known, flat, 0.0).sum(axis=1) / np.maximum(known.sum(axis=1), 1), np.nan)
    inverse = np.where(np.isfinite(mae) & (mae > 0), 1.0 / mae**2, 0.0)
    total = inverse.sum()
    return inverse / total if total > 0 else np.full(len(mae), 1.0 / len(mae))


def abs_error_components(forecast: np.ndarray, reference: np.ndarray, observed: np.ndarray, stations: np.ndarray) -> dict[str, np.ndarray]:
    """Per-day sums over the stations of ``stations`` of the absolute and signed errors of two forecasts, on common pairs.

    ``forecast`` and ``reference`` are ``(station, day)``. Only the station-days where both forecasts and the observation
    exist count, so the two are compared on the same days.
    """
    valid = np.isfinite(forecast) & np.isfinite(reference) & np.isfinite(observed) & stations[:, None]
    f = np.where(valid, forecast, 0.0)
    r = np.where(valid, reference, 0.0)
    o = np.where(valid, observed, 0.0)
    return {
        "n": valid.sum(axis=0).astype(float),
        "abs_f": np.abs(f - o).sum(axis=0),
        "abs_r": np.abs(r - o).sum(axis=0),
        "err_f": (f - o).sum(axis=0),
        "err_r": (r - o).sum(axis=0),
    }


def event_components(
    forecast: np.ndarray,
    observed: np.ndarray,
    valid: np.ndarray,
    threshold: float = RAIN_EVENT_MM,
    forecast_threshold: Optional[float] = None,
) -> dict[str, np.ndarray]:
    """Per-day counts of the 2 x 2 table of "rain of at least ``threshold`` mm" (hits, misses, false alarms, correct nulls).

    The forecast is read against ``forecast_threshold`` when one is given (it is ``threshold`` otherwise): see ``matching_threshold``.
    """
    f = np.where(valid, forecast, 0.0) >= (threshold if forecast_threshold is None else forecast_threshold)
    o = np.where(valid, observed, 0.0) >= threshold
    return {
        "hit": (f & o & valid).sum(axis=0).astype(float),
        "miss": (~f & o & valid).sum(axis=0).astype(float),
        "fa": (f & ~o & valid).sum(axis=0).astype(float),
        "cn": (~f & ~o & valid).sum(axis=0).astype(float),
    }


def matching_threshold(values: np.ndarray, reference: np.ndarray, event: float = RAIN_EVENT_MM) -> float:
    """The threshold at which ``values`` announces as many rainy days as ``reference`` does at ``event`` mm.

    A mean of forecasts spreads the rain over more days than any one of them, so at the same 1 mm it announces more rainy days
    and detects more of them for that reason alone. Reading the mean at the threshold where it announces as many days as
    best_match asks whether it ranks the days better, not whether it says "rain" more often. The threshold is set on the very
    days it is scored on (the optimism of that is small and favours the mean; it is said in the report).
    """
    wanted = int((reference >= event).sum())
    if wanted == 0 or wanted >= len(values):
        return event
    return float(np.sort(values)[::-1][wanted - 1])


def pod(t: Mapping[str, Any]) -> Any:
    """Probability of detection: the share of the rainy days (>= 1 mm) that were forecast."""
    return t["hit"] / (t["hit"] + t["miss"])


def far(t: Mapping[str, Any]) -> Any:
    """False alarm ratio: the share of the forecast rainy days that stayed under 1 mm."""
    return t["fa"] / (t["hit"] + t["fa"])


def csi(t: Mapping[str, Any]) -> Any:
    """Critical success index: hits over every day that was forecast or observed rainy."""
    return t["hit"] / (t["hit"] + t["miss"] + t["fa"])


def verdict(diff: float, low: float, high: float, useful: float, lower_is_better: bool = True) -> str:
    """The reading of a paired difference ``entry - best_match`` and its 95 % interval ``[low, high]``.

    ``better`` / ``worse`` need the interval to exclude 0 AND the estimate to reach the useful threshold: a gain that is
    certain but smaller than the threshold is ``better (below threshold)``, not a reason to change anything.
    ``lower_is_better`` is False for a score that should go up (the detection rate): the difference is read the other way.
    """
    if not all(math.isfinite(x) for x in (diff, low, high)):
        return "n/a"
    if not lower_is_better:
        diff, low, high = -diff, -high, -low
    if high < 0:
        return "better" if diff <= -useful else "better (below threshold)"
    if low > 0:
        return "worse" if diff >= useful else "worse (below threshold)"
    return "no difference shown"


# ---------------------------------------------------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------------------------------------------------

BLEND_MEAN = "mean"
BLEND_MEDIAN = "median"
BLEND_WEIGHTED = "weighted_mean"  # weights learned on the first half of the days, scored on the second half only
MEAN_SAME_FREQUENCY = "mean_same_rain_frequency"  # the mean read at the threshold where it announces as many rainy days as best_match
SHARE = "share_of_models"  # the share of the models that forecast 1 mm or more, read as a probability
USEFUL_EVENT_RATE = 0.02  # a change of 2 points of the detection rate / false alarm ratio / critical success index
USEFUL_BRIER = 0.01


def entry_series(dataset: Dataset, quantity: str, learn_days: Optional[np.ndarray]) -> dict[str, np.ndarray]:
    """Every competitor as an array ``(station, lead, day)``: best_match, the grid one, each model and the blends.

    ``learn_days`` are the indices of the days the weights of ``weighted_mean`` are learned on (none: no weighted blend).
    """
    fc = dataset.fc[quantity]
    series = {entry: fc[:, ENTRIES.index(entry), :, :] for entry in ENTRIES}
    members = np.stack([series[model] for model in MODELS])  # (model, station, lead, day)
    series[BLEND_MEAN], _ = blend_members(members, "mean")
    series[BLEND_MEDIAN], _ = blend_members(members, "median")
    if learn_days is not None:
        weighted = np.full(series[BLEND_MEAN].shape, math.nan)
        for l_index in range(len(LEADS)):
            errors = np.abs(members[:, :, l_index, :][:, :, learn_days] - dataset.obs[quantity][None, :, learn_days])
            weighted[:, l_index, :] = weighted_blend(members[:, :, l_index, :], learn_weights(errors))
        series[BLEND_WEIGHTED] = weighted
    return series


def mae_interval(components: Mapping[str, np.ndarray], key: str, resamples: np.ndarray) -> tuple[float, float, float]:
    return interval({"s": components[key], "n": components["n"]}, lambda t: t["s"] / t["n"], resamples)


def score_rows(
    dataset: Dataset,
    series_by_quantity: Mapping[str, Mapping[str, np.ndarray]],
    days: np.ndarray,
    period: str,
    resamples: np.ndarray,
) -> list[dict[str, Any]]:
    """MAE, bias and the paired difference with ``best_match`` for every quantity, lead, subset and competitor."""
    rows: list[dict[str, Any]] = []
    for quantity in QUANTITIES:
        series = series_by_quantity[quantity]
        observed = dataset.obs[quantity][:, days]
        for subset in SUBSETS:
            stations = dataset.station_mask(subset, quantity)
            for l_index, lead in enumerate(LEADS):
                reference = series[BEST_MATCH][:, l_index, :][:, days]
                for entry, values in series.items():
                    if entry == BEST_MATCH:
                        continue
                    comps = abs_error_components(values[:, l_index, :][:, days], reference, observed, stations)
                    n = float(comps["n"].sum())
                    if n == 0:
                        continue
                    mae_f = mae_interval(comps, "abs_f", resamples)
                    mae_r = mae_interval(comps, "abs_r", resamples)
                    diff = interval(comps, lambda t: (t["abs_f"] - t["abs_r"]) / t["n"], resamples)
                    rows.append(
                        {
                            "period": period,
                            "quantity": quantity,
                            "subset": subset,
                            "lead": lead,
                            "entry": entry,
                            "n_pairs": int(n),
                            "mae": mae_f[0],
                            "mae_low": mae_f[1],
                            "mae_high": mae_f[2],
                            "bias": float(comps["err_f"].sum() / n),
                            "best_match_mae": mae_r[0],
                            "best_match_mae_low": mae_r[1],
                            "best_match_mae_high": mae_r[2],
                            "best_match_bias": float(comps["err_r"].sum() / n),
                            "diff": diff[0],
                            "diff_low": diff[1],
                            "diff_high": diff[2],
                            "useful_threshold": USEFUL_GAIN[quantity],
                            "verdict": verdict(diff[0], diff[1], diff[2], USEFUL_GAIN[quantity]),
                        }
                    )
    return rows


def share_of_models(series: Mapping[str, np.ndarray]) -> np.ndarray:
    """The share of the models that forecast ``RAIN_EVENT_MM`` or more, among those that forecast at all (``(station, lead, day)``)."""
    members = np.stack([series[model] for model in MODELS])
    finite = np.isfinite(members)
    count = finite.sum(axis=0)
    with np.errstate(all="ignore"):
        return np.where(count > 0, ((members >= RAIN_EVENT_MM) & finite).sum(axis=0) / np.maximum(count, 1), np.nan)


def event_rows(
    dataset: Dataset,
    series_by_quantity: Mapping[str, Mapping[str, np.ndarray]],
    days: np.ndarray,
    period: str,
    resamples: np.ndarray,
) -> list[dict[str, Any]]:
    """Detection of the days with 1 mm or more of rain (hit rate, false alarm ratio, critical success index) and Brier score."""
    rows: list[dict[str, Any]] = []
    series = series_by_quantity["rr"]
    observed = dataset.obs["rr"][:, days]
    share = share_of_models(series)
    for subset in SUBSETS:
        stations = dataset.station_mask(subset, "rr")
        for l_index, lead in enumerate(LEADS):
            reference = series[BEST_MATCH][:, l_index, :][:, days]
            candidates = [(entry, values[:, l_index, :][:, days], RAIN_EVENT_MM) for entry, values in series.items() if entry != BEST_MATCH]
            mean_days = series[BLEND_MEAN][:, l_index, :][:, days]
            both = np.isfinite(mean_days) & np.isfinite(reference) & np.isfinite(observed) & stations[:, None]
            if both.any():
                candidates.append((MEAN_SAME_FREQUENCY, mean_days, matching_threshold(mean_days[both], reference[both])))
            for entry, forecast, forecast_threshold in candidates:
                valid = np.isfinite(forecast) & np.isfinite(reference) & np.isfinite(observed) & stations[:, None]
                if not valid.any():
                    continue
                comps = {f"f_{k}": v for k, v in event_components(forecast, observed, valid, forecast_threshold=forecast_threshold).items()}
                comps |= {f"r_{k}": v for k, v in event_components(reference, observed, valid).items()}

                def pick(totals: Mapping[str, Any], prefix: str) -> dict[str, Any]:
                    return {k: totals[f"{prefix}_{k}"] for k in ("hit", "miss", "fa", "cn")}

                row: dict[str, Any] = {
                    "period": period,
                    "subset": subset,
                    "lead": lead,
                    "entry": entry,
                    "n_pairs": int(valid.sum()),
                    "observed_rain_days": int(comps["f_hit"].sum() + comps["f_miss"].sum()),
                    "forecast_threshold_mm": forecast_threshold,
                }
                for name, function, lower_is_better in (("pod", pod, False), ("far", far, True), ("csi", csi, False)):
                    mine = interval(comps, lambda t, f=function: f(pick(t, "f")), resamples)
                    theirs = interval(comps, lambda t, f=function: f(pick(t, "r")), resamples)
                    delta = interval(comps, lambda t, f=function: f(pick(t, "f")) - f(pick(t, "r")), resamples)
                    row[name] = mine[0]
                    row[f"best_match_{name}"] = theirs[0]
                    row[f"d_{name}"] = delta[0]
                    row[f"d_{name}_low"] = delta[1]
                    row[f"d_{name}_high"] = delta[2]
                    row[f"{name}_verdict"] = verdict(delta[0], delta[1], delta[2], USEFUL_EVENT_RATE, lower_is_better)
                rows.append(row)
        # Brier score: the share of the models read as a probability, against the yes/no forecast of best_match.
        for l_index, lead in enumerate(LEADS):
            reference = series[BEST_MATCH][:, l_index, :][:, days]
            probability = share[:, l_index, :][:, days]
            valid = np.isfinite(probability) & np.isfinite(reference) & np.isfinite(observed) & stations[:, None]
            if not valid.any():
                continue
            happened = (np.where(valid, observed, 0.0) >= RAIN_EVENT_MM).astype(float)
            said_yes = (np.where(valid, reference, 0.0) >= RAIN_EVENT_MM).astype(float)
            comps = {
                "n": valid.sum(axis=0).astype(float),
                "p": ((np.where(valid, probability, 0.0) - happened) ** 2 * valid).sum(axis=0),
                "r": ((said_yes - happened) ** 2 * valid).sum(axis=0),
            }
            p_score = interval(comps, lambda t: t["p"] / t["n"], resamples)
            r_score = interval(comps, lambda t: t["r"] / t["n"], resamples)
            delta = interval(comps, lambda t: (t["p"] - t["r"]) / t["n"], resamples)
            rows.append(
                {
                    "period": period,
                    "subset": subset,
                    "lead": lead,
                    "entry": SHARE,
                    "n_pairs": int(valid.sum()),
                    "brier": p_score[0],
                    "best_match_brier": r_score[0],
                    "d_brier": delta[0],
                    "d_brier_low": delta[1],
                    "d_brier_high": delta[2],
                    "brier_verdict": verdict(delta[0], delta[1], delta[2], USEFUL_BRIER),
                }
            )
    return rows


def coverage_rows(dataset: Dataset) -> list[dict[str, Any]]:
    """How many station-days each competitor has a value for (a missing one is a model that does not reach the lead)."""
    rows = []
    for quantity in QUANTITIES:
        known_obs = np.isfinite(dataset.obs[quantity])
        for l_index, lead in enumerate(LEADS):
            for e_index, entry in enumerate(ENTRIES):
                values = dataset.fc[quantity][:, e_index, l_index, :]
                rows.append(
                    {
                        "quantity": quantity,
                        "lead": lead,
                        "entry": entry,
                        "station_days_with_observation": int(known_obs.sum()),
                        "station_days_with_forecast": int((np.isfinite(values) & known_obs).sum()),
                    }
                )
    return rows


def member_rows(dataset: Dataset) -> list[dict[str, Any]]:
    """The number of models behind the mean on each station-day with an observation, by lead (temperature maxima)."""
    rows = []
    members = np.stack([dataset.fc["tx"][:, ENTRIES.index(model), :, :] for model in MODELS])
    known_obs = np.isfinite(dataset.obs["tx"])
    count = np.isfinite(members).sum(axis=0)  # (station, lead, day)
    for l_index, lead in enumerate(LEADS):
        counts = count[:, l_index, :][known_obs]
        rows.append(
            {
                "lead": lead,
                "station_days": int(known_obs.sum()),
                "mean_members": float(counts.mean()),
                "min_members": int(counts.min()),
                "max_members": int(counts.max()),
                "station_days_with_fewer_than_all_models": int((counts < len(MODELS)).sum()),
                "station_days_with_fewer_members_than_the_lead_usually_has": int((counts < counts.max()).sum()),
            }
        )
    return rows


def station_rows(dataset: Dataset, series_by_quantity: Mapping[str, Mapping[str, np.ndarray]]) -> list[dict[str, Any]]:
    """MAE and bias of best_match and of the mean for each station and lead (no interval: one station has few days)."""
    rows = []
    for quantity in QUANTITIES:
        observed = dataset.obs[quantity]
        for s_index, station in enumerate(dataset.stations):
            for l_index, lead in enumerate(LEADS):
                row: dict[str, Any] = {"quantity": quantity, "station": station.key, "name": station.name, "group": station.group, "lead": lead}
                for entry in (BEST_MATCH, BLEND_MEAN):
                    values = series_by_quantity[quantity][entry][s_index, l_index, :]
                    valid = np.isfinite(values) & np.isfinite(observed[s_index])
                    row[f"{entry}_mae"] = float(np.abs(values[valid] - observed[s_index][valid]).mean()) if valid.any() else math.nan
                    row[f"{entry}_bias"] = float((values[valid] - observed[s_index][valid]).mean()) if valid.any() else math.nan
                    row["n"] = int(valid.sum())
                rows.append(row)
    return rows


def paired_mae_difference(forecast: np.ndarray, reference: np.ndarray, observed: np.ndarray, stations: np.ndarray) -> float:
    """MAE of ``forecast`` minus MAE of ``reference`` over the common pairs of the chosen stations (``nan`` if there are none)."""
    comps = abs_error_components(forecast, reference, observed, stations)
    total = comps["n"].sum()
    return float((comps["abs_f"].sum() - comps["abs_r"].sum()) / total) if total else math.nan


def robustness_rows(dataset: Dataset, series_by_quantity: Mapping[str, Mapping[str, np.ndarray]]) -> list[dict[str, Any]]:
    """Is the gain of the mean the work of one station? Leave each station out in turn and count the stations it wins at.

    ``diff`` is the paired difference of the MAE of the mean and of best_match over all the stations; ``leave_one_out_*``
    are the smallest and the largest value it takes when one station is left out (and which one); ``stations_better`` is the
    number of stations where the mean has the lower MAE.
    """
    rows = []
    everyone = np.ones(len(dataset.stations), dtype=bool)
    for quantity in QUANTITIES:
        observed = dataset.obs[quantity]
        series = series_by_quantity[quantity]
        for l_index, lead in enumerate(LEADS):
            mean = series[BLEND_MEAN][:, l_index, :]
            reference = series[BEST_MATCH][:, l_index, :]
            leave_out = {}
            wins = total = 0
            for s_index, station in enumerate(dataset.stations):
                without = everyone.copy()
                without[s_index] = False
                leave_out[station.key] = paired_mae_difference(mean, reference, observed, without)
                alone = np.zeros(len(dataset.stations), dtype=bool)
                alone[s_index] = True
                comps = abs_error_components(mean, reference, observed, alone)
                if comps["n"].sum():
                    total += 1
                    wins += int(comps["abs_f"].sum() < comps["abs_r"].sum())
            finite = {k: v for k, v in leave_out.items() if math.isfinite(v)}
            low_key = min(finite, key=finite.get)
            high_key = max(finite, key=finite.get)
            rows.append(
                {
                    "quantity": quantity,
                    "lead": lead,
                    "diff": paired_mae_difference(mean, reference, observed, everyone),
                    "leave_one_out_min": finite[low_key],
                    "leave_one_out_min_station_left_out": low_key,
                    "leave_one_out_max": finite[high_key],
                    "leave_one_out_max_station_left_out": high_key,
                    "stations_better": wins,
                    "stations_total": total,
                }
            )
    return rows


def score_dataset(dataset: Dataset) -> dict[str, Any]:
    """Every table of the report, from a data set. ``second_half`` is where the learned weights are tried."""
    n_days = len(dataset.days)
    half = n_days // 2
    learn_days = np.arange(0, half)
    test_days = np.arange(half, n_days)
    all_days = np.arange(n_days)
    series_all = {q: entry_series(dataset, q, None) for q in QUANTITIES}
    series_learned = {q: entry_series(dataset, q, learn_days) for q in QUANTITIES}
    resamples_all = make_resamples(len(all_days))
    resamples_half = make_resamples(len(test_days))
    return {
        "mae": score_rows(dataset, series_all, all_days, "all_days", resamples_all)
        + score_rows(dataset, series_learned, test_days, "second_half", resamples_half),
        "rain_events": event_rows(dataset, series_all, all_days, "all_days", resamples_all)
        + event_rows(dataset, series_learned, test_days, "second_half", resamples_half),
        "coverage": coverage_rows(dataset),
        "members": member_rows(dataset),
        "by_station": station_rows(dataset, series_all),
        "robustness": robustness_rows(dataset, series_all),
        "split": {
            "learning_days": [dataset.days[0].isoformat(), dataset.days[half - 1].isoformat()],
            "test_days": [dataset.days[half].isoformat(), dataset.days[-1].isoformat()],
        },
    }


# ---------------------------------------------------------------------------------------------------------------------
# How much the hourly sampling hides of the extremes (France only)
# ---------------------------------------------------------------------------------------------------------------------

# Three stations whose hourly file is read: the forecasts give the temperature at the hour, the observation is the true
# extreme of the day, and the difference of the two is a bias every competitor shares.
SAMPLING_STATIONS = ("75114001", "69029001", "31069001")


def mf_hourly_url(dept: str) -> str:
    return f"{MF_BASE}/HOR/H_{dept}_latest-2025-2026.csv.gz"


# The columns of an hourly file that stand for the four daily quantities (the extreme, the sum or the maximum inside the hour).
HOURLY_COLUMNS = {"tx": "TX", "tn": "TN", "rr": "RR1", "ws": "FXY"}


def parse_mf_hourly(raw: bytes, num_poste: str) -> dict[datetime, dict[str, float]]:
    """``{UTC hour: {t, tx, tn, rr, ws}}`` of one station in an hourly file of Meteo-France.

    The label of an hour is the END of the hour it describes: TX, TN, RR1 and FXY of the label H are the extreme, the sum and
    the maximum over (H-1, H]; ``t`` is the instantaneous temperature at H. A value with quality code 2 is dropped.
    """
    text = gzip.decompress(raw).decode("utf-8")
    out: dict[datetime, dict[str, float]] = {}
    columns = {"t": "T", **HOURLY_COLUMNS}
    for row in csv.DictReader(io.StringIO(text), delimiter=";"):
        if row["NUM_POSTE"] != num_poste:
            continue
        stamp = row["AAAAMMJJHH"]
        values = {}
        for key, column in columns.items():
            value = (row.get(column) or "").strip()
            values[key] = float(value) if value and (row.get("Q" + column) or "").strip() != "2" else math.nan
        out[datetime(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8]), int(stamp[8:10]), tzinfo=UTC)] = values
    return out


def parse_mf_hourly_temperature(raw: bytes, num_poste: str) -> dict[datetime, float]:
    """``{UTC hour: temperature (degC) at that hour}`` of one station in an hourly file of Meteo-France (``T``, instantaneous)."""
    return {moment: values["t"] for moment, values in parse_mf_hourly(raw, num_poste).items() if math.isfinite(values["t"])}


def sampling_bias(hourly: Mapping[datetime, float], station: Station, days: Sequence[date], tx: np.ndarray, tn: np.ndarray) -> dict[str, Any]:
    """Mean of (true TX - maximum of the hourly temperatures of its window) and of (minimum of the hourly ones - true TN).

    Both are positive when the hourly sampling hides part of the extreme. ``tx`` and ``tn`` are the daily observations.
    """
    high, low = [], []
    for index, day in enumerate(days):
        for quantity, observed, bucket in (("tx", tx[index], high), ("tn", tn[index], low)):
            start, end = window_bounds(station, quantity, day)
            values = [hourly[h] for h in window_hours(start, end, accumulated=False) if h in hourly]
            if len(values) == int((end - start).total_seconds() // 3600) and math.isfinite(observed):
                bucket.append(observed - max(values) if quantity == "tx" else min(values) - observed)
    return {
        "n_days": min(len(high), len(low)),
        "true_tx_minus_hourly_max_mean": float(np.mean(high)) if high else math.nan,
        "hourly_min_minus_true_tn_mean": float(np.mean(low)) if low else math.nan,
    }


def windows_check(hourly: Mapping[datetime, Mapping[str, float]], station: Station, days: Sequence[date], obs: Mapping[str, np.ndarray], index: int) -> dict[str, Any]:
    """Rebuild the daily values of Meteo-France from its hourly values and compare: which window is the daily value made of?

    For each quantity: the mean absolute difference between the daily file and the value rebuilt over the window this tool uses
    (``official``), and over the UTC calendar day (``utc_day``), which is what one would take without looking. 0.000 for the
    first means the window is the right one.
    """
    out: dict[str, Any] = {}
    for quantity in QUANTITIES:
        gaps: dict[str, list[float]] = {"official": [], "utc_day": []}
        for d_index, day in enumerate(days):
            observed = obs[quantity][index, d_index]
            if not math.isfinite(observed):
                continue
            windows = {
                "official": window_bounds(station, quantity, day),
                "utc_day": (utc_midnight(day), utc_midnight(day) + timedelta(hours=24)),
            }
            for name, (start, end) in windows.items():
                values = np.array([hourly.get(h, {}).get(quantity, math.nan) for h in window_hours(start, end, accumulated=True)])
                rebuilt = fold_values(values, quantity, min_coverage=1.0)
                if math.isfinite(rebuilt):
                    gaps[name].append(abs(rebuilt - observed))
        out[quantity] = {
            "days": len(gaps["official"]),
            "official_mean_abs_difference": float(np.mean(gaps["official"])) if gaps["official"] else math.nan,
            "utc_day_mean_abs_difference": float(np.mean(gaps["utc_day"])) if gaps["utc_day"] else math.nan,
        }
    return out


def load_hourly_checks(source: Source, dataset_stations: Sequence[Station], days: Sequence[date], obs: Mapping[str, np.ndarray]) -> dict[str, Any]:
    """The two checks that read the hourly files of Meteo-France (downloaded the first time): the sampling bias and the windows."""
    sampling: dict[str, Any] = {}
    windows: dict[str, Any] = {}
    for index, station in enumerate(dataset_stations):
        if station.key not in SAMPLING_STATIONS:
            continue
        try:
            raw = source.download("mf", f"H_{station.mf_dept}.csv.gz", mf_hourly_url(station.mf_dept))
        except BudgetExceeded:
            continue  # not downloaded: the tables are left out, not guessed
        hourly = parse_mf_hourly(raw, station.key)
        temperatures = {moment: v["t"] for moment, v in hourly.items() if math.isfinite(v["t"])}
        sampling[station.key] = {"name": station.name} | sampling_bias(temperatures, station, days, obs["tx"][index], obs["tn"][index])
        windows[station.key] = {"name": station.name} | windows_check(hourly, station, days, obs, index)
    return {"sampling": sampling, "windows_check": windows}


# ---------------------------------------------------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------------------------------------------------


def clean(value: Any) -> Any:
    """JSON-ready: ``nan`` becomes ``null``, floats are rounded to 4 decimals."""
    if isinstance(value, float):
        return round(value, 4) if math.isfinite(value) else None
    if isinstance(value, (np.floating, np.integer)):
        return clean(value.item())
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def fmt(value: Optional[float], digits: int = 2) -> str:
    return "n/a" if value is None or not math.isfinite(value) else f"{value:.{digits}f}"


def signed(value: Optional[float], digits: int = 2) -> str:
    return "n/a" if value is None or not math.isfinite(value) else f"{value:+.{digits}f}"


def render_summary(results: Mapping[str, Any]) -> str:
    """The tables a person reads: MAE of best_match and of the mean at each lead, with intervals and the reading."""
    lines: list[str] = []
    rows = [r for r in results["mae"] if r["period"] == "all_days"]
    for quantity in QUANTITIES:
        lines.append(f"\n== {QUANTITY_LABEL[quantity]} (MAE; [95 % interval]; difference with best_match) ==")
        for subset in SUBSETS:
            lines.append(f"-- {subset}")
            lines.append(f"{'lead':>4} {'n':>5} {'best_match':>22} {'mean':>22} {'diff(mean)':>24} {'reading':<26} {'median diff':>24}")
            for lead in LEADS:
                mean = next((r for r in rows if r["quantity"] == quantity and r["subset"] == subset and r["lead"] == lead and r["entry"] == BLEND_MEAN), None)
                median = next((r for r in rows if r["quantity"] == quantity and r["subset"] == subset and r["lead"] == lead and r["entry"] == BLEND_MEDIAN), None)
                if mean is None:
                    continue
                bm = f"{fmt(mean['best_match_mae'])} [{fmt(mean['best_match_mae_low'])},{fmt(mean['best_match_mae_high'])}]"
                mn = f"{fmt(mean['mae'])} [{fmt(mean['mae_low'])},{fmt(mean['mae_high'])}]"
                df = f"{signed(mean['diff'])} [{signed(mean['diff_low'])},{signed(mean['diff_high'])}]"
                md = f"{signed(median['diff'])} [{signed(median['diff_low'])},{signed(median['diff_high'])}]" if median else "n/a"
                lines.append(f"{lead:>4} {mean['n_pairs']:>5} {bm:>22} {mn:>22} {df:>24} {mean['verdict']:<26} {md:>24}")
    lines.append("\n== rain events (>= 1 mm): detection / false alarm ratio, mean against best_match ==")
    for subset in SUBSETS:
        lines.append(f"-- {subset}")
        for lead in LEADS:
            r = next((x for x in results["rain_events"] if x["period"] == "all_days" and x["subset"] == subset and x["lead"] == lead and x["entry"] == BLEND_MEAN), None)
            if r is None:
                continue
            lines.append(
                f"{lead:>4} n={r['n_pairs']:<5} rainy={r['observed_rain_days']:<4} "
                f"POD {fmt(r['pod'])} vs {fmt(r['best_match_pod'])} ({signed(r['d_pod'])} [{signed(r['d_pod_low'])},{signed(r['d_pod_high'])}] {r['pod_verdict']}) | "
                f"FAR {fmt(r['far'])} vs {fmt(r['best_match_far'])} ({signed(r['d_far'])} [{signed(r['d_far_low'])},{signed(r['d_far_high'])}] {r['far_verdict']})"
            )
    split = results["split"]
    lines.append(
        f"\n== weights learned on {split['learning_days'][0]}..{split['learning_days'][1]}, "
        f"scored on {split['test_days'][0]}..{split['test_days'][1]} only (all stations): MAE of the mean, of the weighted mean, "
        "and difference of the weighted mean with best_match =="
    )
    half = [r for r in results["mae"] if r["period"] == "second_half" and r["subset"] == "all"]
    for quantity in QUANTITIES:
        for lead in LEADS:
            plain = next((r for r in half if r["quantity"] == quantity and r["lead"] == lead and r["entry"] == BLEND_MEAN), None)
            learned = next((r for r in half if r["quantity"] == quantity and r["lead"] == lead and r["entry"] == BLEND_WEIGHTED), None)
            if plain is None or learned is None:
                continue
            lines.append(
                f"  {quantity} J+{lead}: best_match {fmt(plain['best_match_mae'])} | mean {fmt(plain['mae'])} ({signed(plain['diff'])} {plain['verdict']}) | "
                f"weighted {fmt(learned['mae'])} ({signed(learned['diff'])} [{signed(learned['diff_low'])},{signed(learned['diff_high'])}] {learned['verdict']})"
            )
    lines.append("\n== is the gain of the mean the work of one station? (MAE of the mean - MAE of best_match, all stations; leave-one-out range; stations won) ==")
    for row in results["robustness"]:
        lines.append(
            f"  {row['quantity']} J+{row['lead']}: {signed(row['diff'])}  leave-one-out [{signed(row['leave_one_out_min'])} without {row['leave_one_out_min_station_left_out']}, "
            f"{signed(row['leave_one_out_max'])} without {row['leave_one_out_max_station_left_out']}]  mean wins at {row['stations_better']} of {row['stations_total']} stations"
        )
    if results.get("sampling"):
        lines.append("\n== what the hourly sampling hides of the extremes (France, from the hourly files of Meteo-France) ==")
        for row in results["sampling"].values():
            lines.append(
                f"  {row['name']}: true TX - hourly maximum {signed(row['true_tx_minus_hourly_max_mean'])} degC, "
                f"hourly minimum - true TN {signed(row['hourly_min_minus_true_tn_mean'])} degC ({row['n_days']} days)"
            )
    if results.get("windows_check"):
        lines.append("\n== the daily values of Meteo-France rebuilt from its hourly values: mean absolute difference over the window used / over the UTC day ==")
        for row in results["windows_check"].values():
            cells = ", ".join(f"{q} {fmt(row[q]['official_mean_abs_difference'], 3)} / {fmt(row[q]['utc_day_mean_abs_difference'], 3)}" for q in QUANTITIES)
            lines.append(f"  {row['name']} ({row['tx']['days']} days): {cells}")
    lines.append("\n== how many models are behind the mean (temperature maxima) ==")
    for row in results["members"]:
        lines.append(
            f"  J+{row['lead']}: mean {row['mean_members']:.2f} of {len(MODELS)} models, "
            f"{row['station_days_with_fewer_than_all_models']} of {row['station_days']} station-days with fewer than all"
        )
    return "\n".join(lines)


def ci(value: Optional[float], low: Optional[float], high: Optional[float], digits: int = 2, plus: bool = False) -> str:
    text = signed(value, digits) if plus else fmt(value, digits)
    lo = signed(low, digits) if plus else fmt(low, digits)
    hi = signed(high, digits) if plus else fmt(high, digits)
    return f"{text} [{lo}, {hi}]"


def md_table(header: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def render_markdown(results: Mapping[str, Any]) -> str:
    """The tables of the report, in Markdown, from the results (the report quotes them as they are)."""
    days = [r for r in results["mae"] if r["period"] == "all_days"]
    half = [r for r in results["mae"] if r["period"] == "second_half"]

    def find(rows: Sequence[Mapping[str, Any]], quantity: str, subset: str, lead: int, entry: str) -> Optional[Mapping[str, Any]]:
        return next((r for r in rows if r["quantity"] == quantity and r["subset"] == subset and r["lead"] == lead and r["entry"] == entry), None)

    out: list[str] = []
    for quantity in ("tx", "tn"):
        out.append(f"\n### {QUANTITY_LABEL[quantity]}: best_match against the mean of the {len(MODELS)} models, all stations\n")
        rows = []
        for lead in LEADS:
            mean = find(days, quantity, "all", lead, BLEND_MEAN)
            median = find(days, quantity, "all", lead, BLEND_MEDIAN)
            if mean is None or median is None:
                continue
            rows.append(
                [
                    f"J+{lead}",
                    str(mean["n_pairs"]),
                    ci(mean["best_match_mae"], mean["best_match_mae_low"], mean["best_match_mae_high"]),
                    ci(mean["mae"], mean["mae_low"], mean["mae_high"]),
                    ci(mean["diff"], mean["diff_low"], mean["diff_high"], plus=True),
                    mean["verdict"],
                    ci(median["diff"], median["diff_low"], median["diff_high"], plus=True),
                    f"{signed(mean['best_match_bias'])} / {signed(mean['bias'])}",
                ]
            )
        out.append(md_table(["Lead", "Pairs", "MAE best_match", "MAE mean", "Mean - best_match", "Reading", "Median - best_match", "Bias best_match / mean"], rows))
    for quantity in ("tx", "tn", "rr", "ws"):
        out.append(f"\n### {QUANTITY_LABEL[quantity]}: mean minus best_match (MAE), France and the world apart\n")
        rows = []
        for lead in LEADS:
            cells = [f"J+{lead}"]
            for subset in SUBSETS:
                mean = find(days, quantity, subset, lead, BLEND_MEAN)
                cells.append("n/a" if mean is None else f"{ci(mean['diff'], mean['diff_low'], mean['diff_high'], plus=True)} {mean['verdict']} (n={mean['n_pairs']})")
            rows.append(cells)
        out.append(md_table(["Lead", "All stations", "France", "World"], rows))
    out.append("\n### Rain days of 1 mm or more, the mean against best_match (stations with a rain observation)\n")
    rows = []
    for subset in SUBSETS:
        for lead in LEADS:
            r = next((x for x in results["rain_events"] if x["period"] == "all_days" and x["subset"] == subset and x["lead"] == lead and x["entry"] == BLEND_MEAN), None)
            if r is None:
                continue
            fb = r["pod"] / (1 - r["far"]) if r["far"] < 1 else math.nan
            fb_ref = r["best_match_pod"] / (1 - r["best_match_far"]) if r["best_match_far"] < 1 else math.nan
            rows.append(
                [
                    subset,
                    f"J+{lead}",
                    f"{r['n_pairs']} / {r['observed_rain_days']}",
                    f"{fmt(r['best_match_pod'])} / {fmt(r['pod'])}",
                    f"{ci(r['d_pod'], r['d_pod_low'], r['d_pod_high'], plus=True)}",
                    f"{fmt(r['best_match_far'])} / {fmt(r['far'])}",
                    f"{ci(r['d_far'], r['d_far_low'], r['d_far_high'], plus=True)}",
                    f"{fmt(fb_ref)} / {fmt(fb)}",
                    f"{fmt(r['best_match_csi'])} / {fmt(r['csi'])}",
                ]
            )
    out.append(md_table(["Stations", "Lead", "Pairs / rainy days", "Detection best_match / mean", "Change", "False alarm ratio best_match / mean", "Change", "Rainy days forecast per observed, best_match / mean", "CSI best_match / mean"], rows))
    out.append("\n### The same, the mean read at the threshold where it announces as many rainy days as best_match (stations with a rain observation)\n")
    rows = []
    for subset in SUBSETS:
        for lead in LEADS:
            r = next((x for x in results["rain_events"] if x["period"] == "all_days" and x["subset"] == subset and x["lead"] == lead and x["entry"] == MEAN_SAME_FREQUENCY), None)
            if r is None:
                continue
            rows.append(
                [
                    subset,
                    f"J+{lead}",
                    fmt(r["forecast_threshold_mm"]),
                    f"{fmt(r['best_match_pod'])} / {fmt(r['pod'])}",
                    ci(r["d_pod"], r["d_pod_low"], r["d_pod_high"], plus=True),
                    f"{fmt(r['best_match_far'])} / {fmt(r['far'])}",
                    ci(r["d_far"], r["d_far_low"], r["d_far_high"], plus=True),
                    f"{fmt(r['best_match_csi'])} / {fmt(r['csi'])}",
                    f"{ci(r['d_csi'], r['d_csi_low'], r['d_csi_high'], plus=True)} {r['csi_verdict']}",
                ]
            )
    out.append(md_table(["Stations", "Lead", "Threshold of the mean (mm)", "Detection best_match / mean", "Change", "False alarm ratio best_match / mean", "Change", "CSI best_match / mean", "Change of CSI"], rows))
    out.append("\n### Brier score of a yes/no forecast of 1 mm (best_match) and of the share of the models that say yes\n")
    rows = []
    for lead in LEADS:
        r = next((x for x in results["rain_events"] if x["period"] == "all_days" and x["subset"] == "all" and x["lead"] == lead and x["entry"] == SHARE), None)
        if r is not None:
            rows.append([f"J+{lead}", str(r["n_pairs"]), fmt(r["best_match_brier"], 3), fmt(r["brier"], 3), ci(r["d_brier"], r["d_brier_low"], r["d_brier_high"], 3, plus=True)])
    out.append(md_table(["Lead", "Pairs", "Brier yes/no best_match", "Brier share of models", "Difference"], rows))
    for quantity in ("tx", "tn", "rr", "ws"):
        out.append(f"\n### {QUANTITY_LABEL[quantity]}: each competitor, MAE (bias), all stations\n")
        entries = [e for e in (*ENTRIES[1:], BLEND_MEAN, BLEND_MEDIAN)]
        rows = []
        bm_cells = ["best_match"]
        for lead in LEADS:
            ref = find(days, quantity, "all", lead, BLEND_MEAN)
            bm_cells.append("n/a" if ref is None else f"{fmt(ref['best_match_mae'])} ({signed(ref['best_match_bias'])})")
        rows.append(bm_cells)
        for entry in entries:
            cells = [entry]
            for lead in LEADS:
                r = find(days, quantity, "all", lead, entry)
                cells.append("n/a" if r is None else f"{fmt(r['mae'])} ({signed(r['bias'])}) {signed(r['diff'])}")
            rows.append(cells)
        out.append(md_table(["Competitor", *[f"J+{lead}" for lead in LEADS]], rows))
    out.append(f"\n### The grid: best_match asked at the point snapped to the {0.05} degree grid, minus best_match at the station (MAE)\n")
    rows = []
    for quantity in QUANTITIES:
        for subset in SUBSETS:
            cells = [quantity, subset]
            for lead in LEADS:
                r = find(days, quantity, subset, lead, GRID_ENTRY)
                cells.append("n/a" if r is None else ci(r["diff"], r["diff_low"], r["diff_high"], plus=True))
            rows.append(cells)
    out.append(md_table(["Quantity", "Stations", *[f"J+{lead}" for lead in LEADS]], rows))
    split = results["split"]
    out.append(f"\n### Weights learned on {split['learning_days'][0]} to {split['learning_days'][1]}, scored on {split['test_days'][0]} to {split['test_days'][1]} only (all stations)\n")
    rows = []
    for quantity in QUANTITIES:
        for lead in LEADS:
            plain = find(half, quantity, "all", lead, BLEND_MEAN)
            learned = find(half, quantity, "all", lead, BLEND_WEIGHTED)
            if plain is None or learned is None:
                continue
            rows.append(
                [
                    quantity,
                    f"J+{lead}",
                    fmt(plain["best_match_mae"]),
                    f"{fmt(plain['mae'])} ({signed(plain['diff'])})",
                    f"{fmt(learned['mae'])} ({ci(learned['diff'], learned['diff_low'], learned['diff_high'], plus=True)})",
                    learned["verdict"],
                ]
            )
    out.append(md_table(["Quantity", "Lead", "MAE best_match", "MAE mean (difference)", "MAE weighted mean (difference)", "Reading"], rows))
    out.append("\n### Is the gain of the mean the work of one station?\n")
    rows = [
        [
            row["quantity"],
            f"J+{row['lead']}",
            signed(row["diff"]),
            f"{signed(row['leave_one_out_min'])} (without {row['leave_one_out_min_station_left_out']}) to {signed(row['leave_one_out_max'])} (without {row['leave_one_out_max_station_left_out']})",
            f"{row['stations_better']} of {row['stations_total']}",
        ]
        for row in results["robustness"]
    ]
    out.append(md_table(["Quantity", "Lead", "Mean - best_match", "Leaving one station out", "Stations where the mean has the lower MAE"], rows))
    out.append("\n### How many models are behind the mean\n")
    rows = [
        [f"J+{r['lead']}", f"{fmt(r['mean_members'])} of {len(MODELS)}", f"{r['station_days_with_fewer_than_all_models']} of {r['station_days']}"]
        for r in results["members"]
    ]
    out.append(md_table(["Lead", "Models in the mean (average)", "Station-days with fewer than all the models"], rows))
    if results.get("sampling"):
        out.append("\n### What the hourly sampling hides of the daily extremes (Meteo-France hourly files)\n")
        rows = [
            [row["name"], str(row["n_days"]), signed(row["true_tx_minus_hourly_max_mean"]), signed(row["hourly_min_minus_true_tn_mean"])]
            for row in results["sampling"].values()
        ]
        out.append(md_table(["Station", "Days", "True TX minus hourly maximum (degC)", "Hourly minimum minus true TN (degC)"], rows))
    if results.get("windows_check"):
        out.append("\n### The daily file of Meteo-France rebuilt from its hourly file (mean absolute difference, over the window used by the tool / over the UTC calendar day)\n")
        rows = []
        for row in results["windows_check"].values():
            rows.append([row["name"], str(row["tx"]["days"])] + [f"{fmt(row[q]['official_mean_abs_difference'], 3)} / {fmt(row[q]['utc_day_mean_abs_difference'], 3)}" for q in QUANTITIES])
        out.append(md_table(["Station", "Days", "TX (degC)", "TN (degC)", "RR (mm)", "FXY (m/s)"], rows))
    out.append("\n### Stations: MAE of best_match and of the mean (TX and TN)\n")
    rows = []
    for station in sorted({r["station"] for r in results["by_station"]}):
        cells_name = next(r for r in results["by_station"] if r["station"] == station)
        cells = [f"{station} {cells_name['name']}"]
        for quantity in ("tx", "tn"):
            for lead in (1, 3, 5):
                r = next(x for x in results["by_station"] if x["station"] == station and x["quantity"] == quantity and x["lead"] == lead)
                cells.append(f"{fmt(r['best_match_mae'])} / {fmt(r['mean_mae'])}")
        rows.append(cells)
    out.append(md_table(["Station", "TX J+1", "TX J+3", "TX J+5", "TN J+1", "TN J+3", "TN J+5"], rows))
    return "\n".join(out) + "\n"


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in keys})


# ---------------------------------------------------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------------------------------------------------


def request_days(args: argparse.Namespace) -> tuple[date, date]:
    last = date.fromisoformat(args.end_date)
    return last - timedelta(days=args.days - 1), last


def scored_days(args: argparse.Namespace) -> tuple[date, date]:
    first, last = request_days(args)
    return first + timedelta(days=1), last - timedelta(days=1)


def station_requests(args: argparse.Namespace, stations: Sequence[Station]) -> list[tuple[str, dict[str, str]]]:
    first, last = request_days(args)
    requests = []
    for station in stations:
        requests.append((f"{station.key} main", forecast_params(station.lat, station.lon, first, last, (BEST_MATCH, *MODELS))))
        grid_lat, grid_lon = grid_point(station)
        requests.append((f"{station.key} grid", forecast_params(grid_lat, grid_lon, first, last, None)))
    return requests


def selected_stations(args: argparse.Namespace) -> list[Station]:
    if not args.stations:
        return list(STATIONS)
    wanted = set(args.stations)
    chosen = [s for s in STATIONS if s.key in wanted]
    unknown = wanted - {s.key for s in chosen}
    if unknown:
        raise SystemExit(f"unknown station(s): {', '.join(sorted(unknown))}")
    return chosen


def make_source(args: argparse.Namespace, dry_run: bool = False) -> Source:
    return Source(
        cache_dir=Path(args.cache_dir),
        max_weight=args.max_weight,
        max_weight_per_minute=args.max_per_minute,
        refresh=False,
        refresh_obs=args.refresh_obs,
        dry_run=dry_run,
    )


def production_cost() -> dict[str, Any]:
    """What the daily request of the API weighs today and what asking the eight models as well would weigh.

    The refresh of the daily forecast is every ``DAILY_TTL_SECONDS`` for each cell of the forecast grid.
    """
    current = OpenMeteoCollector.request_params("daily", 48.8, 2.3)
    # The models are compared on the daily variables only: the hourly cloud cover the request also carries (for the aurora) is left
    # out of the hypotheses; the request of today is weighed as it is sent.
    daily_only = {k: v for k, v in current.items() if k not in ("hourly", "forecast_hours")}
    measured = ["temperature_2m_max", "temperature_2m_min", "precipitation_sum", "wind_speed_10m_max"]
    models = ",".join(MODELS)
    temperatures = {**daily_only, "daily": "temperature_2m_max,temperature_2m_min", "models": models}
    only_measured = {**daily_only, "daily": ",".join(measured), "models": models}
    all_daily = {**daily_only, "models": models}
    refreshes_a_day = 86400 // OpenMeteoCollector.DAILY_TTL_SECONDS
    weights = {
        "daily_request_today": request_weight(current),
        "eight_models_for_the_two_temperatures_only": request_weight(temperatures),
        "eight_models_for_the_four_measured_variables_only": request_weight(only_measured),
        "eight_models_for_all_seven_daily_variables": request_weight(all_daily),
    }
    weights["today_plus_two_temperatures_of_eight_models"] = weights["daily_request_today"] + weights["eight_models_for_the_two_temperatures_only"]
    weights["today_plus_four_variables_of_eight_models"] = weights["daily_request_today"] + weights["eight_models_for_the_four_measured_variables_only"]
    return {
        "forecast_days": int(current["forecast_days"]),
        "refreshes_a_day_per_cell": refreshes_a_day,
        "weighted_calls_per_refresh": weights,
        "weighted_calls_a_day_per_cell": {name: round(weight * refreshes_a_day, 2) for name, weight in weights.items()},
        "daily_quota_of_the_free_api": 10000,
    }


def command_plan(args: argparse.Namespace) -> int:
    stations = selected_stations(args)
    source = make_source(args, dry_run=True)
    first, last = request_days(args)
    total = 0.0
    pending = 0.0
    print(f"period requested {first} to {last} ({args.days} days); days scored {scored_days(args)[0]} to {scored_days(args)[1]}")
    print(f"{len(stations)} stations, {len(MODELS)} models + best_match, {len(LEADS)} leads, variables {', '.join(HOURLY_VARIABLES)}")
    for label, params in station_requests(args, stations):
        weight = request_weight(params)
        url = canonical_url(PREVIOUS_RUNS_URL, params)
        cached = (source.cache_dir / "open-meteo" / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()[:24]}.json").exists()
        total += weight
        pending += 0.0 if cached else weight
        print(f"  {label:<22} weight {weight:>8.3f} {'cached' if cached else 'to send'}")
    print(f"total weight of the plan {total:.2f}; still to send {pending:.2f}; already spent {source.spent():.2f}; ceiling {args.max_weight}")
    print(f"after the plan: {source.spent() + pending:.2f} of {args.max_weight}")
    return 0 if source.spent() + pending <= args.max_weight else 2


def command_fetch(args: argparse.Namespace) -> int:
    stations = selected_stations(args)
    source = make_source(args)
    status = 0
    try:
        if args.only in (None, "forecasts"):
            for label, params in station_requests(args, stations):
                try:
                    source.open_meteo(params, label)
                except BudgetExceeded as error:
                    print(f"STOP: {error}", file=sys.stderr)
                    status = 2
                    break
                print(f"  {label:<22} ok (spent {source.spent():.2f} of {args.max_weight})", flush=True)
        if args.only in (None, "observations") and status == 0:
            first, last = scored_days(args)
            days = date_range(first, last)
            obs, _ = load_observations(source, stations, days)
            load_hourly_checks(source, stations, days, obs)
            print("  observations ok")
    finally:
        source.close()
    print(f"sent {source.om_calls} requests this run, {source.om_hits} answered by the cache; weight spent in all: {source.spent():.2f} of {args.max_weight}")
    return status


def command_score(args: argparse.Namespace) -> int:
    stations = selected_stations(args)
    source = make_source(args, dry_run=True)
    first, last = scored_days(args)
    try:
        dataset = build_dataset(source, stations, first, last)
    except BudgetExceeded as error:
        print(f"the cache is not complete, run `fetch` first ({error})", file=sys.stderr)
        return 2
    finally:
        source.close()
    results = score_dataset(dataset)
    results |= load_hourly_checks(source, dataset.stations, dataset.days, dataset.obs)
    ledger = source.ledger()
    sent = [e for e in ledger if e.get("event", "sent") == "sent"]
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scored_days": [first.isoformat(), last.isoformat()],
        "n_days": len(dataset.days),
        "requested_days": dataset.meta["request_days"],
        "leads_days": list(LEADS),
        "models": list(MODELS),
        "reference": BEST_MATCH,
        "stations": [
            {"key": s.key, "name": s.name, "group": s.group, "lat": s.lat, "lon": s.lon, "timezone": s.tz, "source": s.source, "note": s.note}
            for s in dataset.stations
        ],
        "useful_thresholds": USEFUL_GAIN | {"event_rate": USEFUL_EVENT_RATE, "brier": USEFUL_BRIER},
        "rain_event_mm": RAIN_EVENT_MM,
        "bootstrap": {"resamples": BOOTSTRAP_RESAMPLES, "seed": BOOTSTRAP_SEED, "unit": "whole days, all stations of a day together"},
        "open_meteo_calls": {
            "requests_sent_all_runs": len(sent),
            "weighted_calls_all_runs": source.spent(),
            "per_request_weight_rule": "app/collectors/weights.py, variables multiplied by the number of models",
        },
        "forecast_details": dataset.meta["forecast_details"],
        "observations": dataset.meta["observations"],
        "production_cost": production_cost(),
    }
    document = clean({"meta": meta} | results)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(document, separators=(",", ":"), ensure_ascii=False) + "\n"
    if out.suffix == ".gz":
        out.write_bytes(gzip.compress(text.encode("utf-8"), mtime=0))  # mtime 0: the gzip header carries no clock
    else:
        out.write_text(text, encoding="utf-8")
    csv_dir = Path(args.csv_dir) if args.csv_dir else Path(args.cache_dir) / "results"
    write_csv(csv_dir / "mae.csv", clean(results["mae"]))
    write_csv(csv_dir / "rain-events.csv", clean(results["rain_events"]))
    write_csv(csv_dir / "by-station.csv", clean(results["by_station"]))
    if args.markdown:
        Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown).write_text(render_markdown(results), encoding="utf-8")
    print(render_summary(results))
    print(f"\nwritten: {out} and the CSV files in {csv_dir}")
    return 0


PROBE_MODELS = (
    BEST_MATCH,
    "ecmwf_ifs",
    "ecmwf_ifs025",
    "ecmwf_aifs025_single",
    "icon_seamless",
    "gfs_seamless",
    "meteofrance_seamless",
    "ukmo_seamless",
    "gem_seamless",
    "jma_seamless",
    "kma_seamless",
    "cma_grapes_global",
    "bom_access_global",
)
PROBE_PLACE = (48.8217, 2.3378)  # the station of Paris: which models answer at 1 and 5 days, at both ends of the period


def probe_requests() -> list[tuple[str, dict[str, str]]]:
    from app.collectors.grid import coord_str

    base = {"latitude": coord_str(PROBE_PLACE[0]), "longitude": coord_str(PROBE_PLACE[1]), "timezone": "GMT", "wind_speed_unit": "ms"}
    requests = []
    for label, first, last in (("models, start of the archive window", "2026-07-21", "2026-07-23"), ("models, end of the period", "2026-10-19", "2026-10-21")):
        requests.append(
            (
                label,
                {**base, "start_date": first, "end_date": last, "models": ",".join(PROBE_MODELS), "hourly": "temperature_2m_previous_day1,temperature_2m_previous_day5"},
            )
        )
    probability = [f"precipitation_probability_previous_day{n}" for n in LEADS]
    requests.append(
        (
            "best_match, probability of precipitation at fixed lead",
            {**base, "start_date": "2026-10-19", "end_date": "2026-10-21", "hourly": ",".join([*probability, "precipitation_previous_day1", "wind_speed_10m_previous_day1", "precipitation_previous_day5"])},
        )
    )
    return requests


def command_probe(args: argparse.Namespace) -> int:
    """Which models answer at 1 and 5 days, and does the probability of precipitation exist at a fixed lead (about 6 calls)."""
    source = make_source(args)
    try:
        for label, params in probe_requests():
            try:
                payload = source.open_meteo(params, f"probe: {label}")
            except BudgetExceeded as error:
                print(f"STOP: {error}", file=sys.stderr)
                return 2
            hourly = payload["hourly"]
            print(f"\n{label} (weight {request_weight(params)}, {len(hourly['time'])} hours)")
            for name, values in hourly.items():
                if name != "time":
                    print(f"  {name:<62} {sum(1 for v in values if v is None):>3} of {len(values)} hours empty")
    finally:
        source.close()
    print(f"\nweight spent in all: {source.spent():.2f} of {args.max_weight}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("plan", "probe", "fetch", "score"))
    parser.add_argument("--cache-dir", default=str(default_cache_dir()), help="where the answers and the ledger are kept (default: the temporary folder)")
    parser.add_argument("--max-weight", type=float, default=1500.0, help="ceiling of weighted Open-Meteo calls, all runs together (default 1500)")
    parser.add_argument("--max-per-minute", type=float, default=300.0, help="weighted calls a minute at most (default 300, half of what the free API allows)")
    parser.add_argument("--end-date", default="2026-10-21", help="last day of data requested (the day before is the last day scored)")
    parser.add_argument("--days", type=int, default=63, help="days of data requested (two of them are margin: the first and the last day are not scored)")
    parser.add_argument("--stations", nargs="*", help="only these stations (NUM_POSTE or airport code)")
    parser.add_argument("--only", choices=("forecasts", "observations"), help="fetch: only one of the two")
    parser.add_argument("--refresh-obs", action="store_true", help="fetch the observation files again")
    parser.add_argument("--out", default=str(REPO_ROOT / "docs" / "measurements" / "forecast-models-2026-10-results.json.gz"), help="score: the results file (gzip when the name ends with .gz)")
    parser.add_argument("--markdown", help="score: also write the tables of the report in Markdown to this file")
    parser.add_argument("--csv-dir", help="score: where the CSV tables go (default: <cache>/results)")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return {"plan": command_plan, "probe": command_probe, "fetch": command_fetch, "score": command_score}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
