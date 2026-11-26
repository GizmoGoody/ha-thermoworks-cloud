"""Binary sensors for Thermoworks Cloud: battery charging, fan accessories, channel alarms and the rate limit."""

from abc import abstractmethod
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .budget import RequestBudget
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
    ThermoworksAlarm,
    ThermoworksChannel,
    ThermoworksDevice,
    ThermoworksFan,
    Unit,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add binary sensors for passed config_entry in HA."""
    coordinator: ThermoworksCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ].coordinator

    new_entities: list[BinarySensorEntity] = []
    for device in coordinator.data.devices.values():
        # BINARY_SENSORS is at the end of the file, after the classes it lists
        for binary_sensor in BINARY_SENSORS:
            for source in binary_sensor.sources(device):
                entity = binary_sensor.build(coordinator, source)
                if entity is not None:
                    new_entities.append(entity)

    if coordinator.budget is not None:
        new_entities.append(RateLimitedSensor(config_entry, coordinator.budget))

    async_add_entities(new_entities)


class RateLimitedSensor(BinarySensorEntity):
    """Whether ThermoWorks answered the latest request with "rate limited".

    On the config entry's service device. It reads the request budget rather than the latest
    poll, so it stays available while the key is rate limited.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "rate_limited"

    def __init__(self, config_entry: ConfigEntry, budget: RequestBudget) -> None:
        """Initialise the sensor."""
        self._budget = budget
        self._attr_unique_id = f"{config_entry.entry_id}_rate_limited"
        self._attr_device_info = service_device_info(config_entry)

    async def async_added_to_hass(self) -> None:
        """Update whenever the budget changes."""
        self.async_on_remove(self._budget.async_add_listener(self.async_write_ha_state))

    @property
    def is_on(self) -> bool:
        """Return whether the latest request was rate limited."""
        return self._budget.rate_limited

    @property
    def extra_state_attributes(self) -> dict[str, int | None]:
        """Return how long ThermoWorks asked to wait, in seconds."""
        return {"retry_after": self._budget.retry_after}


class ChargingSensor(ThermoworksDeviceEntity[bool], BinarySensorEntity):
    """Whether the device's battery is charging, from the API's `batteryState`.

    Created when the device reports a battery state. Named "Charging" by Home Assistant
    from the device class.
    """

    suffix = "charging"

    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING
    _attr_has_entity_name = True

    @classmethod
    def parse(cls, device: ThermoworksDevice) -> View[bool, None] | None:
        if device.battery_state is None:
            return None
        return View(entity_state=device.battery_state == "charging", description=None)

    def _apply(self, entity_state: bool) -> None:
        self._attr_is_on = entity_state


@dataclass(frozen=True)
class FanConnection:
    """Whether the fan is connected. None if it doesn't say, shown as unknown."""

    connected: bool | None


class FanConnectedSensor(ThermoworksFanEntity[FanConnection], BinarySensorEntity):
    """Whether the fan accessory is connected.

    Unlike the fan sensors, this stays available while the fan is disconnected, since
    showing that is its job.
    """

    suffix = "connected"

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_has_entity_name = True
    _attr_translation_key = "fan_connected"

    @classmethod
    def parse(cls, fan: ThermoworksFan) -> View[FanConnection, None]:
        return View(entity_state=FanConnection(fan.connected), description=None)

    def _apply(self, entity_state: FanConnection) -> None:
        self._attr_is_on = entity_state.connected


@dataclass(frozen=True)
class AlarmState:
    """Whether an alarm is going off, plus its configuration."""

    active: bool
    enabled: bool | None
    value: float | None
    units: Unit | None


@dataclass(frozen=True)
class AlarmDescription(ChannelDescription):
    """A channel's name, and whether the alarm is on."""

    # An alarm that is off when the entity is created starts disabled. The ThermoWorks app
    # cannot set the alarms of some channels, such as the sensors along an RFX MEAT probe.
    enabled_by_default: bool


class AlarmBinarySensor(
    ThermoworksChannelEntity[AlarmState, AlarmDescription], BinarySensorEntity
):
    """Base class for Thermoworks channel alarm binary sensors.

    Created when the channel has the alarm, but only available while the channel has a
    valid reading. Without one, the alarm's state is an old evaluation, not a current one.
    """


    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_has_entity_name = True

    @classmethod
    @abstractmethod
    def _alarm(cls, channel: ThermoworksChannel) -> ThermoworksAlarm | None:
        """Return the alarm this sensor shows."""

    @classmethod
    def parse(cls, channel: ThermoworksChannel) -> View[AlarmState, AlarmDescription] | None:
        alarm = cls._alarm(channel)
        if alarm is None:
            return None
        description = AlarmDescription(
            name=channel.entity_name, enabled_by_default=alarm.enabled is True
        )
        if channel.reading is None:
            return View(entity_state=None, description=description)
        return View(
            entity_state=AlarmState(
                active=alarm.enabled is True and alarm.alarming is True,
                enabled=alarm.enabled,
                value=alarm.value,
                units=alarm.units,
            ),
            description=description,
        )

    def _describe(self, description: AlarmDescription) -> None:
        # Named after the channel, through each sensor's translation key
        self._attr_translation_placeholders = {"channel_name": description.name}
        # Only read when the entity is first registered
        self._attr_entity_registry_enabled_default = description.enabled_by_default

    def _apply(self, entity_state: AlarmState) -> None:
        self._attr_is_on = entity_state.active
        self._attr_extra_state_attributes = {
            "enabled": entity_state.enabled,
            "value": entity_state.value,
            "units": entity_state.units,
        }


class HighAlarmBinarySensor(AlarmBinarySensor):
    """Whether the channel's high alarm is going off."""

    suffix = "high-alarm"

    _attr_translation_key = "high_alarm"

    @classmethod
    def _alarm(cls, channel: ThermoworksChannel) -> ThermoworksAlarm | None:
        return channel.alarm_high


class LowAlarmBinarySensor(AlarmBinarySensor):
    """Whether the channel's low alarm is going off."""

    suffix = "low-alarm"

    _attr_translation_key = "low_alarm"

    @classmethod
    def _alarm(cls, channel: ThermoworksChannel) -> ThermoworksAlarm | None:
        return channel.alarm_low


# Every binary sensor type. Each is created for the sources on a device that have it.
BINARY_SENSORS: list[type[ChargingSensor | FanConnectedSensor | AlarmBinarySensor]] = [
    ChargingSensor,
    FanConnectedSensor,
    HighAlarmBinarySensor,
    LowAlarmBinarySensor,
]
