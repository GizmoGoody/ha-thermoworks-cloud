"""Tests for the request budget entities of ThermoWorks API key entries."""

from datetime import datetime, timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.thermoworks_cloud.const import (
    CONF_API_KEY,
    CONF_CLOUD_PROVIDER,
    DOMAIN,
    PROVIDER_THERMOWORKS,
)
from tests.api_test_object import TEST_ACCOUNT_ID, TEST_API_KEY, ApiTestObject

NOON = datetime(2026, 11, 26, 12, 0, 0, tzinfo=dt_util.UTC)
NEXT_MIDNIGHT = datetime(2026, 11, 27, 0, 0, 0, tzinfo=dt_util.UTC)

REQUESTS_TODAY = "sensor.thermoworks_cloud_requests_today"
REQUESTS_REMAINING = "sensor.thermoworks_cloud_requests_remaining"
RATE_LIMIT_PER_DAY = "sensor.thermoworks_cloud_rate_limit_per_day"
REQUESTS_RESET = "sensor.thermoworks_cloud_requests_reset"
REQUESTS_EXHAUSTED = "sensor.thermoworks_cloud_requests_exhausted"
SUSTAINABLE_INTERVAL = "sensor.thermoworks_cloud_sustainable_interval"
RATE_LIMITED = "binary_sensor.thermoworks_cloud_rate_limited"
TEMPERATURE_ENTITY = "sensor.12_34_56_78_90_ab_ch_1_temperature"


def _api_key_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ThermoWorks Cloud",
        unique_id=f"{PROVIDER_THERMOWORKS}:{TEST_ACCOUNT_ID}",
        data={CONF_API_KEY: TEST_API_KEY, CONF_CLOUD_PROVIDER: PROVIDER_THERMOWORKS},
    )
    entry.add_to_hass(hass)
    return entry


async def _set_up(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: int = 31) -> None:
    """Move time on far enough for the integration to poll again."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


def _state(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state.state


async def test_budget_after_setup(
    hass: HomeAssistant, api_test_object: ApiTestObject, freezer: FrozenDateTimeFactory
) -> None:
    """Test that setup counts its two requests and shows the key's limits."""

    # Setup
    freezer.move_to(NOON)

    # Act
    await _set_up(hass, _api_key_entry(hass))

    # Assert: one request for the limits, one poll
    assert _state(hass, REQUESTS_TODAY) == "2"
    assert _state(hass, REQUESTS_REMAINING) == "2998"
    assert _state(hass, RATE_LIMIT_PER_DAY) == "3000"
    assert _state(hass, REQUESTS_RESET) == NEXT_MIDNIGHT.isoformat()
    # 2,998 polls at 30 seconds last past midnight
    assert _state(hass, REQUESTS_EXHAUSTED) == NEXT_MIDNIGHT.isoformat()
    # 12 hours over 2,998 requests is 14.4 seconds, below the 15 second minimum
    assert _state(hass, SUSTAINABLE_INTERVAL) == "15"
    assert _state(hass, RATE_LIMITED) == "off"


async def test_budget_entities_are_diagnostic(
    hass: HomeAssistant, api_test_object: ApiTestObject
) -> None:
    """Test that the budget entities are diagnostic and the detailed ones start disabled."""

    # Act
    await _set_up(hass, _api_key_entry(hass))

    # Assert
    registry = er.async_get(hass)
    disabled = {
        "sensor.thermoworks_cloud_rate_limit_per_minute",
        "sensor.thermoworks_cloud_recommended_poll_interval",
        "sensor.thermoworks_cloud_api_tier",
        "sensor.thermoworks_cloud_last_request",
    }
    for entity_id in {
        REQUESTS_TODAY, REQUESTS_REMAINING, RATE_LIMIT_PER_DAY, REQUESTS_RESET,
        REQUESTS_EXHAUSTED, SUSTAINABLE_INTERVAL, RATE_LIMITED, *disabled,
    }:
        entry = registry.async_get(entity_id)
        assert entry is not None, entity_id
        assert entry.entity_category is EntityCategory.DIAGNOSTIC
        assert (entry.disabled_by is not None) == (entity_id in disabled), entity_id


async def test_every_response_counts_including_not_modified(
    hass: HomeAssistant, api_test_object: ApiTestObject, freezer: FrozenDateTimeFactory
) -> None:
    """Test that a 304 answer counts, since ThermoWorks counts it toward the daily limit."""

    # Setup
    freezer.move_to(NOON)
    await _set_up(hass, _api_key_entry(hass))
    api_test_object.respond_to_devices(status=304)

    # Act
    await _poll(hass, freezer)
    await _poll(hass, freezer)

    # Assert
    assert _state(hass, REQUESTS_TODAY) == "4"
    assert _state(hass, REQUESTS_REMAINING) == "2996"


async def test_count_continues_after_reload_without_reading_limits_again(
    hass: HomeAssistant, api_test_object: ApiTestObject, freezer: FrozenDateTimeFactory
) -> None:
    """Test that a reload keeps the count and does not spend a request on the limits."""

    # Setup
    freezer.move_to(NOON)
    entry = _api_key_entry(hass)
    await _set_up(hass, entry)

    # Act
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    # Assert: only the first poll after the reload is added
    assert len(api_test_object.me_requests) == 1
    assert _state(hass, REQUESTS_TODAY) == "3"
    assert _state(hass, RATE_LIMIT_PER_DAY) == "3000"


async def test_limits_read_again_on_a_new_day(
    hass: HomeAssistant, api_test_object: ApiTestObject, freezer: FrozenDateTimeFactory
) -> None:
    """Test that the first setup of a new UTC day reads the limits again."""

    # Setup
    freezer.move_to(NOON)
    entry = _api_key_entry(hass)
    await _set_up(hass, entry)
    freezer.move_to(NEXT_MIDNIGHT + timedelta(hours=1))

    # Act
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    # Assert
    assert len(api_test_object.me_requests) == 2


async def test_count_resets_at_midnight_utc(
    hass: HomeAssistant, api_test_object: ApiTestObject, freezer: FrozenDateTimeFactory
) -> None:
    """Test that the count starts again at midnight UTC, without waiting for a poll."""

    # Setup
    freezer.move_to(NEXT_MIDNIGHT - timedelta(seconds=10))
    await _set_up(hass, _api_key_entry(hass))
    assert _state(hass, REQUESTS_TODAY) == "2"

    # Act
    freezer.move_to(NEXT_MIDNIGHT + timedelta(seconds=1))
    async_fire_time_changed(hass, NEXT_MIDNIGHT)
    await hass.async_block_till_done()

    # Assert
    assert _state(hass, REQUESTS_TODAY) == "0"
    assert _state(hass, REQUESTS_REMAINING) == "3000"
    assert _state(hass, REQUESTS_RESET) == (NEXT_MIDNIGHT + timedelta(days=1)).isoformat()


async def test_rate_limited_keeps_budget_available(
    hass: HomeAssistant, api_test_object: ApiTestObject, freezer: FrozenDateTimeFactory
) -> None:
    """Test that a rate limited poll is shown, while the readings become unavailable."""

    # Setup
    freezer.move_to(NOON)
    await _set_up(hass, _api_key_entry(hass))
    api_test_object.respond_to_devices(status=429, headers={"Retry-After": "600"})

    # Act
    await _poll(hass, freezer)

    # Assert
    assert _state(hass, TEMPERATURE_ENTITY) == "unavailable"
    rate_limited = hass.states.get(RATE_LIMITED)
    assert rate_limited is not None
    assert rate_limited.state == "on"
    assert rate_limited.attributes["retry_after"] == 600
    assert _state(hass, REQUESTS_TODAY) == "3"


@pytest.mark.parametrize(
    ("requests_used", "expected_interval"),
    [
        # 12 hours left. 2,880 remaining is 15 seconds each
        (120, "15"),
        # 1,440 remaining is 30 seconds each
        (1560, "30"),
        # None remaining: wait for the reset, 12 hours away
        (3000, "43200"),
    ],
)
async def test_sustainable_interval(
    hass: HomeAssistant,
    api_test_object: ApiTestObject,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict,
    requests_used: int,
    expected_interval: str,
) -> None:
    """Test the shortest interval that lasts until the reset, from a saved count."""

    # Setup: the count saved earlier today, less the two requests setup makes
    freezer.move_to(NOON)
    entry = _api_key_entry(hass)
    hass_storage[f"{DOMAIN}.{entry.entry_id}.requests"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}.requests",
        "data": {
            "day": NOON.date().isoformat(),
            "requests_today": requests_used - 1,
            "last_request": None,
            "limits": {
                "per_day": 3000,
                "per_minute": 4,
                "recommended_poll_seconds": 60,
                "api_tier": "free",
            },
            "limits_day": NOON.date().isoformat(),
        },
    }

    # Act: the limits are current, so setup makes only the poll
    await _set_up(hass, entry)

    # Assert
    assert _state(hass, REQUESTS_TODAY) == str(requests_used)
    assert _state(hass, SUSTAINABLE_INTERVAL) == expected_interval
