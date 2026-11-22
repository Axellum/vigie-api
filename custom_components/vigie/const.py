"""Constants of the Vigie integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "vigie"
NAME: Final = "Vigie"

# Config entry data: where the instance is and which place is watched.
CONF_LOCATION: Final = "location"

# Config entry options.
CONF_SCAN_INTERVAL_MIN: Final = "scan_interval"
CONF_RIVER_RADIUS_KM: Final = "river_radius_km"
CONF_QUAKE_RADIUS_KM: Final = "quake_radius_km"
CONF_QUAKE_MIN_MAGNITUDE: Final = "quake_min_magnitude"
CONF_EXPERIMENTAL_PREALERT: Final = "experimental_prealert"
CONF_WEATHER_AND_CARD: Final = "weather_and_card"

# The public instance, suggested in the address field of a new entry (any other instance can be typed instead).
PUBLIC_INSTANCE_URL: Final = "https://vigie.auxfilsdesidees.fr"

# Polling: the poll is the fallback of the push, and the whole mechanism when the instance has no scheduler.
DEFAULT_SCAN_INTERVAL_MIN: Final = 5
MIN_SCAN_INTERVAL_MIN: Final = 1
MAX_SCAN_INTERVAL_MIN: Final = 60

# How far a river section (Vigicrues, France) still counts. The API's own default is 5 km and its maximum 50 km.
DEFAULT_RIVER_RADIUS_KM: Final = 5
MIN_RIVER_RADIUS_KM: Final = 1
MAX_RIVER_RADIUS_KM: Final = 50

# The earthquakes near the place (`/v1/earthquakes`, USGS and EMSC): a layer of their own, never mixed with the warnings.
# The feeds the API reads list nothing below magnitude 4.5, so a smaller minimum would find nothing more.
DEFAULT_QUAKE_RADIUS_KM: Final = 300
MIN_QUAKE_RADIUS_KM: Final = 10
MAX_QUAKE_RADIUS_KM: Final = 3000
DEFAULT_QUAKE_MIN_MAGNITUDE: Final = 4.5
MIN_QUAKE_MIN_MAGNITUDE: Final = 4.5
MAX_QUAKE_MIN_MAGNITUDE: Final = 9.0
QUAKE_WINDOW_HOURS: Final = 24
EVENT_NEW_EARTHQUAKE: Final = "new_earthquake"
MAX_LISTED_QUAKES: Final = 10
QUAKE_ATTRIBUTES_BUDGET_BYTES: Final = 8_000

# The experimental, unofficial pre-alert for Turkey (`/v1/experimental/prealert`, ADR 0011 of the API): read only when the
# option is ticked (off by default), and only from an instance that runs it. Its levels are Vigie's words, not official colours.
# The instance reads two forecast runs a day: one read every half hour is enough.
DEFAULT_EXPERIMENTAL_PREALERT: Final = False
PREALERT_LEVEL_STATES: Final = ("none", "watch", "strong", "severe")
PREALERT_INTERVAL: Final = timedelta(minutes=30)

# The weather of the place and what the dashboard card shows (ADR 0042 of the API), on by default and untickable to keep the
# warnings only. `/v1/point` is cached by the instance for 7 to 15 minutes (its Cache-Control), so one read every 10 minutes is
# enough; the tide, the normals of the month and the list of spots change far more slowly and are read again far less often.
DEFAULT_WEATHER_AND_CARD: Final = True
WEATHER_INTERVAL: Final = timedelta(minutes=10)
TIDES_REFRESH: Final = timedelta(hours=6)
NORMALS_REFRESH: Final = timedelta(hours=12)
SPOTS_REFRESH: Final = timedelta(hours=24)
ACTIVITIES_REFRESH: Final = timedelta(minutes=30)
# The profile asked of `/v1/point` (its full answer, not the compact one of the screens, and the sea when there is one).
POINT_DEVICE_TYPE: Final = "generic"
# Half the side of the box around the place in which a measuring station's wind is looked for (degrees), and the spots offered
# to the card: those within this distance of the place, nearest first.
STATION_BOX_DEG: Final = 0.3
MAX_SPOT_DISTANCE_KM: Final = 60.0
MAX_LISTED_SPOTS: Final = 12

# Time given to an answer. The first read of a country can fetch a feed and up to 40 CAP documents.
HEALTH_TIMEOUT_S: Final = 10
VIGILANCES_TIMEOUT_S: Final = 60
EARTHQUAKES_TIMEOUT_S: Final = 30
PREALERT_TIMEOUT_S: Final = 30
WEATHER_TIMEOUT_S: Final = 30

# The push stream: re-asked after a failure with a growing delay (seconds), and looked at again from time to time
# when the instance does not run its scheduler (no push is sent then).
STREAM_MIN_DELAY_S: Final = 5
STREAM_MAX_DELAY_S: Final = 300
STREAM_STABLE_AFTER_S: Final = 60
STREAM_RECHECK_PUSH_S: Final = 900
STREAM_HEARTBEAT_S: Final = 30

# What the entities show.
STATE_NONE: Final = "none"
MAX_LEVEL_STATES: Final = ("none", "yellow", "orange", "red")
EVENT_NEW: Final = "new"
EVENT_UPGRADED: Final = "upgraded"
EVENT_ENDED: Final = "ended"
EVENT_TYPES: Final = (EVENT_NEW, EVENT_UPGRADED, EVENT_ENDED)

# The attributes of the maximum-level sensor stay well under the 16 KiB the recorder keeps (MAX_STATE_ATTRS_BYTES):
# at most MAX_LISTED_VIGILANCES warnings are listed, fewer when the text would pass ATTRIBUTES_BUDGET_BYTES.
MAX_LISTED_VIGILANCES: Final = 20
ATTRIBUTES_BUDGET_BYTES: Final = 12_000
MAX_TEXT_CHARS: Final = 300

DEFAULT_UPDATE_INTERVAL: Final = timedelta(minutes=DEFAULT_SCAN_INTERVAL_MIN)
