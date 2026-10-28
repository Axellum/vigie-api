"""Solar eclipses: NASA's catalogue and Besselian elements, and what they mean at a place.

``app/data/solar-eclipses.json.gz`` holds NASA's catalogue of the solar eclipses of 2001 to 2100 with their polynomial Besselian
elements, as the Five Millennium Canon's table gives them (``tools/build_eclipses.py``). From the elements, this module works out
the local circumstances of a place (the contacts, the magnitude, the share of the Sun hidden, the altitude of the Sun) and the
points of the central line, by the method of the Explanatory Supplement to the Astronomical Almanac (1992, chapter 8) as Meeus
writes it (Elements of Solar Eclipses 1951-2200, 1989). Every computed value is Vigie's; the elements and the catalogue are NASA's.

The place is on the WGS84 ellipsoid at sea level, without refraction, and the Moon is a sphere (no limb profile): NASA's own path
tables make the same choices, and say that the limb changes the durations by 1 to 3 seconds. Times are UT: the elements are in
Terrestrial Dynamical Time and the catalogue's delta-T converts them, so an error of delta-T moves every time by as much.

Everything takes NumPy arrays of places, so that the same code serves one place and the grid of a map.
"""

from __future__ import annotations

import gzip
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

import numpy as np

CATALOGUE_FILE = Path(__file__).resolve().parents[1] / "data" / "solar-eclipses.json.gz"

EARTH_RADIUS_KM = 6378.137  # the equatorial radius, the unit of the fundamental plane
FLATTENING = 1 / 298.257223563  # WGS84
B_OVER_A = 1 - FLATTENING  # 0.99664719
E2 = FLATTENING * (2 - FLATTENING)  # 0.00669438
DELTA_T_ROTATION = 0.00417807  # degrees the Earth turns in one second of delta-T (1.002738 * 15 / 3600)
TIME_STEP_H = 1e-4  # for the derivatives in time (0.36 s)

EclipseKind = Literal["total", "annular", "hybrid", "partial"]
LocalKind = Literal["total", "annular", "partial", "none"]
_KINDS: dict[str, EclipseKind] = {"T": "total", "A": "annular", "H": "hybrid", "P": "partial"}


@dataclass(frozen=True)
class SolarEclipse:
    """One eclipse of NASA's catalogue: its fields as the catalogue writes them, and its Besselian elements."""

    date: str  # the date of greatest eclipse (TD), "YYYY-MM-DD": the eclipse's identifier
    catalogue_number: int
    type_code: str  # NASA's type, e.g. "T", "A-", "Pb", "H3" (https://eclipse.gsfc.nasa.gov/SEcat5/catkey.html)
    saros: int
    greatest_ut_s: float  # the instant of greatest eclipse, seconds after 0 h UT of the date
    delta_t_s: float
    gamma: float
    magnitude: float
    greatest_lat: Optional[float]
    greatest_lon: Optional[float]
    sun_altitude_deg: Optional[float]
    sun_azimuth_deg: Optional[float]
    path_width_km: Optional[float]
    central_duration_s: Optional[float]
    t0: float  # hours (TD) on the date
    x: tuple[float, ...]
    y: tuple[float, ...]
    d: tuple[float, ...]
    mu: tuple[float, ...]
    l1: tuple[float, ...]
    l2: tuple[float, ...]
    tan_f1: float
    tan_f2: float
    tmin: float
    tmax: float
    elements_source: str  # the NASA page (or the canon's file) the elements were read from

    @property
    def kind(self) -> EclipseKind:
        return _KINDS[self.type_code[0]]

    @property
    def midnight_utc(self) -> float:
        """The Unix time of 0 h UT on the date of the elements."""
        return datetime.fromisoformat(self.date).replace(tzinfo=timezone.utc).timestamp()

    def unix(self, t: float) -> float:
        """The Unix time (UT) of ``t`` hours after ``t0`` (TD)."""
        return self.midnight_utc + (self.t0 + t) * 3600.0 - self.delta_t_s

    def hours(self, unix_utc: float) -> float:
        """The inverse of ``unix``."""
        return (unix_utc + self.delta_t_s - self.midnight_utc) / 3600.0 - self.t0

    @property
    def greatest_utc(self) -> float:
        """The instant of greatest eclipse (UT), as NASA states it."""
        return self.midnight_utc + self.greatest_ut_s


def _poly(coefficients: tuple[float, ...], t: np.ndarray) -> np.ndarray:
    total = np.zeros_like(t, dtype=float)
    for c in reversed(coefficients):
        total = total * t + c
    return total


@lru_cache(maxsize=1)
def catalogue(path: Path = CATALOGUE_FILE) -> tuple[SolarEclipse, ...]:
    """The bundled catalogue, read once, in date order."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    eclipses = []
    for e in data["eclipses"]:
        eclipses.append(
            SolarEclipse(
                date=e["date"],
                catalogue_number=e["catalogue_number"],
                type_code=e["type"],
                saros=e["saros"],
                greatest_ut_s=e["greatest_ut_s"],
                delta_t_s=e["delta_t_s"],
                gamma=e["gamma"],
                magnitude=e["magnitude"],
                greatest_lat=e["greatest_lat"],
                greatest_lon=e["greatest_lon"],
                sun_altitude_deg=e["sun_altitude_deg"],
                sun_azimuth_deg=e["sun_azimuth_deg"],
                path_width_km=e["path_width_km"],
                central_duration_s=e["central_duration_s"],
                t0=e["t0"],
                x=tuple(e["x"]),
                y=tuple(e["y"]),
                d=tuple(e["d"]),
                mu=tuple(e["mu"]),
                l1=tuple(e["l1"]),
                l2=tuple(e["l2"]),
                tan_f1=e["tan_f1"],
                tan_f2=e["tan_f2"],
                tmin=e["tmin"],
                tmax=e["tmax"],
                elements_source=e["elements_source"],
            )
        )
    return tuple(sorted(eclipses, key=lambda item: item.date))


def eclipse_on(date: str) -> Optional[SolarEclipse]:
    """The eclipse of ``date`` ("YYYY-MM-DD"), or ``None``."""
    for eclipse in catalogue():
        if eclipse.date == date:
            return eclipse
    return None


# ============================================================================
# A place in the fundamental plane
# ============================================================================


@dataclass(frozen=True)
class Places:
    """Places on the ellipsoid: geodetic latitude and east longitude (degrees), and their geocentric ``rho sin/cos phi'``."""

    lat: np.ndarray
    lon: np.ndarray
    rho_sin: np.ndarray
    rho_cos: np.ndarray

    @classmethod
    def at(cls, lat: np.ndarray | float, lon: np.ndarray | float) -> Places:
        lat_a = np.clip(np.asarray(lat, dtype=float), -89.9999, 89.9999)
        lon_a = np.asarray(lon, dtype=float)
        u = np.arctan(B_OVER_A * np.tan(np.radians(lat_a)))
        return cls(lat=lat_a, lon=lon_a, rho_sin=B_OVER_A * np.sin(u), rho_cos=np.cos(u))


@dataclass(frozen=True)
class Shadow:
    """At a time ``t`` and for each place: the shadow axis seen from the place (``u``, ``v``, Earth radii), the radii of the
    penumbra and the umbra in the place's plane (``l1``, ``l2``; ``l2`` is negative when the Moon covers the Sun), the place's
    height in the fundamental frame (``zeta``, positive when the Sun is up) and the Sun's altitude and azimuth (degrees)."""

    u: np.ndarray
    v: np.ndarray
    l1: np.ndarray
    l2: np.ndarray
    zeta: np.ndarray
    sun_altitude: np.ndarray
    sun_azimuth: np.ndarray
    parallactic: np.ndarray  # the angle from the north to the zenith on the Sun's disc, towards the east (degrees)

    @property
    def distance(self) -> np.ndarray:
        return np.hypot(self.u, self.v)


def shadow(eclipse: SolarEclipse, t: np.ndarray | float, places: Places) -> Shadow:
    """The shadow at ``t`` hours after ``t0`` for each place."""
    t_a = np.broadcast_to(np.asarray(t, dtype=float), np.broadcast(places.lat, np.asarray(t)).shape)
    x = _poly(eclipse.x, t_a)
    y = _poly(eclipse.y, t_a)
    d = np.radians(_poly(eclipse.d, t_a))
    mu = _poly(eclipse.mu, t_a)
    hour_angle = np.radians(mu + places.lon - DELTA_T_ROTATION * eclipse.delta_t_s)
    sin_d, cos_d = np.sin(d), np.cos(d)
    sin_h, cos_h = np.sin(hour_angle), np.cos(hour_angle)
    xi = places.rho_cos * sin_h
    eta = places.rho_sin * cos_d - places.rho_cos * sin_d * cos_h
    zeta = places.rho_sin * sin_d + places.rho_cos * cos_d * cos_h
    phi = np.radians(places.lat)
    sin_phi, cos_phi = np.sin(phi), np.cos(phi)
    altitude = np.degrees(np.arcsin(np.clip(sin_phi * sin_d + cos_phi * cos_d * cos_h, -1.0, 1.0)))
    azimuth = np.degrees(np.arctan2(-cos_d * sin_h, sin_d * cos_phi - cos_d * sin_phi * cos_h)) % 360.0
    parallactic = np.degrees(np.arctan2(sin_h, np.tan(phi) * cos_d - sin_d * cos_h))
    return Shadow(
        u=x - xi,
        v=y - eta,
        l1=_poly(eclipse.l1, t_a) - zeta * eclipse.tan_f1,
        l2=_poly(eclipse.l2, t_a) - zeta * eclipse.tan_f2,
        zeta=zeta,
        sun_altitude=altitude,
        sun_azimuth=azimuth,
        parallactic=parallactic,
    )


def magnitude(s: Shadow) -> np.ndarray:
    """The share of the Sun's diameter the Moon covers (0 when they do not touch; above 1 in totality)."""
    return np.maximum(0.0, (s.l1 - s.distance) / (s.l1 + s.l2))


def obscuration(s: Shadow) -> np.ndarray:
    """The share of the Sun's disc the Moon hides (0 to 1)."""
    k = (s.l1 - s.l2) / (s.l1 + s.l2)  # the Moon's radius, the Sun's being 1
    sep = 2.0 * s.distance / (s.l1 + s.l2)  # the distance between the centres
    result = np.zeros_like(sep)
    inside = sep <= np.abs(k - 1.0)
    result = np.where(inside, np.where(k >= 1.0, 1.0, k * k), result)
    partial = (~inside) & (sep < 1.0 + k)
    with np.errstate(invalid="ignore", divide="ignore"):
        safe = np.where(partial, sep, 1.0)
        a1 = np.arccos(np.clip((safe**2 + 1.0 - k**2) / (2.0 * safe), -1.0, 1.0))
        a2 = np.arccos(np.clip((safe**2 + k**2 - 1.0) / (2.0 * safe * k), -1.0, 1.0))
        lens = a1 + k * k * a2 - 0.5 * np.sqrt(np.maximum(0.0, (-safe + 1 + k) * (safe + 1 - k) * (safe - 1 + k) * (safe + 1 + k)))
    return np.where(partial, lens / math.pi, result)


def time_of_maximum(eclipse: SolarEclipse, places: Places, iterations: int = 8) -> np.ndarray:
    """For each place, the time (hours after ``t0``) when the axis passes nearest, within the validity of the elements."""
    t = np.zeros_like(places.lat, dtype=float)
    for _ in range(iterations):
        before = shadow(eclipse, t - TIME_STEP_H, places)
        after = shadow(eclipse, t + TIME_STEP_H, places)
        now = shadow(eclipse, t, places)
        du = (after.u - before.u) / (2 * TIME_STEP_H)
        dv = (after.v - before.v) / (2 * TIME_STEP_H)
        step = -(now.u * du + now.v * dv) / (du * du + dv * dv)
        t = np.clip(t + step, eclipse.tmin, eclipse.tmax)
    return t


# ============================================================================
# Local circumstances of one place
# ============================================================================


@dataclass(frozen=True)
class Contact:
    """A contact (or the maximum) at a place: its time (UT) and the Sun's position then."""

    name: Literal["partial_begin", "central_begin", "maximum", "central_end", "partial_end"]
    time_utc: float
    sun_altitude_deg: float
    sun_azimuth_deg: float


@dataclass(frozen=True)
class LocalCircumstances:
    """What a place sees of one eclipse (computed by Vigie from NASA's elements)."""

    kind: LocalKind
    magnitude: float
    obscuration: float
    contacts: tuple[Contact, ...]  # in time order; a contact outside the validity of the elements is left out
    central_duration_s: Optional[float]  # the total or annular phase, when the place has one
    sunrise_utc: Optional[float] = None  # the Sun rises (geometric horizon) between the first contact and the last
    sunset_utc: Optional[float] = None  # the Sun sets between them


def _one(value: np.ndarray) -> float:
    return float(np.asarray(value).reshape(-1)[0])


def _root(f, low: float, high: float, iterations: int = 50) -> Optional[float]:  # type: ignore[no-untyped-def]
    """The ``t`` in ``[low, high]`` where ``f`` changes sign, by bisection; ``None`` when it does not."""
    f_low, f_high = f(low), f(high)
    if f_low == 0:
        return low
    if (f_low < 0) == (f_high < 0):
        return None
    for _ in range(iterations):
        middle = (low + high) / 2
        f_middle = f(middle)
        if (f_middle < 0) == (f_low < 0):
            low, f_low = middle, f_middle
        else:
            high = middle
    return (low + high) / 2


def local_circumstances(eclipse: SolarEclipse, lat: float, lon: float) -> LocalCircumstances:
    """The eclipse at ``(lat, lon)``: contacts, magnitude, share hidden, central duration. Whether the Sun is up is each
    contact's ``sun_altitude_deg``, and a sunrise or sunset between the contacts is given; a place where the Sun stays down from
    the first contact to the last sees nothing (``none``)."""
    places = Places.at(lat, lon)
    t_max = _one(time_of_maximum(eclipse, places))
    at_max = shadow(eclipse, t_max, places)
    mag = _one(magnitude(at_max))

    def contact(name, t: float) -> Contact:  # type: ignore[no-untyped-def]
        s = shadow(eclipse, t, places)
        return Contact(name, eclipse.unix(t), round(_one(s.sun_altitude), 2), round(_one(s.sun_azimuth), 2))

    if mag <= 0.0:
        return LocalCircumstances("none", 0.0, 0.0, (), None)

    def outer(t: float) -> float:
        s = shadow(eclipse, t, places)
        return _one(s.distance - s.l1)

    def inner(t: float) -> float:
        s = shadow(eclipse, t, places)
        return _one(s.distance - np.abs(s.l2))

    times: dict[str, Optional[float]] = {
        "partial_begin": _root(outer, eclipse.tmin, t_max),
        "maximum": t_max,
        "partial_end": _root(outer, t_max, eclipse.tmax),
    }
    kind: LocalKind = "partial"
    duration: Optional[float] = None
    l2_max = _one(at_max.l2)
    if _one(at_max.distance) < abs(l2_max):
        kind = "total" if l2_max < 0 else "annular"
        times["central_begin"] = _root(inner, eclipse.tmin, t_max)
        times["central_end"] = _root(inner, t_max, eclipse.tmax)
        if times["central_begin"] is not None and times["central_end"] is not None:
            duration = (times["central_end"] - times["central_begin"]) * 3600.0
    contacts = sorted((contact(name, t) for name, t in times.items() if t is not None), key=lambda c: c.time_utc)

    def altitude(t: float) -> float:
        return _one(shadow(eclipse, t, places).sun_altitude)

    first = times["partial_begin"] if times["partial_begin"] is not None else eclipse.tmin
    last = times["partial_end"] if times["partial_end"] is not None else eclipse.tmax
    crossing = _root(altitude, first, last)
    sunrise = sunset = None
    if crossing is not None:
        if altitude(first) < 0:
            sunrise = eclipse.unix(crossing)
        else:
            sunset = eclipse.unix(crossing)
    elif all(c.sun_altitude_deg <= 0.0 for c in contacts):
        # The Sun is down from the first contact to the last: the place is on the night side, where the shadow does not reach (the
        # fundamental plane would put it under the shadow, through the Earth).
        return LocalCircumstances("none", 0.0, 0.0, (), None)
    return LocalCircumstances(kind, mag, _one(obscuration(at_max)), tuple(contacts), duration, sunrise, sunset)


# ============================================================================
# The central line
# ============================================================================


def project(eclipse: SolarEclipse, t: np.ndarray, xi: np.ndarray, eta: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The point of the ellipsoid that the line through ``(xi, eta)`` of the fundamental plane, parallel to the shadow axis,
    meets on the Sun's side: ``(lat, lon, valid)``; ``valid`` is false where the line misses the Earth."""
    d = np.radians(_poly(eclipse.d, t))
    mu = _poly(eclipse.mu, t)
    omega = 1.0 / np.sqrt(1.0 - E2 * np.cos(d) ** 2)
    y1 = omega * eta
    b1 = omega * np.sin(d)
    b2 = B_OVER_A * omega * np.cos(d)
    square = 1.0 - xi**2 - y1**2
    valid = square >= 0.0
    b = np.sqrt(np.maximum(square, 0.0))
    phi1 = np.arcsin(np.clip(b * b1 + y1 * b2, -1.0, 1.0))
    hour_angle = np.degrees(np.arctan2(xi, b * b2 - y1 * b1))
    lat = np.degrees(np.arctan(np.tan(phi1) / B_OVER_A))
    lon = (hour_angle - mu + DELTA_T_ROTATION * eclipse.delta_t_s + 180.0) % 360.0 - 180.0
    return lat, lon, valid


def central_line(eclipse: SolarEclipse, t: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The point under the shadow axis at each ``t``: ``(lat, lon, valid)``."""
    return project(eclipse, t, _poly(eclipse.x, t), _poly(eclipse.y, t))
