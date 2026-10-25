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

# Time given to an answer. The first read of a country can fetch a feed and up to 40 CAP documents.
HEALTH_TIMEOUT_S: Final = 10
VIGILANCES_TIMEOUT_S: Final = 60
EARTHQUAKES_TIMEOUT_S: Final = 30

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
