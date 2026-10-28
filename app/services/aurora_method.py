"""The aurora estimate of Vigie: the chance to see an aurora from a place at a given hour (ADR 0012). Ours, not a source's.

Nothing here is a provider's figure: the estimate is a method of this project, written down in
``docs/decisions/0012-aurora-estimate.md`` with its criterion, and measured on past nights (``tools/measure_aurora.py``,
``docs/measurements/aurora-2026-10.md``) before any route serves it. Every answer that carries it says so (``derived``, ``estimate``).

The chance is a product of four factors between 0 and 1:

- **activity**: how far towards the equator the aurora reaches for the planetary Kp index of that hour. The equatorward edge of the
  aurora seen overhead is a magnetic latitude that falls with Kp (``edge_latitude``: ``EDGE_AT_KP0 - EDGE_PER_KP * Kp``, two
  numbers fitted on the reports of 2014 to 2022 only); SWPC writes that the aurora "can be observed from as much as a 1000 km away
  when the aurora is bright", so the factor falls linearly from 1 at the edge to 0 at 1,000 km (``REACH_KM``) beyond it.
- **darkness**: 1 when the Sun is 18 degrees or more below the horizon (the dark night), 0 at 6 degrees below (the end of civil
  twilight), linear in between: a bright aurora is seen in nautical twilight.
- **moon**: 1 when the Moon is down; above the horizon it takes away up to half of the chance, in proportion to its illuminated
  fraction (``MOON_WEIGHT``).
- **clouds**: the share of the sky the forecast leaves clear, ``1 - cloud_cover / 100``.

The magnetic latitude is the modified apex latitude of the International Geomagnetic Reference Field, IGRF-14, read from a grid
computed offline by following its field lines (``app/services/magnetic.py``). A centred dipole would put Western Europe 5 to 10
degrees too far north and make the estimate far too hopeful there.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

# --- The settings of the method (ADR 0012). Fitted: EDGE_AT_KP0 and EDGE_PER_KP, on the reports of 2014 to 2022 only.
# ``python tools/measure_aurora.py fit`` on 6,443 reports of 2014 to 2022 (2026-10-28): 62.5 and 1.9, mean log-likelihood -0.502.
EDGE_AT_KP0 = 62.5  # magnetic latitude (degrees) of the equatorward edge of the aurora seen overhead, at Kp 0
EDGE_PER_KP = 1.9  # degrees the edge moves towards the equator for each unit of Kp
REACH_KM = 1000.0  # SWPC: "can be observed from as much as a 1000 km away when the aurora is bright"
KM_PER_DEGREE = 111.2  # one degree of latitude along a meridian (mean Earth radius 6,371 km)
DARK_SUN_ALTITUDE = -18.0  # the dark night: the Sun 18 degrees below the horizon or more
TWILIGHT_SUN_ALTITUDE = -6.0  # no aurora is counted while the Sun is higher than this
MOON_WEIGHT = 0.5  # the share of the chance a full Moon above the horizon takes away

# The words the answers use for a chance (ours).
LEVELS: tuple[tuple[float, str], ...] = ((0.5, "likely"), (0.2, "possible"), (0.05, "unlikely"), (0.0, "no"))


def magnetic_latitude(lat: float, lon: float) -> float:
    """The magnetic latitude the estimate uses: the modified apex latitude of IGRF-14 (``app/services/magnetic.py``)."""
    from app.services.magnetic import magnetic_latitude as apex

    return apex(lat, lon)


def edge_latitude(kp: float, edge_at_kp0: float = EDGE_AT_KP0, edge_per_kp: float = EDGE_PER_KP) -> float:
    """Geomagnetic latitude of the equatorward edge of the aurora seen overhead, for a Kp index."""
    return edge_at_kp0 - edge_per_kp * max(0.0, min(9.0, kp))


def distance_to_edge_km(magnetic_lat: float, kp: float, edge_at_kp0: float = EDGE_AT_KP0, edge_per_kp: float = EDGE_PER_KP) -> float:
    """How far the place is on the equator side of the edge (km); 0 or less when it is under or poleward of it."""
    return (edge_latitude(kp, edge_at_kp0, edge_per_kp) - abs(magnetic_lat)) * KM_PER_DEGREE


def activity(magnetic_lat: float, kp: float, edge_at_kp0: float = EDGE_AT_KP0, edge_per_kp: float = EDGE_PER_KP) -> float:
    """1 under the aurora, falling linearly to 0 at ``REACH_KM`` on the equator side of its edge."""
    distance = distance_to_edge_km(magnetic_lat, kp, edge_at_kp0, edge_per_kp)
    if distance <= 0.0:
        return 1.0
    return max(0.0, 1.0 - distance / REACH_KM)


def darkness(sun_altitude: float) -> float:
    """1 in the dark night (Sun at -18 degrees or lower), 0 above -6 degrees, linear in between."""
    if sun_altitude <= DARK_SUN_ALTITUDE:
        return 1.0
    if sun_altitude >= TWILIGHT_SUN_ALTITUDE:
        return 0.0
    return (TWILIGHT_SUN_ALTITUDE - sun_altitude) / (TWILIGHT_SUN_ALTITUDE - DARK_SUN_ALTITUDE)


def moon_factor(moon_altitude: float, illuminated_fraction: float) -> float:
    """1 when the Moon is below the horizon; up to ``1 - MOON_WEIGHT`` with a full Moon above it."""
    if moon_altitude <= 0.0:
        return 1.0
    return 1.0 - MOON_WEIGHT * max(0.0, min(1.0, illuminated_fraction))


def cloud_factor(cloud_cover_pct: Optional[float]) -> Optional[float]:
    """The clear share of the sky, ``None`` when the cloud cover is unknown."""
    if cloud_cover_pct is None:
        return None
    return 1.0 - max(0.0, min(100.0, cloud_cover_pct)) / 100.0


def level_of(chance: float) -> str:
    """The word for a chance: ``likely`` from 0.5, ``possible`` from 0.2, ``unlikely`` from 0.05, else ``no``."""
    for floor, word in LEVELS:
        if chance >= floor and (floor > 0.0 or chance > 0.0):
            return word
    return "no"


def sun_and_moon(lat: float, lon: float, when_utc: float) -> tuple[float, float, float]:
    """Altitude of the Sun, altitude of the Moon (degrees, refraction included) and the illuminated fraction of the Moon, by astronomy-engine."""
    import astronomy

    moment = datetime.fromtimestamp(when_utc, tz=timezone.utc)
    time = astronomy.Time.Make(moment.year, moment.month, moment.day, moment.hour, moment.minute, moment.second + moment.microsecond / 1e6)
    observer = astronomy.Observer(lat, lon, 0.0)
    altitudes: list[float] = []
    for body in (astronomy.Body.Sun, astronomy.Body.Moon):
        equator = astronomy.Equator(body, time, observer, True, True)
        horizon = astronomy.Horizon(time, observer, equator.ra, equator.dec, astronomy.Refraction.Normal)
        altitudes.append(horizon.altitude)
    fraction = astronomy.Illumination(astronomy.Body.Moon, time).phase_fraction
    return altitudes[0], altitudes[1], fraction
