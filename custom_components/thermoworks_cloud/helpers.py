"""Helpers shared by the ThermoWorks Cloud platforms and flows."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL

from .const import CLOUD_PROVIDERS, CONF_CLOUD_PROVIDER, PROVIDER_THERMOWORKS


def get_default_scan_interval(config_entry: ConfigEntry) -> int:
    """Return the scan interval used when the config entry has none saved.

    It depends on the provider, because each one costs a different number of requests per poll.
    Entries saved before providers existed are ThermoWorks Cloud entries.
    """
    provider = config_entry.data.get(CONF_CLOUD_PROVIDER, PROVIDER_THERMOWORKS)
    return CLOUD_PROVIDERS[provider]["default_scan_interval"]


def get_scan_interval(config_entry: ConfigEntry) -> int:
    """Return the config entry's scan interval in seconds."""
    return config_entry.options.get(
        CONF_SCAN_INTERVAL, get_default_scan_interval(config_entry)
    )
