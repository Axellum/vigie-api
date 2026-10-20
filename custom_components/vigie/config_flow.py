"""Config flow: the address of an instance and the place to watch; options: the poll and the river radius."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, CONF_NAME, CONF_URL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    LocationSelector,
    LocationSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import VigieClient, VigieConnectionError, VigieResponseError, normalize_url
from .const import (
    CONF_LOCATION,
    CONF_RIVER_RADIUS_KM,
    CONF_SCAN_INTERVAL_MIN,
    DEFAULT_RIVER_RADIUS_KM,
    DEFAULT_SCAN_INTERVAL_MIN,
    DOMAIN,
    MAX_RIVER_RADIUS_KM,
    MAX_SCAN_INTERVAL_MIN,
    MIN_RIVER_RADIUS_KM,
    MIN_SCAN_INTERVAL_MIN,
    NAME,
)


def unique_id_for(url: str, latitude: float, longitude: float) -> str:
    """One entry per instance and place: the same address and the same place (to about 11 m) is the same entry."""
    return f"{url}|{latitude:.4f}|{longitude:.4f}"


class VigieConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up one watched place on one Vigie API instance."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> VigieOptionsFlow:
        """The options of an entry: the poll interval and the river radius."""
        return VigieOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """First setup: name, address of the instance, place (the home zone by default)."""
        return await self._async_form("user", user_input, None)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change the address or the place of an entry (its entities keep their identity)."""
        return await self._async_form("reconfigure", user_input, self._get_reconfigure_entry())

    async def _async_form(self, step_id: str, user_input: dict[str, Any] | None, entry: ConfigEntry | None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                url = normalize_url(user_input[CONF_URL])
            except ValueError:
                errors[CONF_URL] = "invalid_url"
            else:
                if error := await self._async_check(url):
                    errors["base"] = error
                else:
                    location = user_input[CONF_LOCATION]
                    latitude, longitude = float(location[CONF_LATITUDE]), float(location[CONF_LONGITUDE])
                    unique_id = unique_id_for(url, latitude, longitude)
                    data = {CONF_URL: url, CONF_LATITUDE: latitude, CONF_LONGITUDE: longitude}
                    if entry is None:
                        await self.async_set_unique_id(unique_id)
                        self._abort_if_unique_id_configured()
                        return self.async_create_entry(title=user_input[CONF_NAME], data=data)
                    if any(other.entry_id != entry.entry_id and other.unique_id == unique_id for other in self._async_current_entries()):
                        return self.async_abort(reason="already_configured")
                    return self.async_update_reload_and_abort(entry, unique_id=unique_id, title=user_input[CONF_NAME], data=data)

        defaults = self._defaults(user_input, entry)
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): str,
                vol.Required(CONF_URL): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
                vol.Required(CONF_LOCATION): LocationSelector(LocationSelectorConfig(radius=False)),
            }
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, defaults),
            errors=errors,
        )

    def _defaults(self, user_input: dict[str, Any] | None, entry: ConfigEntry | None) -> dict[str, Any]:
        """What the form shows: what was just typed, else the entry, else the home zone of Home Assistant."""
        if user_input is not None:
            return user_input
        if entry is not None:
            return {
                CONF_NAME: entry.title,
                CONF_URL: entry.data[CONF_URL],
                CONF_LOCATION: {CONF_LATITUDE: entry.data[CONF_LATITUDE], CONF_LONGITUDE: entry.data[CONF_LONGITUDE]},
            }
        home = self.hass.states.get("zone.home")
        latitude = home.attributes.get(CONF_LATITUDE) if home else None
        longitude = home.attributes.get(CONF_LONGITUDE) if home else None
        return {
            CONF_NAME: f"{NAME} {self.hass.config.location_name}",
            CONF_LOCATION: {
                CONF_LATITUDE: latitude if latitude is not None else self.hass.config.latitude,
                CONF_LONGITUDE: longitude if longitude is not None else self.hass.config.longitude,
            },
        }

    async def _async_check(self, url: str) -> str:
        """An empty string when `/v1/health` of the address answers like a Vigie API, else the key of the error."""
        try:
            await VigieClient(async_get_clientsession(self.hass), url).health()
        except VigieConnectionError:
            return "cannot_connect"
        except VigieResponseError:
            return "invalid_response"
        return ""


class VigieOptionsFlow(OptionsFlowWithReload):
    """The poll interval and the river radius; the entry is reloaded when they change."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show or save the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL_MIN: int(round(user_input[CONF_SCAN_INTERVAL_MIN])),
                    CONF_RIVER_RADIUS_KM: int(round(user_input[CONF_RIVER_RADIUS_KM])),
                }
            )
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL_MIN): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL_MIN,
                        max=MAX_SCAN_INTERVAL_MIN,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="min",
                    )
                ),
                vol.Required(CONF_RIVER_RADIUS_KM): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_RIVER_RADIUS_KM,
                        max=MAX_RIVER_RADIUS_KM,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="km",
                    )
                ),
            }
        )
        current = {
            CONF_SCAN_INTERVAL_MIN: DEFAULT_SCAN_INTERVAL_MIN,
            CONF_RIVER_RADIUS_KM: DEFAULT_RIVER_RADIUS_KM,
            **self.config_entry.options,
        }
        return self.async_show_form(step_id="init", data_schema=self.add_suggested_values_to_schema(schema, current))
