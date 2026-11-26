"""Sensors representing a Thermoworks thermometer.

Each sensor's `parse` method builds a view of everything it shows from a device, channel or fan,
and the base classes in `entity.py` handle identity, updates and availability the same way
for all of them.
"""
from abc import abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any

from homeassistant.components.sensor import (
    ENTITY_ID_FORMAT,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .budget import Limits, RequestBudget
from .const import DOMAIN
from .coordinator import ThermoworksCoordinator
from .helpers import service_device_info
from .entity import (
    ChannelDescription,
    ThermoworksChannelEntity,
    ThermoworksDeviceEntity,
    ThermoworksFanEntity,
    View,
)
from .models import (
    FanActivity,
    ThermoworksAlarm,
    ThermoworksChannel,
    ThermoworksDevice,
    ThermoworksFan,
    Unit,
)

_LOGGER: logging.Logger = logging.getLogger(__package__)

# Home Assistant's unit for each unit the integration handles
HA_UNITS: dict[Unit, str] = {
    Unit.FAHRENHEIT: UnitOfTemperature.FAHRENHEIT,
    Unit.CELSIUS: UnitOfTemperature.CELSIUS,
    Unit.HUMIDITY: PERCENTAGE,
}
TEMPERATURE_UNITS = (Unit.FAHRENHEIT, Unit.CELSIUS)


def _channel_attributes(channel: ThermoworksChannel) -> dict[str, Any]:
    """Return the channel metadata shown on its measurement sensors."""
    return {
        "channel_id": channel.id,
        "status": channel.status,
        "enabled": channel.enabled,
        "probe_failed": channel.probe_failed,
        "alarming": channel.alarming,
        "last_telemetry_saved": (
            channel.last_telemetry_saved.isoformat() if channel.last_telemetry_saved else None
        ),
        "last_seen": channel.last_seen.isoformat() if channel.last_seen else None,
    }


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add sensors for passed config_entry in HA."""

    coordinator: ThermoworksCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ].coordinator

    new_entities: list[SensorEntity] = []
    for device in coordinator.data.devices.values():
        for device_channel in device.channels.values():
            if device_channel.units is None:
                _LOGGER.warning(
                    "Device %s channel %s reports a unit this integration doesn't handle, so "
                    "it has no measurement sensor. The unit is in the debug log.",
                    device.display_name(),
                    device_channel.display_name(),
                )

        # SENSORS is at the end of the file, after the classes it lists
        for sensor in SENSORS:
            for source in sensor.sources(device):
                entity = sensor.build(hass, coordinator, source)
                if entity is None:
                    _LOGGER.debug("Not creating %s sensor for device %s",
                                  sensor.suffix, device.display_name())
                    continue
                new_entities.append(entity)

    if coordinator.budget is not None:
        new_entities.extend(
            RequestBudgetSensor(
                config_entry, coordinator.budget, coordinator.poll_interval, description
            )
            for description in BUDGET_SENSORS
        )

    if len(new_entities) > 0:
        _LOGGER.debug("New entities to create: %d", len(new_entities))
        async_add_entities(new_entities)
    else:
        _LOGGER.debug("No new entities created")


class DeviceSensor[R](ThermoworksDeviceEntity[R], SensorEntity):
    """Base class for sensors on the device itself."""

    entity_id_format = ENTITY_ID_FORMAT

    _attr_has_entity_name = True


@dataclass(frozen=True)
class BatteryState:
    """Battery level, and whether it's charging."""

    level: float
    charging: bool


class BatterySensor(DeviceSensor[BatteryState]):
    """Battery level of the device, with a charging icon while it charges."""

    suffix = "battery"

    # https://developers.home-assistant.io/docs/core/entity/sensor/#available-device-classes
    _attr_device_class = SensorDeviceClass.BATTERY

    # https://developers.home-assistant.io/docs/core/entity/sensor/#available-state-classes
    _attr_state_class = SensorStateClass.MEASUREMENT

    # Naming
    # https://developers.home-assistant.io/docs/core/entity#entity-naming
    # https://developers.home-assistant.io/docs/internationalization/core/#name-of-entities
    _attr_translation_key = "battery"

    # API data is in percent with no decimal place
    # https://developers.home-assistant.io/docs/core/entity/sensor#properties
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 0

    def _unique_id(self) -> str:
        # Created without the suffix
        return f"{DOMAIN}-{self._ha_device_identifier}"

    @classmethod
    def parse(cls, device: ThermoworksDevice) -> View[BatteryState, None] | None:
        if device.battery is None:
            return None
        return View(
            entity_state=BatteryState(
                level=float(device.battery), charging=device.battery_state == "charging"
            ),
            description=None,
        )

    def _apply(self, entity_state: BatteryState) -> None:
        self._attr_native_value = entity_state.level
        # Only handle charging, as HA doesn't natively support a charging icon
        self._attr_icon = "mdi:battery-charging-100" if entity_state.charging else None

    def _on_unavailable(self) -> None:
        # HA shows the icon while unavailable, and the battery may no longer be charging
        self._attr_icon = None


class SignalSensor(DeviceSensor[float]):
    """Signal strength of the device's connection, in dBm."""

    suffix = "signal"

    # https://developers.home-assistant.io/docs/core/entity/sensor/#available-device-classes
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH

    # https://developers.home-assistant.io/docs/core/entity/sensor/#available-state-classes
    _attr_state_class = SensorStateClass.MEASUREMENT

    _attr_translation_key = "signal"

    # API data is in negative decibels with no decimal place
    # https://developers.home-assistant.io/docs/core/entity/sensor#properties
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_suggested_display_precision = 0

    @classmethod
    def parse(cls, device: ThermoworksDevice) -> View[float, None] | None:
        signal = device.signal_strength
        if signal is None:
            return None
        return View(entity_state=float(signal), description=None)

    def _apply(self, entity_state: float) -> None:
        self._attr_native_value = entity_state


class LastSeenSensor(DeviceSensor[datetime]):
    """When the device last contacted ThermoWorks Cloud."""

    suffix = "last-seen"

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "last_seen"

    @classmethod
    def parse(cls, device: ThermoworksDevice) -> View[datetime, None] | None:
        # Already parsed into UTC by the device model
        last_seen = device.last_seen
        if last_seen is None:
            return None
        return View(entity_state=last_seen, description=None)

    def _apply(self, entity_state: datetime) -> None:
        self._attr_native_value = entity_state


class TransmitIntervalSensor(DeviceSensor[int]):
    """How often the device sends readings, in seconds."""

    suffix = "transmit-interval"

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_translation_key = "transmit_interval"

    @classmethod
    def parse(cls, device: ThermoworksDevice) -> View[int, None] | None:
        interval = device.transmit_interval_in_seconds
        if interval is None:
            return None
        return View(entity_state=interval, description=None)

    def _apply(self, entity_state: int) -> None:
        self._attr_native_value = entity_state


class FanSensor[R, D = None](ThermoworksFanEntity[R, D], SensorEntity):
    """Base class for Thermoworks fan accessory sensors.

    Created for every fan, but only available while it's connected, since a disconnected
    fan still reports its last state and set temperature, which aren't current.
    """

    entity_id_format = ENTITY_ID_FORMAT

    _attr_has_entity_name = True


@dataclass(frozen=True)
class FanStatus:
    """What a connected fan is doing. None if it doesn't say, shown as unknown."""

    activity: FanActivity | None


class FanStateSensor(FanSensor[FanStatus]):
    """What the fan is doing: paused, blowing or pulsing."""

    suffix = "state"

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [activity.value for activity in FanActivity]
    _attr_translation_key = "fan_state"

    @classmethod
    def parse(cls, fan: ThermoworksFan) -> View[FanStatus, None]:
        if fan.connected is not True:
            return View(entity_state=None, description=None)
        return View(entity_state=FanStatus(fan.activity), description=None)

    def _apply(self, entity_state: FanStatus) -> None:
        activity = entity_state.activity
        self._attr_native_value = activity.value if activity is not None else None


@dataclass(frozen=True)
class FanSetpoint:
    """A connected fan's set temperature, None if it doesn't say, and the channel it follows."""

    temperature: int | None
    fan_channel: str | None


@dataclass(frozen=True)
class FanSetTemperatureDescription:
    """The temperature unit the gateway displays, which the fan's set temperature is in."""

    unit: str


class FanSetTemperatureSensor(FanSensor[FanSetpoint, FanSetTemperatureDescription]):
    """The temperature the fan is set to hold."""

    suffix = "set-temperature"

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_suggested_display_precision = 0
    _attr_translation_key = "fan_set_temperature"

    @classmethod
    def parse(
        cls, fan: ThermoworksFan
    ) -> View[FanSetpoint, FanSetTemperatureDescription] | None:
        # Without a temperature unit the set temperature can't be shown correctly. An
        # existing entity becomes unavailable and keeps its last unit, rather than
        # switching to a plain number, which HA would treat as a unit change.
        unit = fan.display_units
        if unit is None or unit not in TEMPERATURE_UNITS:
            return None
        return View(
            entity_state=(
                FanSetpoint(fan.set_temp, fan.fan_channel) if fan.connected is True else None
            ),
            description=FanSetTemperatureDescription(unit=HA_UNITS[unit]),
        )

    def _describe(self, description: FanSetTemperatureDescription) -> None:
        self._attr_native_unit_of_measurement = description.unit

    def _apply(self, entity_state: FanSetpoint) -> None:
        self._attr_native_value = entity_state.temperature
        self._attr_extra_state_attributes = (
            {"channel": entity_state.fan_channel} if entity_state.fan_channel is not None else {}
        )


class ChannelSensor[R, D: ChannelDescription = ChannelDescription](
    ThermoworksChannelEntity[R, D], SensorEntity
):
    """Base class for thermoworks channel sensors."""

    entity_id_format = ENTITY_ID_FORMAT

    # https://developers.home-assistant.io/docs/core/entity/sensor/#available-state-classes
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True

    # API data is given at higher precision, but that isn't needed
    # https://developers.home-assistant.io/docs/core/entity/sensor#properties
    _attr_suggested_display_precision = 1

    def _describe(self, description: D) -> None:
        # Named after the channel, not the device
        self._attr_name = description.name.capitalize()
        self._attr_translation_placeholders = {"channel_name": description.name}


@dataclass(frozen=True)
class Measurement:
    """A channel's valid reading, and the channel metadata shown alongside it."""

    value: float
    attributes: dict[str, Any]


@dataclass(frozen=True)
class MeasurementDescription(ChannelDescription):
    """A channel's name, and the unit its measurement is in."""

    unit: str


class MeasurementSensor(ChannelSensor[Measurement, MeasurementDescription]):
    """Base class for sensors showing a channel's reading.

    Created from the channel's units, even before it has a reading, so a probe that
    isn't plugged in yet still gets its entity. It's available only with a valid reading.
    """

    # The reading's unit. The sensor only applies to channels in one of these.
    supported_units: tuple[Unit, ...]

    # Changes on device contact without a new measurement, which would bloat the recorder
    _unrecorded_attributes = frozenset({"last_seen"})

    def _unique_id(self) -> str:
        # Created without the suffix
        return f"{DOMAIN}-{self._ha_device_identifier}-{self._channel_number}"

    @classmethod
    def parse(
        cls, channel: ThermoworksChannel
    ) -> View[Measurement, MeasurementDescription] | None:
        if channel.units not in cls.supported_units:
            return None
        measurement = None
        if channel.reading is not None:
            measurement = Measurement(
                value=channel.reading,
                # Source metadata, kept separate from Home Assistant's state update time
                attributes=_channel_attributes(channel),
            )
        return View(
            entity_state=measurement,
            description=MeasurementDescription(
                name=channel.display_name(), unit=HA_UNITS[channel.units]
            ),
        )

    def _describe(self, description: MeasurementDescription) -> None:
        super()._describe(description)
        self._attr_native_unit_of_measurement = description.unit

    def _apply(self, entity_state: Measurement) -> None:
        self._attr_native_value = entity_state.value
        self._attr_extra_state_attributes = entity_state.attributes


class TemperatureSensor(MeasurementSensor):
    """Temperature reading from a channel's probe."""

    suffix = "temperature"
    supported_units = TEMPERATURE_UNITS

    # https://developers.home-assistant.io/docs/core/entity/sensor/#available-device-classes
    _attr_device_class = SensorDeviceClass.TEMPERATURE

    # Naming
    # https://developers.home-assistant.io/docs/core/entity#entity-naming
    # https://developers.home-assistant.io/docs/internationalization/core/#name-of-entities
    _attr_translation_key = "temperature"


class HumiditySensor(MeasurementSensor):
    """Humidity reading from a channel's sensor."""

    suffix = "humidity"
    supported_units = (Unit.HUMIDITY,)

    # https://developers.home-assistant.io/docs/core/entity/sensor/#available-device-classes
    _attr_device_class = SensorDeviceClass.HUMIDITY

    # Naming
    # https://developers.home-assistant.io/docs/core/entity#entity-naming
    # https://developers.home-assistant.io/docs/internationalization/core/#name-of-entities
    _attr_translation_key = "humidity"


@dataclass(frozen=True)
class AlarmThreshold:
    """An alarm's configured threshold, None if it isn't set, and whether it's on and going off."""

    value: float | None
    enabled: bool | None
    alarming: bool | None


@dataclass(frozen=True)
class AlarmThresholdDescription(ChannelDescription):
    """A channel's name, and the unit its alarm threshold is in."""

    unit: str


class AlarmThresholdSensor(ChannelSensor[AlarmThreshold, AlarmThresholdDescription]):
    """Base class for Thermoworks channel alarm threshold sensors.

    Created when the channel has the alarm. The threshold is a setting, not a measurement,
    so it doesn't depend on the reading.
    """

    _attr_suggested_display_precision = 0

    @classmethod
    @abstractmethod
    def _alarm(cls, channel: ThermoworksChannel) -> ThermoworksAlarm | None:
        """Return the alarm this sensor shows."""

    @classmethod
    def parse(
        cls, channel: ThermoworksChannel
    ) -> View[AlarmThreshold, AlarmThresholdDescription] | None:
        alarm = cls._alarm(channel)
        if alarm is None:
            return None
        # The ThermoWorks API reports the unit on the channel, not on each alarm. A threshold
        # is compared with its channel's reading, so it's in the same unit.
        units = alarm.units or channel.units
        if units is None:
            # Not a unit HA knows. Like the measurement sensors, there's no entity, and an
            # existing one becomes unavailable and keeps its last unit.
            return None
        return View(
            entity_state=AlarmThreshold(alarm.value, alarm.enabled, alarm.alarming),
            description=AlarmThresholdDescription(
                name=channel.display_name(), unit=HA_UNITS[units]
            ),
        )

    def _describe(self, description: AlarmThresholdDescription) -> None:
        super()._describe(description)
        self._attr_native_unit_of_measurement = description.unit
        self._attr_device_class = (
            SensorDeviceClass.HUMIDITY
            if description.unit == PERCENTAGE
            else SensorDeviceClass.TEMPERATURE
        )

    def _apply(self, entity_state: AlarmThreshold) -> None:
        self._attr_native_value = entity_state.value
        self._attr_extra_state_attributes = {
            "enabled": entity_state.enabled,
            "alarming": entity_state.alarming,
        }


class HighAlarmThresholdSensor(AlarmThresholdSensor):
    """The channel's high alarm threshold."""

    suffix = "high-alarm-threshold"

    _attr_translation_key = "high_alarm_threshold"

    @classmethod
    def _alarm(cls, channel: ThermoworksChannel) -> ThermoworksAlarm | None:
        return channel.alarm_high

    def _describe(self, description: AlarmThresholdDescription) -> None:
        super()._describe(description)
        self._attr_name = f"{description.name} High Alarm Threshold"


class LowAlarmThresholdSensor(AlarmThresholdSensor):
    """The channel's low alarm threshold."""

    suffix = "low-alarm-threshold"

    _attr_translation_key = "low_alarm_threshold"

    @classmethod
    def _alarm(cls, channel: ThermoworksChannel) -> ThermoworksAlarm | None:
        return channel.alarm_low

    def _describe(self, description: AlarmThresholdDescription) -> None:
        super()._describe(description)
        self._attr_name = f"{description.name} Low Alarm Threshold"


# Every sensor type. Each is created for the sources on a device that have it.
SENSORS: list[type[DeviceSensor[Any] | FanSensor[Any, Any] | ChannelSensor[Any, Any]]] = [
    BatterySensor,
    SignalSensor,
    LastSeenSensor,
    TransmitIntervalSensor,
    FanStateSensor,
    FanSetTemperatureSensor,
    HumiditySensor,
    TemperatureSensor,
    HighAlarmThresholdSensor,
    LowAlarmThresholdSensor,
]


@dataclass(frozen=True, kw_only=True)
class RequestBudgetSensorDescription(SensorEntityDescription):
    """Describes a sensor for the API key's request budget."""

    # Reads the value from the budget and the config entry's scan interval
    value_fn: Callable[[RequestBudget, int], StateType | datetime]


class RequestBudgetSensor(SensorEntity):
    """Part of the API key's request budget, on the config entry's service device.

    These read the budget rather than the latest poll, so they stay available while polling
    fails, including while the key is rate limited.
    """

    entity_description: RequestBudgetSensorDescription

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        config_entry: ConfigEntry,
        budget: RequestBudget,
        scan_interval: int,
        description: RequestBudgetSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        self.entity_description = description
        self._budget = budget
        self._scan_interval = scan_interval
        self._attr_unique_id = f"{config_entry.entry_id}_{description.key}"
        self._attr_device_info = service_device_info(config_entry)

    async def async_added_to_hass(self) -> None:
        """Update whenever the budget changes."""
        self.async_on_remove(self._budget.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> StateType | datetime:
        """Return the value from the budget."""
        return self.entity_description.value_fn(self._budget, self._scan_interval)


def _limit(
    read: Callable[[Limits], StateType],
) -> Callable[[RequestBudget, int], StateType]:
    """Return a value_fn reading one of the key's limits, or None while they are unknown."""
    return lambda budget, _: read(budget.limits) if budget.limits is not None else None


# The request budget sensors, created for ThermoWorks API keys only
BUDGET_SENSORS: tuple[RequestBudgetSensorDescription, ...] = (
    RequestBudgetSensorDescription(
        key="requests_today",
        translation_key="requests_today",
        # Starts again from zero at midnight UTC, which statistics treat as a reset
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda budget, _: budget.requests_today,
    ),
    RequestBudgetSensorDescription(
        key="requests_remaining",
        translation_key="requests_remaining",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda budget, _: budget.requests_remaining,
    ),
    RequestBudgetSensorDescription(
        key="rate_limit_per_day",
        translation_key="rate_limit_per_day",
        value_fn=_limit(lambda limits: limits.per_day),
    ),
    RequestBudgetSensorDescription(
        key="requests_reset",
        translation_key="requests_reset",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda budget, _: budget.reset,
    ),
    RequestBudgetSensorDescription(
        key="requests_exhausted",
        translation_key="requests_exhausted",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda budget, scan_interval: budget.exhausted(scan_interval),
    ),
    RequestBudgetSensorDescription(
        key="sustainable_interval",
        translation_key="sustainable_interval",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        # Whole seconds, like the scan interval
        suggested_display_precision=0,
        value_fn=lambda budget, _: budget.sustainable_interval,
    ),
    RequestBudgetSensorDescription(
        key="rate_limit_per_minute",
        translation_key="rate_limit_per_minute",
        entity_registry_enabled_default=False,
        value_fn=_limit(lambda limits: limits.per_minute),
    ),
    RequestBudgetSensorDescription(
        key="recommended_poll_interval",
        translation_key="recommended_poll_interval",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        # Whole seconds, like the scan interval
        suggested_display_precision=0,
        entity_registry_enabled_default=False,
        value_fn=_limit(lambda limits: limits.recommended_poll_seconds),
    ),
    RequestBudgetSensorDescription(
        key="api_tier",
        translation_key="api_tier",
        entity_registry_enabled_default=False,
        value_fn=_limit(lambda limits: limits.api_tier),
    ),
    RequestBudgetSensorDescription(
        key="last_request",
        translation_key="last_request",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_registry_enabled_default=False,
        value_fn=lambda budget, _: budget.last_request,
    ),
)
