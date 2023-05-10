from __future__ import annotations

import logging
import typing

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.components.climate import HVACAction
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_MODE, MODE_SERIAL, OPTIONS_DEFAULTS, CONF_NAME, DOMAIN, COORDINATOR, CLIMATE
from .entity import SatEntity
from .serial import binary_sensor as serial_binary_sensor

if typing.TYPE_CHECKING:
    from .climate import SatClimate

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_setup_entry(_hass: HomeAssistant, _config_entry: ConfigEntry, _async_add_entities: AddEntitiesCallback):
    """
    Add binary sensors for the serial protocol if the integration is set to use it.
    """
    climate = _hass.data[DOMAIN][_config_entry.entry_id][CLIMATE]
    coordinator = _hass.data[DOMAIN][_config_entry.entry_id][COORDINATOR]

    # Retrieve the defaults and override it with the user options
    options = OPTIONS_DEFAULTS.copy()
    options.update(_config_entry.data)

    # Check if integration is set to use the serial protocol
    if options.get(CONF_MODE) == MODE_SERIAL:
        # Call function to set up OpenTherm binary sensors
        await serial_binary_sensor.async_setup_entry(_hass, _config_entry, _async_add_entities)

    _async_add_entities([
        SatControlSetpointSynchroSensor(coordinator, climate, _config_entry),
        SatCentralHeatingSynchroSensor(coordinator, climate, _config_entry),
    ])


class SatControlSetpointSynchroSensor(SatEntity, BinarySensorEntity):

    def __init__(self, coordinator, climate: SatClimate, config_entry: ConfigEntry):
        super().__init__(coordinator, config_entry)
        self._climate = climate

    @property
    def name(self):
        """Return the friendly name of the sensor."""
        return "Control Setpoint Synchro"

    @property
    def device_class(self):
        """Return the device class."""
        return BinarySensorDeviceClass.PROBLEM

    @property
    def available(self):
        """Return availability of the sensor."""
        return self._climate is not None and self._coordinator.setpoint is not None

    @property
    def is_on(self):
        """Return the state of the sensor."""
        coordinator_setpoint = self._coordinator.setpoint
        climate_setpoint = float(self._climate.extra_state_attributes.get("setpoint") or coordinator_setpoint)

        return not (
                self._climate.state_attributes.get("hvac_action") != HVACAction.HEATING or
                round(climate_setpoint, 1) == round(coordinator_setpoint, 1)
        )

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-control-setpoint-synchro"


class SatCentralHeatingSynchroSensor(SatEntity, BinarySensorEntity):
    def __init__(self, coordinator, climate: SatClimate, config_entry: ConfigEntry) -> None:
        """Initialize the Central Heating Synchro sensor."""
        super().__init__(coordinator, config_entry)
        self._climate = climate

    @property
    def name(self) -> str:
        """Return the friendly name of the sensor."""
        return "Central Heating Synchro"

    @property
    def device_class(self) -> str:
        """Return the device class."""
        return BinarySensorDeviceClass.PROBLEM

    @property
    def available(self) -> bool:
        """Return availability of the sensor."""
        return self._climate is not None

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        device_active = self._coordinator.device_active
        climate_hvac_action = self._climate.state_attributes.get("hvac_action")

        return not (
                (climate_hvac_action == HVACAction.OFF and not device_active) or
                (climate_hvac_action == HVACAction.IDLE and not device_active) or
                (climate_hvac_action == HVACAction.HEATING and device_active)
        )

    @property
    def unique_id(self) -> str:
        """Return a unique ID to use for this entity."""
        return f"{self._config_entry.data.get(CONF_NAME).lower()}-central-heating-synchro"
