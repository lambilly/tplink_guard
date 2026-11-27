"""Constants for the TPLink Guard integration."""

from __future__ import annotations

DOMAIN = "tplink_guard"

PLATFORMS: list[str] = ["sensor", "binary_sensor", "device_tracker"]

CONF_WHITELIST_PATH = "whitelist_path"
CONF_WATCHLIST_PATH = "watchlist_path"
CONF_MONITORED_PATH = "monitored_path"
CONF_MATCH_BY = "match_by"

DEFAULT_NAME = "TPLink Guard"
DEFAULT_HOST = "192.168.1.1"
DEFAULT_PORT = 80
DEFAULT_USERNAME = "admin"
DEFAULT_SCAN_INTERVAL = 60
DEFAULT_WHITELIST_FILENAME = "whitelist.txt"
DEFAULT_PHONE_FILENAME = "phone.txt"
DEFAULT_MONITORED_FILENAME = "monitored.txt"
# List files live in a subfolder of the integration directory so /config
# stays clean: /config/custom_components/tplink_guard/lists/
DEFAULT_LISTS_RELDIR = "custom_components/tplink_guard/lists"
DEFAULT_MATCH_BY = "both"
DEFAULT_TIMEOUT = 10

# Minutes to stop polling after another client (e.g. a browser logged into
# the router's web UI) takes over the single local admin session. The
# router allows one active web session per account, so polling and a
# human user keep kicking each other; the yield lets the human work.
SESSION_YIELD_MINUTES = 5

MATCH_MODES = ["mac", "ip", "both"]

# TP-Link SLP password encoding, extracted from web-static/js/su/su.js
ENCODE_KEY1 = "RDpbLfCPsJZ7fiv"
ENCODE_KEY2 = (
    "yLwVl0zKqws7LgKPRQ84Mdt708T1qQ3Ha7xv3H7NyU84p21BriUWBU43odz3iP4rBL3cD02KZci"
    "XTysVXiV8ngg6vL48rPJyAUw0HurW20xqxv9aYb4M9wK1Ae0wlro510qXeU07kV57fQMc8L6aLg"
    "MLwygtc0F10a0Dg70TOoouyFhdysuRMO51yY5ZlOZZLEal1h0t9YQW0Ko7oBwmCAHoic4HYbUy"
    "VeU3sfQ1xtXcPcf1aT303wAQhv66qzW"
)

ATTR_DEVICES = "devices"
ATTR_IP = "ip"
ATTR_MAC = "mac"
ATTR_HOSTNAME = "hostname"
ATTR_VENDOR = "vendor"
ATTR_STATE = "state"
ATTR_TYPE = "type"
ATTR_KNOWN = "known"
ATTR_LAST_UPDATE = "last_update"
ATTR_WHITELIST_COUNT = "whitelist_count"
ATTR_WATCHLIST = "watchlist"

SERVICE_RELOAD_WHITELIST = "reload_whitelist"
