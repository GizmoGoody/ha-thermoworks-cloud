"""Constants for the Thermoworks Cloud integration."""

DOMAIN = "thermoworks_cloud"
# Each poll of the ThermoWorks API is one request. Every 30 seconds is 2,880 requests a day, within
# the free plan's limit of 3,000 a day, which resets at midnight UTC.
THERMOWORKS_DEFAULT_SCAN_INTERVAL_SECONDS = 30
# Each poll of ETI Cloud takes several requests and its limits are unknown, so it keeps 30 minutes.
ETI_DEFAULT_SCAN_INTERVAL_SECONDS = 1800
# The ThermoWorks API caches responses for 15 seconds, so polling faster cannot return new data.
# This also matches its free plan's limit of 4 requests per minute.
MIN_SCAN_INTERVAL_SECONDS = 15
# Also arbitrary: an upper bound (24 hours) so the options flow and the scan
# interval number entity agree on a maximum.
MAX_SCAN_INTERVAL_SECONDS = 86400

CONF_CLOUD_PROVIDER = "cloud_provider"
CONF_API_KEY = "api_key"

PROVIDER_THERMOWORKS = "thermoworks"
PROVIDER_ETI = "eti"

CLOUD_PROVIDERS = {
    PROVIDER_THERMOWORKS: {
        "name": "ThermoWorks Cloud",
        "base_url": "https://cloud.thermoworks.com/api",
        "api_key": None,  # Use library defaults
        "app_id": None,
        "referer": None,
        "default_scan_interval": THERMOWORKS_DEFAULT_SCAN_INTERVAL_SECONDS,
    },
    PROVIDER_ETI: {
        "name": "ETI Cloud",
        "api_key": "AIzaSyBD4snlT2LllO4k0NywX5qYjJ7M7WfU4_I",
        "app_id": "1:701566661301:web:7a9bc711c05985ead144fc",
        "referer": "https://cloud.etiltd.com/",
        "default_scan_interval": ETI_DEFAULT_SCAN_INTERVAL_SECONDS,
    },
}
