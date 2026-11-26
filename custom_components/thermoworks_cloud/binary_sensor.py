"""Binary sensors for Thermoworks Cloud fan accessories and channel alarms."""

from abc import abstractmethod
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    ENTITY_ID_FORMAT,
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ThermoworksCoordinator
from .entity import ChannelDescription, ThermoworksChannelEntity, ThermoworksFanEntity, View
from .models import ThermoworksAlarm, ThermoworksChannel, ThermoworksFan, Unit


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
                entity = binary_sensor.build(hass, coordinator, source)
                if entity is not None:
                    new_entities.append(entity)

    async_add_entities(new_entities)


@dataclass(frozen=True)
class FanConnection:
    """Whether the fan is connected. None if it doesn't say, shown as unknown."""

    connected: bool | None


class FanConnectedSensor(ThermoworksFanEntity[FanConnection], BinarySensorEntity):
    """Whether the fan accessory is connected.

    Unlike the fan sensors, this stays available while the fan is disconnected, since
    showing that is its job.
    """

    entity_id_format = ENTITY_ID_FORMAT
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

    entity_id_format = ENTITY_ID_FORMAT

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
BINARY_SENSORS: list[type[FanConnectedSensor | AlarmBinarySensor]] = [
    FanConnectedSensor,
    HighAlarmBinarySensor,
    LowAlarmBinarySensor,
]
