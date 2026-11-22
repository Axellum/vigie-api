"""The maximum level of the official warnings that cover the place, the strongest earthquake near it (a layer of its own), and the
experimental, unofficial pre-alert for Turkey when its option is ticked."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfPrecipitationDepth, UnitOfSpeed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import MAX_LEVEL_STATES, PREALERT_LEVEL_STATES
from .coordinator import VigieConfigEntry, VigieCoordinator, VigiePrealertCoordinator, VigieQuakeCoordinator
from .entity import VigieEntity, VigiePrealertEntity, VigieQuakeEntity, VigieWeatherDataEntity
from .models import build_attributes
from .prealert import build_prealert_attributes
from .quakes import build_quake_attributes
from .weather_data import (
    Gust,
    VigieWeatherCoordinator,
    next_extreme,
    rain_next_hour,
    rain_slots,
    strongest_gust,
    utc_datetime,
)

PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: VigieConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    """Add the sensors of the entry."""
    runtime = entry.runtime_data
    entities: list[SensorEntity] = [VigieMaxLevelSensor(runtime.coordinator), VigieStrongestQuakeSensor(runtime.quakes)]
    if runtime.prealert is not None:
        entities.append(VigiePrealertSensor(runtime.prealert))
    if runtime.weather is not None:
        entities += [VigieRainNextHourSensor(runtime.weather), VigieGustSensor(runtime.weather), VigieNextHighTideSensor(runtime.weather)]
    async_add_entities(entities)


def _credit(value: Any) -> str | None:
    return value if isinstance(value, str) else None


class VigieRainNextHourSensor(VigieWeatherDataEntity, SensorEntity):
    """The rain of the next hour (mm), the sum of the steps of the API's rain nowcast; unknown when a step is unknown.

    In metropolitan France the steps come from Météo-France's 5-minute rain (PIAF) summed by quarter of an hour; elsewhere from
    Open-Meteo's 15-minute forecast; in the overseas territories the API gives none (unknown).
    """

    _attr_translation_key = "rain_next_hour"
    _attr_device_class = SensorDeviceClass.PRECIPITATION
    _attr_native_unit_of_measurement = UnitOfPrecipitationDepth.MILLIMETERS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: VigieWeatherCoordinator) -> None:
        super().__init__(coordinator, "rain_next_hour")

    @property
    def _nowcast(self) -> dict[str, Any]:
        nowcast = self.coordinator.data.point.get("rain_nowcast") if self.coordinator.data is not None else None
        return nowcast if isinstance(nowcast, dict) else {}

    @property
    def native_value(self) -> float | None:
        return rain_next_hour(self.coordinator.data.point) if self.coordinator.data is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The steps as the API gives them, whether it expects rain within the hour, and the strongest rate. In metropolitan
        France also the twelve 5-minute rates of PIAF and the nine slots of the former Météo-France API (`slots`: 5 minutes
        for the first half hour, then 10); `None` elsewhere."""
        nowcast = self._nowcast
        point = self.coordinator.data.point if self.coordinator.data is not None else None
        return {
            "interval_minutes": nowcast.get("interval_minutes"),
            "mm_per_step": nowcast.get("mm_per_step"),
            "rain_expected_60m": nowcast.get("rain_expected_60m"),
            "max_mm_h": nowcast.get("max_mm_h"),
            "rate_5m_mm_h": nowcast.get("rate_5m_mm_h"),
            "slots": rain_slots(point),
        }

    @property
    def attribution(self) -> str | None:
        return _credit(self._nowcast.get("attribution"))


class VigieGustSensor(VigieWeatherDataEntity, SensorEntity):
    """The strongest gust of the next 6 hours of the hourly forecast, or, without it, of the short-term forecast (AROME-PI, in
    France: its own horizon, under 5 hours). Unknown when neither gives a gust."""

    _attr_translation_key = "strongest_gust_next_hours"
    _attr_device_class = SensorDeviceClass.WIND_SPEED
    _attr_native_unit_of_measurement = UnitOfSpeed.KILOMETERS_PER_HOUR
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: VigieWeatherCoordinator) -> None:
        super().__init__(coordinator, "strongest_gust_next_hours")

    def _gust(self) -> Gust | None:
        return strongest_gust(self.coordinator.data.point, int(time.time())) if self.coordinator.data is not None else None

    @property
    def native_value(self) -> float | None:
        gust = self._gust()
        return gust.kmh if gust is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """When the gust is expected, which forecast it was read in, and how far that forecast goes."""
        gust = self._gust()
        if gust is None:
            return {}
        return {"at": utc_datetime(gust.at_utc).isoformat(), "forecast": gust.source, "horizon_minutes": gust.horizon_minutes}

    @property
    def attribution(self) -> str | None:
        gust = self._gust()
        point = self.coordinator.data.point if self.coordinator.data is not None else {}
        if gust is not None and gust.source == "short_term" and isinstance(point.get("short_term"), dict):
            return _credit(point["short_term"].get("attribution"))
        return _credit(point.get("weather_attribution"))


class VigieNextHighTideSensor(VigieWeatherDataEntity, SensorEntity):
    """The time of the next high water at the place (EOT20 model of the API, calculated, not an official tide table); unknown
    inland, where the API has no tide."""

    _attr_translation_key = "next_high_tide"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: VigieWeatherCoordinator) -> None:
        super().__init__(coordinator, "next_high_tide")

    def _tides(self) -> dict[str, Any] | None:
        return self.coordinator.data.tides if self.coordinator.data is not None else None

    @property
    def native_value(self) -> datetime | None:
        high = next_extreme(self._tides(), "high", int(time.time()))
        return utc_datetime(high["time_utc"]) if high is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The height of that high water and the next low water (metres, relative to the model's mean sea level)."""
        now = int(time.time())
        high, low = next_extreme(self._tides(), "high", now), next_extreme(self._tides(), "low", now)
        if high is None and low is None:
            return {}
        return {
            "height_m": high.get("height_m") if high else None,
            "next_low_tide": utc_datetime(low["time_utc"]).isoformat() if low else None,
            "next_low_height_m": low.get("height_m") if low else None,
        }

    @property
    def attribution(self) -> str | None:
        tides = self._tides()
        return _credit(tides.get("attribution")) if tides is not None else None


class VigieMaxLevelSensor(VigieEntity, SensorEntity):
    """The highest level among the warnings in force or announced for the place.

    `none` means a source answered for the place and nothing was found. The state is unknown (not `none`) when no
    source covers the place, when every covering source is unavailable, when a warning cannot be graded, and when the
    sources list warnings that cannot be placed on a map (`not_matchable`).
    """

    _attr_translation_key = "max_level"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(MAX_LEVEL_STATES)

    def __init__(self, coordinator: VigieCoordinator) -> None:
        super().__init__(coordinator, "max_level")

    @property
    def native_value(self) -> str | None:
        """`none`, `yellow`, `orange` or `red`; `None` (unknown) when it cannot be told."""
        return self.coordinator.data.max_level

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The count, the level of each phenomenon, the state of the sources and a bounded list of the warnings."""
        return build_attributes(self.coordinator.data, self.hass.config.language)


class VigieStrongestQuakeSensor(VigieQuakeEntity, SensorEntity):
    """The magnitude of the strongest earthquake within the radius in the last day, as its sources reported it.

    Unknown when no earthquake matches: the attribute `count` then says 0 when a seismic source answered, and is null when
    none did. It is not an official warning, and it never changes the warning entities.
    """

    _attr_translation_key = "strongest_earthquake"
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: VigieQuakeCoordinator) -> None:
        super().__init__(coordinator, "strongest_earthquake")

    @property
    def native_value(self) -> float | None:
        """The magnitude, or `None` (unknown) when no earthquake matches."""
        data = self.coordinator.data
        strongest = data.strongest if data is not None else None
        return strongest.magnitude if strongest is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Where and when the strongest one was, the settings of the search, the sources, and a bounded list."""
        coordinator = self.coordinator
        if coordinator.data is None:
            return {}
        return build_quake_attributes(
            coordinator.data, radius_km=coordinator.radius_km, min_magnitude=coordinator.min_magnitude, hours=coordinator.hours
        )


class VigiePrealertSensor(VigiePrealertEntity, SensorEntity):
    """EXPERIMENTAL and UNOFFICIAL: the highest level of today's pre-alert in the province of Turkey the place is in.

    Levels computed by Vigie from the ECMWF forecast with thresholds of its own (a test being improved), never an official
    warning: the official warnings of Turkey are the MGM's (attribute `official_warnings_url`). Unknown when the run gave no
    level for today, and unavailable when the instance does not run the pre-alert or the place is not in Turkey.
    """

    _attr_translation_key = "experimental_prealert"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(PREALERT_LEVEL_STATES)

    def __init__(self, coordinator: VigiePrealertCoordinator) -> None:
        super().__init__(coordinator, "experimental_prealert")

    @property
    def native_value(self) -> str | None:
        """`none`, `watch`, `strong` or `severe` for today; `None` (unknown) when it cannot be told."""
        data = self.coordinator.data
        today = data.today if data is not None else None
        return today.level_word if today is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The notice, the province, the run, each day with its values and thresholds, and the link to the official warnings."""
        data = self.coordinator.data
        return build_prealert_attributes(data) if data is not None else {}
