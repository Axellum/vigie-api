"""The weather entity of the place (only when the option "Weather and card" is ticked, ADR 0042 of the API).

Its values are those of ``/v1/point`` as the instance gives them: in France the models of Météo-France (AROME, ARPEGE, AROME
Outre-mer), elsewhere Open-Meteo, said by the credit (``attribution``). A value the answer does not give is unknown.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from homeassistant.components.weather import Forecast, WeatherEntity, WeatherEntityFeature
from homeassistant.const import UnitOfPrecipitationDepth, UnitOfPressure, UnitOfSpeed, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import VigieConfigEntry
from .entity import device_info
from .weather_data import VigieWeatherCoordinator, condition_of, is_day_at, utc_datetime

PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: VigieConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    """Add the weather entity when the option is ticked."""
    if entry.runtime_data.weather is not None:
        async_add_entities([VigieWeather(entry.runtime_data.weather)])


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


class VigieWeather(CoordinatorEntity[VigieWeatherCoordinator], WeatherEntity):
    """The current weather, the days and (when the instance gives them) the hours of the place."""

    _attr_has_entity_name = True
    _attr_name = None  # the entity is named after its device: the watched place
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_pressure_unit = UnitOfPressure.HPA
    _attr_native_wind_speed_unit = UnitOfSpeed.KILOMETERS_PER_HOUR
    _attr_native_precipitation_unit = UnitOfPrecipitationDepth.MILLIMETERS

    def __init__(self, coordinator: VigieWeatherCoordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_weather"
        self._attr_device_info = device_info(entry, coordinator.client.base_url)

    @property
    def _point(self) -> dict[str, Any]:
        data = self.coordinator.data
        return data.point if data is not None else {}

    @property
    def _current(self) -> dict[str, Any]:
        current = self._point.get("current")
        return current if isinstance(current, dict) else {}

    def _hourly(self) -> list[dict[str, Any]]:
        hourly = self._point.get("hourly")
        return [hour for hour in hourly if isinstance(hour, dict) and isinstance(hour.get("time_utc"), int)] if isinstance(hourly, list) else []

    @property
    def supported_features(self) -> WeatherEntityFeature:
        """Days always; hours only when the instance gives them (an instance without them gives none)."""
        features = WeatherEntityFeature.FORECAST_DAILY
        if self._hourly():
            features |= WeatherEntityFeature.FORECAST_HOURLY
        return features

    @property
    def attribution(self) -> str | None:
        """The credit of the weather provider of the place, as the API words it."""
        value = self._point.get("weather_attribution")
        return value if isinstance(value, str) else None

    def _is_day(self) -> bool | None:
        hourly = self._hourly()
        if hourly and isinstance(hourly[0].get("is_day"), bool):
            return hourly[0]["is_day"]
        coordinator = self.coordinator
        return is_day_at(coordinator.latitude, coordinator.longitude, datetime.now(timezone.utc))

    @property
    def condition(self) -> str | None:
        return condition_of(self._current.get("weather_code"), self._is_day())

    @property
    def native_temperature(self) -> float | None:
        return _number(self._current.get("temp_c"))

    @property
    def native_apparent_temperature(self) -> float | None:
        return _number(self._current.get("feels_like_c"))

    @property
    def humidity(self) -> float | None:
        return _number(self._current.get("humidity_pct"))

    @property
    def native_pressure(self) -> float | None:
        return _number(self._current.get("pressure_hpa"))

    @property
    def native_wind_speed(self) -> float | None:
        return _number(self._current.get("wind_kmh"))

    @property
    def native_wind_gust_speed(self) -> float | None:
        return _number(self._current.get("gust_kmh"))

    @property
    def wind_bearing(self) -> float | None:
        return _number(self._current.get("wind_deg"))

    @property
    def uv_index(self) -> float | None:
        return _number(self._current.get("uv_index"))

    def _place_zone(self) -> timezone:
        offset = self._point.get("utc_offset_s")
        return timezone(timedelta(seconds=offset)) if isinstance(offset, int) and not isinstance(offset, bool) else timezone.utc

    async def async_forecast_daily(self) -> list[Forecast] | None:
        """The days of the answer: their date is the place's own (midnight, at its UTC offset)."""
        days = self._point.get("daily")
        if not isinstance(days, list):
            return None
        zone = self._place_zone()
        forecasts: list[Forecast] = []
        for day in days:
            if not isinstance(day, dict) or not isinstance(day.get("date"), str):
                continue
            try:
                when = datetime.combine(date.fromisoformat(day["date"]), datetime.min.time(), tzinfo=zone)
            except ValueError:
                continue
            forecasts.append(
                Forecast(
                    datetime=when.isoformat(),
                    condition=condition_of(day.get("code"), True),
                    native_temperature=_number(day.get("tmax")),
                    native_templow=_number(day.get("tmin")),
                    native_precipitation=_number(day.get("rain_mm")),
                    precipitation_probability=_number(day.get("rain_probability_pct")),
                    native_wind_speed=_number(day.get("wind_max")),
                    uv_index=_number(day.get("uv_max")),
                )
            )
        return forecasts

    async def async_forecast_hourly(self) -> list[Forecast] | None:
        """The hours of the answer, when the instance gives them."""
        hours = self._hourly()
        if not hours:
            return None
        return [
            Forecast(
                datetime=utc_datetime(hour["time_utc"]).isoformat(),
                condition=condition_of(hour.get("weather_code"), hour.get("is_day") if isinstance(hour.get("is_day"), bool) else None),
                native_temperature=_number(hour.get("temp_c")),
                native_apparent_temperature=_number(hour.get("feels_like_c")),
                native_dew_point=_number(hour.get("dew_point_c")),
                native_precipitation=_number(hour.get("precipitation_mm")),
                precipitation_probability=_number(hour.get("precipitation_probability_pct")),
                native_wind_speed=_number(hour.get("wind_kmh")),
                native_wind_gust_speed=_number(hour.get("wind_gust_kmh")),
                wind_bearing=_number(hour.get("wind_deg")),
                cloud_coverage=_number(hour.get("cloud_cover_pct")),
                native_pressure=_number(hour.get("pressure_hpa")),
                uv_index=_number(hour.get("uv_index")),
            )
            for hour in hours
        ]

    @callback
    def _handle_coordinator_update(self) -> None:
        """A new read: tell the subscribers of the forecasts too."""
        super()._handle_coordinator_update()
        self.coordinator.config_entry.async_create_task(self.hass, self.async_update_listeners(("daily", "hourly")))
