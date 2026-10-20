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

# Polling: the poll is the fallback of the push, and the whole mechanism when the instance has no scheduler.
DEFAULT_SCAN_INTERVAL_MIN: Final = 5
MIN_SCAN_INTERVAL_MIN: Final = 1
MAX_SCAN_INTERVAL_MIN: Final = 60

# How far a river section (Vigicrues, France) still counts. The API's own default is 5 km and its maximum 50 km.
DEFAULT_RIVER_RADIUS_KM: Final = 5
MIN_RIVER_RADIUS_KM: Final = 1
MAX_RIVER_RADIUS_KM: Final = 50

# Time given to an answer. The first read of a country can fetch a feed and up to 40 CAP documents.
HEALTH_TIMEOUT_S: Final = 10
VIGILANCES_TIMEOUT_S: Final = 60

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
