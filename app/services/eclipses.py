"""The solar eclipse service: NASA's catalogue as NASA states it, and for a place or a map what Vigie works out from NASA's elements.

No request leaves for an eclipse: the catalogue and the elements are bundled (``app/data/solar-eclipses.json.gz``). The one
exception is the clouds of a place, asked only when the eclipse's maximum falls within the hourly forecast the weather already
requests (48 hours; ``Aggregator.clouds_for_aurora``). The maps take a second or two to draw: they run in a thread and the last
few are kept.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

import numpy as np

from app import clock
from app.attribution import ECLIPSES, credit_text, merge_credits
from app.models.sky import (
    EclipseClouds,
    EclipseContact,
    EclipseDetail,
    EclipseFrame,
    EclipseHere,
    EclipseList,
    EclipseMapCollection,
    EclipseMapFeature,
    EclipseSummary,
)
from app.services import eclipse_map
from app.services.eclipse_besselian import (
    LocalCircumstances,
    Places,
    SolarEclipse,
    catalogue,
    eclipse_on,
    local_circumstances,
    obscuration,
    shadow,
)

CloudsFetcher = Callable[[float, float], Awaitable[tuple[Optional[list[tuple[int, Optional[float]]]], Optional[int], Optional[str]]]]

FORECAST_REACH_S = 48 * 3600  # the hourly cloud cover the weather request carries
FRAMES = 60  # the disc between C1 and C4, plus each contact
MAPS_KEPT = 4
DEFAULT_YEARS = 10


def summary(eclipse: SolarEclipse, here: Optional[EclipseHere] = None) -> EclipseSummary:
    return EclipseSummary(
        id=eclipse.date,
        kind=eclipse.kind,
        type_code=eclipse.type_code,
        saros=eclipse.saros,
        greatest_utc=round(eclipse.greatest_utc, 1),
        greatest_lat=eclipse.greatest_lat,
        greatest_lon=eclipse.greatest_lon,
        sun_altitude_deg=eclipse.sun_altitude_deg,
        magnitude=eclipse.magnitude,
        gamma=eclipse.gamma,
        path_width_km=eclipse.path_width_km,
        central_duration_s=eclipse.central_duration_s,
        delta_t_s=eclipse.delta_t_s,
        elements_source=eclipse.elements_source,
        here=here,
    )


def here_of(eclipse: SolarEclipse, lat: float, lon: float, with_frames: bool = False) -> EclipseHere:
    """What ``(lat, lon)`` sees of ``eclipse``."""
    local: LocalCircumstances = local_circumstances(eclipse, lat, lon)
    contacts = [
        EclipseContact(
            name=c.name,
            time_utc=round(c.time_utc, 1),
            sun_altitude_deg=c.sun_altitude_deg,
            sun_azimuth_deg=c.sun_azimuth_deg,
            sun_up=c.sun_altitude_deg > 0.0,
        )
        for c in local.contacts
    ]
    if local.kind == "none":
        return EclipseHere(lat=lat, lon=lon, kind="none", visible=False, contacts=[])
    return EclipseHere(
        lat=lat,
        lon=lon,
        kind=local.kind,
        visible=any(c.sun_up for c in contacts) or local.sunrise_utc is not None or local.sunset_utc is not None,
        magnitude=round(local.magnitude, 4),
        obscuration=round(local.obscuration, 4),
        central_duration_s=None if local.central_duration_s is None else round(local.central_duration_s, 1),
        contacts=contacts,
        sunrise_utc=None if local.sunrise_utc is None else round(local.sunrise_utc, 1),
        sunset_utc=None if local.sunset_utc is None else round(local.sunset_utc, 1),
        frames=frames_of(eclipse, lat, lon, contacts) if with_frames else None,
    )


def frames_of(eclipse: SolarEclipse, lat: float, lon: float, contacts: list[EclipseContact]) -> Optional[list[EclipseFrame]]:
    """The Moon on the Sun's disc at ``FRAMES`` even steps from the first contact to the last, and at each contact."""
    names = {c.name: c.time_utc for c in contacts}
    if "partial_begin" not in names or "partial_end" not in names:
        return None
    steps = np.linspace(names["partial_begin"], names["partial_end"], FRAMES + 1)
    times = np.unique(np.concatenate([steps, np.array([c.time_utc for c in contacts])]))
    t = np.array([eclipse.hours(float(u)) for u in times])
    places = Places.at(np.full_like(t, lat), np.full_like(t, lon))
    s = shadow(eclipse, t, places)
    scale = 2.0 / (s.l1 + s.l2)
    hidden = obscuration(s)
    return [
        EclipseFrame(
            time_utc=round(float(times[i]), 1),
            moon_east=round(float(s.u[i] * scale[i]), 4),
            moon_north=round(float(s.v[i] * scale[i]), 4),
            moon_radius=round(float((s.l1[i] - s.l2[i]) / (s.l1[i] + s.l2[i])), 4),
            obscuration=round(float(hidden[i]), 4),
            sun_altitude_deg=round(float(s.sun_altitude[i]), 2),
            zenith_angle_deg=round(float(s.parallactic[i]), 1),
        )
        for i in range(len(times))
    ]


class EclipseService:
    """The routes' work. ``clouds`` gives the hourly cloud cover of a place (the aggregator's), or is ``None``."""

    def __init__(self, clouds: Optional[CloudsFetcher] = None) -> None:
        self.clouds = clouds
        self._maps: OrderedDict[str, EclipseMapCollection] = OrderedDict()
        self._lock = asyncio.Lock()

    @staticmethod
    def credit() -> str:
        return credit_text(*ECLIPSES)

    async def eclipses(
        self, year_from: Optional[int] = None, year_to: Optional[int] = None, lat: Optional[float] = None, lon: Optional[float] = None
    ) -> EclipseList:
        now = clock.wall()
        first = year_from if year_from is not None else datetime.fromtimestamp(now, timezone.utc).year
        last = year_to if year_to is not None else first + DEFAULT_YEARS - 1
        chosen = [e for e in catalogue() if first <= int(e.date[:4]) <= last]

        def work() -> list[EclipseSummary]:
            return [summary(e, None if lat is None or lon is None else here_of(e, lat, lon)) for e in chosen]

        return EclipseList(
            generated_utc=int(now), year_from=first, year_to=last, eclipses=await clock.run_blocking(work), attribution=self.credit()
        )

    async def eclipse(self, date: str, lat: Optional[float] = None, lon: Optional[float] = None) -> Optional[EclipseDetail]:
        found = eclipse_on(date)
        if found is None:
            return None
        now = clock.wall()
        here: Optional[EclipseHere] = None
        attribution = self.credit()
        if lat is not None and lon is not None:
            here = await clock.run_blocking(here_of, found, lat, lon, True)
            here, weather_credit = await self._with_clouds(here, now)
            attribution = merge_credits(attribution, weather_credit) or attribution
        base = summary(found, here)
        return EclipseDetail(**base.model_dump(), generated_utc=int(now), attribution=attribution)

    async def _with_clouds(self, here: EclipseHere, now: float) -> tuple[EclipseHere, Optional[str]]:
        maximum = next((c.time_utc for c in here.contacts if c.name == "maximum"), None)
        if maximum is None:
            return here, None
        if not now <= maximum <= now + FORECAST_REACH_S:
            note = "the eclipse is past" if maximum < now else "the hourly cloud forecast reaches 48 hours ahead"
            return here.model_copy(update={"clouds_note": note}), None
        if self.clouds is None:
            return here.model_copy(update={"clouds_note": "no weather source on this instance"}), None
        try:
            hourly, _, credit = await self.clouds(here.lat, here.lon)
        except Exception:
            hourly, credit = None, None
        if not hourly or credit is None:
            return here.model_copy(update={"clouds_note": "the weather source did not answer"}), None
        hour = int(maximum // 3600 * 3600)
        value = next((pct for time_utc, pct in hourly if time_utc == hour), None)
        if not any(time_utc == hour for time_utc, _ in hourly):
            return here.model_copy(update={"clouds_note": "the forecast has no value for that hour"}), None
        clouds = EclipseClouds(time_utc=hour, cloud_cover_pct=value, attribution=credit)
        return here.model_copy(update={"clouds": clouds}), credit

    async def eclipse_map(self, date: str) -> Optional[EclipseMapCollection]:
        found = eclipse_on(date)
        if found is None:
            return None
        async with self._lock:
            kept = self._maps.get(date)
            if kept is not None:
                self._maps.move_to_end(date)
                return kept
            drawn = await clock.run_blocking(self._draw, found)
            self._maps[date] = drawn
            while len(self._maps) > MAPS_KEPT:
                self._maps.popitem(last=False)
            return drawn

    def _draw(self, eclipse: SolarEclipse) -> EclipseMapCollection:
        features = [
            EclipseMapFeature(geometry=geometry, properties={"kind": "obscuration", "min": low, "max": high})
            for low, high, geometry in eclipse_map.obscuration_bands(eclipse)
        ]
        path = eclipse_map.path_geometry(eclipse)
        if path is not None:
            features.append(EclipseMapFeature(geometry=path, properties={"kind": "path", "eclipse": eclipse.kind}))
        line = eclipse_map.central_line_geometry(eclipse)
        if line is not None:
            features.append(EclipseMapFeature(geometry=line, properties={"kind": "central_line"}))
        return EclipseMapCollection(
            id=eclipse.date, features=features, levels=list(eclipse_map.OBSCURATION_LEVELS), attribution=self.credit()
        )
