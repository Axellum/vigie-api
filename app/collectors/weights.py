"""Weight of an Open-Meteo request in the quota of the free API.

Open-Meteo does not count HTTP requests but "API calls", and a request that asks for a lot counts as
several. What Open-Meteo publishes:

- https://open-meteo.com/en/pricing: "Typically, one API call corresponds to one HTTP API request",
  but a request for "more than 10 weather variables or extending over a period of more than 2 weeks for
  a single location" is "considered multiple API calls", with fractional counts: 2 weeks of data with
  15 variables is 1.5 calls, 4 weeks of data (with the same 15 variables) is 3.0 calls.
- https://openmeteo.substack.com/p/weather-data-for-multiple-locations: the weight is the number of
  locations times the number of days over 14 times the number of variables over 10, the variables
  being "the sum of hourly, daily, and current weather variables".

The rule applied here is ``locations * max(1, variables / 10) * max(1, days / 14)``. Each factor is at
least 1 because the pricing page counts a request below the limits as one call, and a developer of
Open-Meteo wrote in the blog comments that the weight uses a minimum of 14 days. The 15-minute
variables (``minutely_15``) are counted with the others, although the blog does not name them: the
weight can only be over-estimated, never under-estimated. The number of days is ``forecast_days`` plus
``past_days``. Open-Meteo gives no formula for the models, which this project does not select.

Both the quota of the free API (10,000 calls a day, 5,000 an hour, 600 a minute) and the call counts of
``GET /v1/providers`` use this weight.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Optional

# The parameters of a request that list the variables it asks for.
VARIABLE_PARAMETERS = ("current", "hourly", "daily", "minutely_15")

# Variables and days one API call covers without being counted as several.
VARIABLES_PER_CALL = 10
DAYS_PER_CALL = 14


def _names(value: Optional[str]) -> list[str]:
    """The comma-separated names of a parameter (``latitude=1,2`` is two locations)."""
    return [name for name in (value or "").split(",") if name.strip()]


def open_meteo_weight(params: Mapping[str, str], default_forecast_days: int = 7) -> float:
    """Weight, in API calls, of a request with these query parameters.

    ``default_forecast_days`` is the length Open-Meteo answers when the request names none (7 for the
    forecast and the marine APIs, 5 for air quality, 92 for river discharge).
    """
    variables = sum(len(_names(params.get(name))) for name in VARIABLE_PARAMETERS)
    try:
        forecast_days = int(params.get("forecast_days", default_forecast_days))
        past_days = int(params.get("past_days", 0))
    except ValueError:
        forecast_days, past_days = default_forecast_days, 0
    locations = max(1, len(_names(params.get("latitude"))))
    weight = (
        locations
        * max(1.0, variables / VARIABLES_PER_CALL)
        * max(1.0, (forecast_days + past_days) / DAYS_PER_CALL)
    )
    return round(weight, 4)
