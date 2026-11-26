"""Helpers shared by the ThermoWorks Cloud platforms and flows."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import CLOUD_PROVIDERS, CONF_CLOUD_PROVIDER, DOMAIN, PROVIDER_THERMOWORKS


def service_device_info(config_entry: ConfigEntry) -> DeviceInfo:
    """Return the service device that stands for the config entry itself.

    Settings and states of the whole config entry, rather than of a thermometer, are on it.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, config_entry.entry_id)},
        name=config_entry.title,
        entry_type=DeviceEntryType.SERVICE,
    )


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
