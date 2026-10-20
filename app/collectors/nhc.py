"""NOAA National Hurricane Center (NHC): the active tropical cyclones of the Atlantic and the eastern and central Pacific.

Two kinds of request, both without a key:

- ``CurrentStorms.json`` lists the storms that have an advisory now (the format is described in "NHC Tropical Cyclone Status
  JSON File Reference", https://www.nhc.noaa.gov/productexamples/NHC_Tropical_Cyclone_Status_JSON_File_Reference.pdf; the real
  answer differs from that document in one way, read on 2026-10-20: the intensity and the pressure are strings and the
  numeric coordinates are called ``latitudeNumeric`` / ``longitudeNumeric``, so every number is read from either form). It is
  asked conditionally (``ETag``). With no storm it is ``{"activeStorms": []}``, 24 bytes.
- for each storm, the ZIP of shapefiles of its latest advisory that the file points to (``forecastTrack.zipFile``): the cone of
  uncertainty, the forecast track and its points, and the coastal watches and warnings. A bundle belongs to one advisory and
  never changes, so it is fetched once, whatever the number of readers, and forgotten when the storm leaves the list.

One storm is one hazard (category ``HURRICANE``, at its current position); its forecast is served by
``GET /v1/geojson/hazards/{id}/forecast`` (``app/services/forecasts.py``). The words of the NHC are not rewritten: the
storm name, the classification and the advisory number are as it gives them. The levels and the speed unit are ours and
documented in ``docs/data-sources.md``.

Nothing is made up: a pressure the NHC does not give for a forecast point (it writes 9999) is ``None``, a storm with no
position is skipped, and a storm whose bundle cannot be read keeps its hazard and has no forecast.
"""

from __future__ import annotations

import asyncio
import io
import re
import zipfile
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx

from app import clock
from app.collectors.base import BaseCollector, CacheEntry, Collected, optional_float, parse_iso_utc
from app.models.schemas import HazardAlert, HazardCategory, SeverityLevel
from app.shapefile import POINT, ShapefileError, read_shapefile
from app.storm_geo import geometry_of_shape

CURRENT_STORMS_URL = "https://www.nhc.noaa.gov/CurrentStorms.json"
BUNDLE_HOST = "www.nhc.noaa.gov"

KNOT_KMH = 1.852  # one knot is exactly 1.852 km/h (international nautical mile)

# A bundle is some tens of kilobytes; these limits only stop a mistake (or an archive that lies about its size).
MAX_BUNDLE_BYTES = 4 * 1024 * 1024
MAX_MEMBERS = 60
MAX_MEMBER_BYTES = 8 * 1024 * 1024
MAX_STORMS = 20
MAX_BUNDLES_AT_ONCE = 3
RETRY_BUNDLE_S = 600.0  # a bundle that could not be read is asked for again after ten minutes, not at every refresh

STORM_ID = re.compile(r"^(al|ep|cp)(\d{2})(\d{4})$")

# NHC classification abbreviations (the JSON reference), used for the title of the hazard. Others give the plain name.
CLASSIFICATIONS: dict[str, str] = {
    "TD": "Tropical Depression",
    "STD": "Subtropical Depression",
    "TS": "Tropical Storm",
    "STS": "Subtropical Storm",
    "HU": "Hurricane",
    "TY": "Typhoon",
    "PTC": "Post-Tropical Cyclone",
    "PC": "Potential Tropical Cyclone",
}

# The NHC's codes of a coastal watch or warning (``TCWW`` in the shapefile, with these labels in its metadata).
WATCH_WARNING_LABELS: dict[str, str] = {
    "HWR": "Hurricane Warning",
    "HWA": "Hurricane Watch",
    "TWR": "Tropical Storm Warning",
    "TWA": "Tropical Storm Watch",
}

MISSING_THRESHOLD = 9000.0  # the bundle writes 9999 where it has no value (a pressure, a direction, a speed)


def level_of(intensity_kt: Optional[float]) -> SeverityLevel:
    """Our level for a maximum sustained wind (knots), by the thresholds of the Saffir-Simpson Hurricane Wind Scale.

    Category 3 and above (96 kt or more, "major hurricane" for the NHC) is RED, category 1 and 2 (64 kt) ORANGE, a tropical storm
    (34 kt) YELLOW, less GREEN. The hazard model has no "unknown" level: a storm with no intensity is GREEN and has no value.
    https://www.nhc.noaa.gov/aboutsshws.php. The ORANGE and RED choice is ours.
    """
    if intensity_kt is None:
        return SeverityLevel.GREEN
    if intensity_kt >= 96:
        return SeverityLevel.RED
    if intensity_kt >= 64:
        return SeverityLevel.ORANGE
    if intensity_kt >= 34:
        return SeverityLevel.YELLOW
    return SeverityLevel.GREEN


@dataclass(frozen=True)
class ForecastPoint:
    """A forecast position of the centre: ``tau_h`` hours after the nominal start of the forecast."""

    tau_h: Optional[int]
    valid_utc: Optional[int]
    geometry: dict[str, Any]
    wind_kt: Optional[float]
    gust_kt: Optional[float]
    pressure_hpa: Optional[float]
    stage: Optional[str]
    category: Optional[int]  # Saffir-Simpson category the NHC gives for the point, 0 when it is not a hurricane


@dataclass(frozen=True)
class WatchWarning:
    """A coastal stretch under a watch or a warning (a line between two breakpoints)."""

    code: str
    label: Optional[str]
    geometry: dict[str, Any]


@dataclass(frozen=True)
class StormForecast:
    """The forecast of one advisory: cone, track, forecast points and watches and warnings, as GeoJSON geometries."""

    advisory: Optional[str]
    issued_utc: Optional[int]
    cones: tuple[dict[str, Any], ...]
    tracks: tuple[dict[str, Any], ...]
    points: tuple[ForecastPoint, ...]
    watches_warnings: tuple[WatchWarning, ...]
    bytes_read: int = 0


@dataclass(frozen=True)
class Storm:
    """One active storm as the status file gives it, with its forecast when the bundle could be read."""

    id: str
    name: str
    classification: Optional[str]
    intensity_kt: Optional[float]
    pressure_hpa: Optional[float]
    lat: float
    lon: float
    movement_dir_deg: Optional[float]
    movement_speed_mph: Optional[float]
    last_update_utc: int
    advisory: Optional[str]
    advisory_issued_utc: Optional[int]
    advisory_url: Optional[str]
    bundle_url: Optional[str]
    forecast: Optional[StormForecast] = None

    @property
    def season(self) -> int:
        """The year of the season, from the identifier (``al092026``)."""
        return int(self.id[4:])


@dataclass(frozen=True)
class NHCState:
    """What the status file lists, the storms with their forecast."""

    storms: tuple[Storm, ...] = ()
    unreadable: int = 0  # entries of the file that could not be read as a storm
    bundles_failed: int = 0  # storms whose forecast bundle could not be read this time


def _number(value: Any) -> Optional[float]:
    """A number the NHC wrote as a number or as a string; ``None`` when it is neither."""
    if isinstance(value, str):
        value = value.strip() or None
    return optional_float(value)


def _coordinate(numeric: Any, text: Any, positive: str, negative: str) -> Optional[float]:
    """A coordinate from the numeric field, else from the text (``19.5N``, ``108.0W``)."""
    number = _number(numeric)
    if number is not None:
        return number
    if isinstance(text, str):
        found = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([A-Za-z])\s*", text)
        if found and found.group(2).upper() in (positive, negative):
            return float(found.group(1)) * (1.0 if found.group(2).upper() == positive else -1.0)
    return None


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def parse_current_storms(payload: Any) -> NHCState:
    """The storms of a ``CurrentStorms.json`` answer. An answer that is not that file raises ``ValueError``."""
    if not isinstance(payload, dict) or not isinstance(payload.get("activeStorms"), list):
        raise ValueError("NHC answer is not a CurrentStorms.json file (no activeStorms list)")
    storms: list[Storm] = []
    unreadable = 0
    for raw in payload["activeStorms"][:MAX_STORMS]:
        try:
            storm = _parse_storm(raw)
        except (ValueError, TypeError, KeyError, AttributeError):
            storm = None
        if storm is None:
            unreadable += 1
        else:
            storms.append(storm)
    return NHCState(storms=tuple(storms), unreadable=unreadable)


def _parse_storm(raw: Any) -> Optional[Storm]:
    if not isinstance(raw, dict):
        return None
    storm_id = _text(raw.get("id"))
    name = _text(raw.get("name"))
    if storm_id is None or name is None or not STORM_ID.match(storm_id.lower()):
        return None
    lat = _coordinate(raw.get("latitudeNumeric", raw.get("latitude_numeric")), raw.get("latitude"), "N", "S")
    lon = _coordinate(raw.get("longitudeNumeric", raw.get("longitude_numeric")), raw.get("longitude"), "E", "W")
    updated = parse_iso_utc(raw.get("lastUpdate"))
    if lat is None or lon is None or updated is None or not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        return None
    track, public = _section(raw, "forecastTrack"), _section(raw, "publicAdvisory")
    classification = _text(raw.get("classification"))
    return Storm(
        id=storm_id.lower(),
        name=name,
        classification=classification.upper() if classification else None,
        intensity_kt=_number(raw.get("intensity")),
        pressure_hpa=_number(raw.get("pressure")),
        lat=lat,
        lon=lon,
        movement_dir_deg=_number(raw.get("movementDir")),
        movement_speed_mph=_number(raw.get("movementSpeed")),
        last_update_utc=updated,
        advisory=_text(track.get("advNum")) or _text(public.get("advNum")),
        advisory_issued_utc=parse_iso_utc(track.get("issuance")) or parse_iso_utc(public.get("issuance")),
        advisory_url=_text(public.get("url")),
        bundle_url=_bundle_url(track.get("zipFile")),
    )


def _bundle_url(value: Any) -> Optional[str]:
    """The address of a forecast bundle, only when it is an https address of the NHC's own server."""
    url = _text(value)
    if url is None:
        return None
    parts = urlsplit(url)
    return url if parts.scheme == "https" and parts.hostname == BUNDLE_HOST and parts.path.endswith(".zip") else None


def hazard_of(storm: Storm) -> HazardAlert:
    """The hazard of a storm: its current position, our level from the wind, and the wind in km/h (the unit of GDACS cyclones)."""
    label = CLASSIFICATIONS.get(storm.classification or "")
    kmh = None if storm.intensity_kt is None else round(storm.intensity_kt * KNOT_KMH, 1)
    return HazardAlert(
        id=f"nhc_{storm.id}",
        category=HazardCategory.HURRICANE,
        severity=level_of(storm.intensity_kt),
        title=(f"{label} {storm.name}" if label else storm.name)[:96],
        source="NHC",
        sources=["NHC"],
        timestamp_utc=storm.last_update_utc,
        lat=round(storm.lat, 4),
        lon=round(storm.lon, 4),
        value_primary=kmh,
        value_unit="km/h",
        tsunami_warning=False,
    ).with_event_key(cyclone_key(storm.name, storm.season))


def cyclone_key(name: str, season: int) -> str:
    """What names the same storm in two sources: its name and the year of its season (``cyclone:isaias:2026``)."""
    return f"cyclone:{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}:{season}"


# ----------------------------------------------------------------------------------------------------- the forecast bundle


def _valid_time(validtime: Optional[str], reference_utc: Optional[int]) -> Optional[int]:
    """The UTC instant of a ``dd/hhmm`` valid time: the day and hour are the bundle's, the month and year are the ones that put it
    closest to ``reference_utc`` (the advisory), which is how a forecast of up to five days is read across a month end."""
    found = re.fullmatch(r"(\d{2})/(\d{2})(\d{2})", (validtime or "").strip())
    if found is None or reference_utc is None:
        return None
    day, hour, minute = (int(part) for part in found.groups())
    reference = datetime.fromtimestamp(reference_utc, tz=timezone.utc)
    best: Optional[datetime] = None
    for months in (-1, 0, 1):
        month_index = reference.year * 12 + (reference.month - 1) + months
        year, month = divmod(month_index, 12)
        try:
            candidate = datetime(year, month + 1, day, hour, minute, tzinfo=timezone.utc)
        except ValueError:
            continue
        if best is None or abs(candidate - reference) < abs(best - reference):
            best = candidate
    if best is None or abs(best - reference) > timedelta(days=10):
        return None
    return int(best.timestamp())


MONTHS = {name: number for number, name in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}


def _advisory_day(advdate: Optional[str]) -> Optional[int]:
    """Noon UTC of the day in the ``ADVDATE`` of a bundle (``700 PM CDT Fri Oct 09 2026``): close enough to place a ``dd/hhmm``."""
    found = re.search(r"\b([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{4})\s*$", advdate or "")
    if found is None or found.group(1) not in MONTHS:
        return None
    try:
        return int(datetime(int(found.group(3)), MONTHS[found.group(1)], int(found.group(2)), 12, tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


def _known(value: Optional[str]) -> Optional[float]:
    """A numeric field of the bundle; ``None`` for no number or for the 9999 the bundle writes where it has no value."""
    number = _number(value)
    return None if number is None or number >= MISSING_THRESHOLD else number


def _members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    members = [info for info in archive.infolist() if not info.is_dir()]
    if len(members) > MAX_MEMBERS or any(info.file_size > MAX_MEMBER_BYTES for info in members):
        raise ValueError("NHC bundle is larger than any forecast ever was")
    return {info.filename: info for info in members}


def _layer(archive: zipfile.ZipFile, members: dict[str, zipfile.ZipInfo], suffix: str) -> list[tuple[Any, dict[str, str]]]:
    """The records of the layer whose file name ends with ``suffix`` (``5day_pgn``), or none if the bundle has no such layer."""
    shp = next((name for name in members if name.endswith(f"{suffix}.shp")), None)
    dbf = None if shp is None else shp[:-4] + ".dbf"
    if shp is None or dbf not in members:
        return []
    prj = shp[:-4] + ".prj"
    if prj in members and not archive.read(prj).lstrip().upper().startswith(b"GEOGCS"):
        raise ValueError(f"NHC layer {suffix} is not in geographic coordinates")
    try:
        return read_shapefile(archive.read(shp), archive.read(dbf))
    except ShapefileError as exc:
        raise ValueError(f"NHC layer {suffix}: {exc}") from exc


def parse_forecast_bundle(content: bytes, issued_utc: Optional[int] = None, advisory: Optional[str] = None) -> StormForecast:
    """The forecast of a ZIP of shapefiles: cone, track, points, watches and warnings. A ZIP that is none of these raises ``ValueError``.

    ``issued_utc`` (the advisory time of the status file) places the ``dd/hhmm`` valid times in a month and a year.
    """
    if len(content) > MAX_BUNDLE_BYTES:
        raise ValueError("NHC bundle is larger than any forecast ever was")
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"NHC answer is not a ZIP archive: {exc}") from exc
    members = _members(archive)
    cones_layer = _layer(archive, members, "5day_pgn")
    tracks_layer = _layer(archive, members, "5day_lin")
    points_layer = _layer(archive, members, "5day_pts")
    warnings_layer = _layer(archive, members, "wwlin")
    if not (cones_layer or tracks_layer or points_layer):
        raise ValueError("NHC bundle holds none of the cone, the track or the points")

    cones = tuple(g for shape, _ in cones_layer if (g := geometry_of_shape(shape)) is not None)
    tracks = tuple(g for shape, _ in tracks_layer if (g := geometry_of_shape(shape)) is not None)
    first_row = next((row for _, row in [*points_layer, *cones_layer, *tracks_layer] if row), {})
    name_of_advisory = _text(first_row.get("ADVISNUM")) or advisory
    reference_utc = issued_utc if issued_utc is not None else _advisory_day(first_row.get("ADVDATE"))

    points: list[ForecastPoint] = []
    for shape, row in points_layer:
        if shape.kind != POINT:
            continue
        geometry = geometry_of_shape(shape)
        if geometry is None:
            continue
        tau = _number(row.get("TAU"))
        category = _number(row.get("SSNUM"))
        points.append(
            ForecastPoint(
                tau_h=None if tau is None else int(tau),
                valid_utc=_valid_time(row.get("VALIDTIME"), reference_utc),
                geometry=geometry,
                wind_kt=_known(row.get("MAXWIND")),
                gust_kt=_known(row.get("GUST")),
                pressure_hpa=_known(row.get("MSLP")),
                stage=_text(row.get("TCDVLP")),
                category=None if category is None else int(category),
            )
        )
    points.sort(key=lambda point: (point.tau_h is None, point.tau_h or 0))

    warnings: list[WatchWarning] = []
    for shape, row in warnings_layer:
        geometry = geometry_of_shape(shape)
        code = (_text(row.get("TCWW")) or "").upper()
        if geometry is not None and code:
            warnings.append(WatchWarning(code=code, label=WATCH_WARNING_LABELS.get(code), geometry=geometry))
    return StormForecast(
        advisory=name_of_advisory,
        issued_utc=issued_utc,
        cones=cones,
        tracks=tracks,
        points=tuple(points),
        watches_warnings=tuple(warnings),
        bytes_read=len(content),
    )


# ----------------------------------------------------------------------------------------------------- the collector


class NHCCollector(BaseCollector[list[HazardAlert]]):
    """The active storms of the NHC: one conditional request every five minutes, one bundle per new advisory."""

    # 300 s: the file is sent with ``Cache-Control: max-age=300`` (read on 2026-10-20) and the NHC issues advisories every
    # three to six hours. A refresh that finds nothing new is a ``304`` of a few hundred bytes.
    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(
            provider_id="nhc_storms",
            name="NOAA National Hurricane Center (active storms)",
            domain="disasters",
            ttl_seconds=300,
            http_client=http_client,
        )
        # The storms of the last answer read, with their forecast (see ``forecast_for``).
        self.last_state: NHCState = NHCState()
        self._bundles: dict[str, StormForecast] = {}
        self._failed: dict[str, float] = {}  # a bundle that could not be read -> when to ask for it again

    async def _collect(self, client: httpx.AsyncClient, **kwargs: Any) -> Collected[list[HazardAlert]]:
        previous: Optional[CacheEntry[list[HazardAlert]]] = kwargs.get("previous")
        headers = {"Accept": "application/json"}
        if previous is not None and previous.validators:
            for name, header in (("ETag", "If-None-Match"), ("Last-Modified", "If-Modified-Since")):
                if previous.validators.get(name):
                    headers[header] = previous.validators[name]
        response = await self._get(client, CURRENT_STORMS_URL, headers=headers)
        if response.status_code == 304:
            if previous is None:
                raise ValueError("NHC answered 304 Not Modified to a request that was not conditional")
            if any(storm.bundle_url and storm.forecast is None for storm in self.last_state.storms):
                # The file did not change, but a bundle could not be read when it did: it is not left without a forecast until the
                # next advisory, hours later.
                self.last_state = await self._with_forecasts(client, self.last_state)
            return Collected(previous.value, validators=previous.validators)
        response.raise_for_status()
        state = parse_current_storms(response.json())
        state = await self._with_forecasts(client, state)
        self.last_state = state
        validators = {name: response.headers[name] for name in ("ETag", "Last-Modified") if response.headers.get(name)}
        return Collected([hazard_of(storm) for storm in state.storms], validators=validators or None)

    async def _with_forecasts(self, client: httpx.AsyncClient, state: NHCState) -> NHCState:
        """Fetch the bundles that are not held yet (a bundle never changes), forget the ones no storm points to any more."""
        wanted = {storm.bundle_url for storm in state.storms if storm.bundle_url}
        for url in [url for url in self._bundles if url not in wanted]:
            del self._bundles[url]
        semaphore = asyncio.Semaphore(MAX_BUNDLES_AT_ONCE)

        async def load(storm: Storm) -> bool:
            assert storm.bundle_url is not None
            async with semaphore:
                try:
                    response = await self._get(client, storm.bundle_url, headers={"Accept": "application/zip, */*;q=0.5"})
                    response.raise_for_status()
                    self._bundles[storm.bundle_url] = await asyncio.to_thread(
                        parse_forecast_bundle, response.content, storm.advisory_issued_utc, storm.advisory
                    )
                    return True
                except Exception:  # the hazard stays, its forecast is unknown and it is asked for again in ten minutes
                    self._failed[storm.bundle_url] = clock.monotonic() + RETRY_BUNDLE_S
                    return False

        for url in [url for url, until in self._failed.items() if until <= clock.monotonic() or url not in wanted]:
            del self._failed[url]
        todo = [
            storm
            for storm in state.storms
            if storm.bundle_url and storm.bundle_url not in self._bundles and storm.bundle_url not in self._failed
        ]
        await asyncio.gather(*(load(storm) for storm in todo))
        storms = tuple(replace(storm, forecast=self._bundles.get(storm.bundle_url or "")) for storm in state.storms)
        failed = sum(1 for storm in storms if storm.bundle_url and storm.forecast is None)
        return NHCState(storms=storms, unreadable=state.unreadable, bundles_failed=failed)

    def storm_for(self, hazard_id: str) -> Optional[Storm]:
        """The storm of a hazard id (``nhc_al092026``) in the last answer read."""
        return next((storm for storm in self.last_state.storms if f"nhc_{storm.id}" == hazard_id), None)

