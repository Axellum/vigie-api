"""The client of a Vigie API instance: five calls, no state."""

from __future__ import annotations

from typing import Any

import aiohttp
from yarl import URL

from .const import EARTHQUAKES_TIMEOUT_S, HEALTH_TIMEOUT_S, PREALERT_TIMEOUT_S, STREAM_HEARTBEAT_S, VIGILANCES_TIMEOUT_S

HEALTH_PATH = "/v1/health"
VIGILANCES_PATH = "/v1/vigilances"
STREAM_PATH = "/v1/stream"
EARTHQUAKES_PATH = "/v1/earthquakes"
PREALERT_PATH = "/v1/experimental/prealert"

# Decimals of the position sent to the API: about one metre. The stream rounds a followed point to four.
POSITION_DECIMALS = 5


class VigieError(Exception):
    """Base class of the errors of the client."""


class VigieConnectionError(VigieError):
    """The instance could not be reached (refused, timed out, DNS)."""


class VigieResponseError(VigieError):
    """The instance answered, but not with what a Vigie API says."""


def normalize_url(raw: str) -> str:
    """The address of an instance as it is stored: a scheme, a host, an optional path prefix, no trailing slash.

    ``vigie.local:8080`` becomes ``http://vigie.local:8080``. Raises ``ValueError`` for anything that is not an
    http(s) address with a host, and for an address that carries credentials, a query or a fragment.
    """
    text = raw.strip()
    if not text:
        raise ValueError("empty address")
    if "://" not in text:
        text = f"http://{text}"
    try:
        url = URL(text)
    except ValueError as err:
        raise ValueError("not an address") from err
    if url.scheme not in ("http", "https") or not url.host:
        raise ValueError("not an http(s) address")
    if url.user or url.password or url.raw_query_string or url.raw_fragment:
        raise ValueError("credentials, query and fragment are not accepted")
    return str(url).rstrip("/")


class VigieClient:
    """Reads a Vigie API instance with the session of Home Assistant."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")

    @property
    def base_url(self) -> str:
        """The address of the instance, without a trailing slash."""
        return self._base_url

    async def _get_json(self, path: str, params: dict[str, str] | None, timeout_s: float) -> Any:
        url = f"{self._base_url}{path}"
        try:
            async with self._session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=timeout_s)) as response:
                if 400 <= response.status < 500:
                    raise VigieResponseError(f"{path}: HTTP {response.status}: this is not a Vigie API, or it is too old for this route")
                response.raise_for_status()
                return await response.json(content_type=None)
        except VigieError:
            raise
        except aiohttp.ClientResponseError as err:
            raise VigieConnectionError(f"{path}: HTTP {err.status}") from err
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise VigieConnectionError(f"{path}: {type(err).__name__}: {err}") from err
        except ValueError as err:  # the body is not JSON
            raise VigieResponseError(f"{path}: the answer is not JSON") from err

    async def health(self) -> dict[str, Any]:
        """``GET /v1/health``. Raises ``VigieResponseError`` when the answer does not look like a Vigie API's."""
        payload = await self._get_json(HEALTH_PATH, None, HEALTH_TIMEOUT_S)
        if not isinstance(payload, dict) or not isinstance(payload.get("status"), str) or not isinstance(payload.get("version"), str):
            raise VigieResponseError("/v1/health: this does not look like a Vigie API")
        return payload

    async def vigilances(self, lat: float, lon: float, river_radius_km: float) -> Any:
        """``GET /v1/vigilances`` for a point: the raw JSON (the caller parses it)."""
        params = {
            "lat": f"{lat:.{POSITION_DECIMALS}f}",
            "lon": f"{lon:.{POSITION_DECIMALS}f}",
            "river_radius_km": f"{river_radius_km:g}",
        }
        return await self._get_json(VIGILANCES_PATH, params, VIGILANCES_TIMEOUT_S)

    async def earthquakes(self, lat: float, lon: float, radius_km: float, min_magnitude: float, hours: float) -> Any:
        """``GET /v1/earthquakes`` around a point: the raw JSON (the caller parses it). No weather is asked of the instance."""
        params = {
            "lat": f"{lat:.{POSITION_DECIMALS}f}",
            "lon": f"{lon:.{POSITION_DECIMALS}f}",
            "radius_km": f"{radius_km:g}",
            "min_magnitude": f"{min_magnitude:g}",
            "hours": f"{hours:g}",
        }
        return await self._get_json(EARTHQUAKES_PATH, params, EARTHQUAKES_TIMEOUT_S)

    async def prealert(self, lat: float, lon: float) -> Any:
        """``GET /v1/experimental/prealert`` for a point (experimental, unofficial): the raw JSON (the caller parses it).

        An instance that does not run the pre-alert answers 404, and a point outside Turkey 422: both raise ``VigieResponseError``.
        """
        params = {"lat": f"{lat:.{POSITION_DECIMALS}f}", "lon": f"{lon:.{POSITION_DECIMALS}f}"}
        return await self._get_json(PREALERT_PATH, params, PREALERT_TIMEOUT_S)

    def stream_url(self) -> str:
        """The WebSocket address of the push stream."""
        url = URL(self._base_url)
        scheme = "wss" if url.scheme == "https" else "ws"
        return str(url.with_scheme(scheme).with_path(f"{url.path.rstrip('/')}{STREAM_PATH}"))

    def connect_stream(self) -> Any:
        """Open the push stream (an async context manager): the caller follows its point on it."""
        return self._session.ws_connect(self.stream_url(), heartbeat=STREAM_HEARTBEAT_S)
