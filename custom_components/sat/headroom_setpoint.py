"""Dynamic minimum setpoint, version 3: learned flow headroom.

Many boilers cannot modulate below the heat loss of the house. They then stop
the burner themselves as soon as the flow temperature exceeds the setpoint by
a few degrees. A burn only lasts long when the setpoint leaves enough room
above the flow temperature at ignition: the boiler heats the water by its own
temperature rise at minimum power right away, and only the rest of the room
is used to warm up the system.

This controller sets the minimum setpoint to the flow temperature before
ignition plus a headroom. It learns the headroom per burn: a burn that the
boiler cuts off early raises it, a long burn lowers it again. The learned
value is kept in a Store, so it survives a restart.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from time import monotonic
from typing import Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .boiler import BoilerState
from .helpers import clamp

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

DEFAULT_HEADROOM = 17.0
MINIMUM_HEADROOM = 8.0
MAXIMUM_HEADROOM = 30.0
HEADROOM_STEP = 1.0

# A burn shorter than this, ended by the boiler itself, means the headroom was too small.
SHORT_BURN_SECONDS = 300
# A burn at least this long means the headroom can be lowered for a lower flow temperature.
LONG_BURN_SECONDS = 900
# The boiler stopped by itself when the flow passed the setpoint by at least this margin.
BOILER_CUTOFF_MARGIN = 3.0


@dataclass(slots=True)
class _Burn:
    started: float
    setpoint: float
    flow_temperature_at_ignition: float
    maximum_flow_temperature: float

    @property
    def headroom(self) -> float:
        return self.setpoint - self.flow_temperature_at_ignition


class HeadroomMinimumSetpoint:
    """Minimum setpoint = flow temperature before ignition + learned headroom."""

    def __init__(self) -> None:
        self._headroom: float = DEFAULT_HEADROOM
        self._reference_flow_temperature: Optional[float] = None
        self._burn: Optional[_Burn] = None

        self._store: Optional[Store] = None

    @property
    def headroom(self) -> float:
        return self._headroom

    @property
    def current(self) -> Optional[float]:
        """The minimum setpoint for a PWM on-phase, or None before the first flow reading.

        The climate keeps it between the configured minimum and maximum setpoint.
        """
        if self._reference_flow_temperature is None:
            return None

        return self._reference_flow_temperature + self._headroom

    async def async_initialize(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = Store(hass, STORAGE_VERSION, f"sat.{entry_id}.headroom_setpoint")

        data = await self._store.async_load()
        if data and data.get("headroom") is not None:
            self._headroom = clamp(float(data["headroom"]), MINIMUM_HEADROOM, MAXIMUM_HEADROOM)
            _LOGGER.debug("Loaded flow headroom %.1f°C from storage.", self._headroom)

    def update(self, boiler: BoilerState, now: Optional[float] = None) -> None:
        """Track the flow temperature and learn from every finished space-heating burn."""
        if boiler.flow_temperature is None:
            return

        now = monotonic() if now is None else now

        if not boiler.flame_active:
            if self._burn is not None:
                self._finish_burn(now)

            # While the burner is off, the flow temperature approaches the return temperature,
            # which is what the boiler starts from at the next ignition.
            self._reference_flow_temperature = boiler.flow_temperature
            return

        if boiler.hot_water_active or boiler.setpoint is None:
            # Hot water burns say nothing about space heating.
            self._burn = None
            return

        if self._burn is None:
            ignition_flow_temperature = self._reference_flow_temperature if self._reference_flow_temperature is not None else boiler.flow_temperature
            self._burn = _Burn(
                started=now,
                setpoint=boiler.setpoint,
                flow_temperature_at_ignition=ignition_flow_temperature,
                maximum_flow_temperature=boiler.flow_temperature,
            )
            return

        self._burn.maximum_flow_temperature = max(self._burn.maximum_flow_temperature, boiler.flow_temperature)

    def _finish_burn(self, now: float) -> None:
        burn, self._burn = self._burn, None
        duration = now - burn.started
        cut_off_by_boiler = burn.maximum_flow_temperature >= burn.setpoint + BOILER_CUTOFF_MARGIN

        # Learn from the headroom this burn actually had, so burns at other setpoints
        # (warm-up, normal modulation) only move the value when they tell us something new.
        if cut_off_by_boiler and duration < SHORT_BURN_SECONDS:
            self._set_headroom(max(self._headroom, burn.headroom + HEADROOM_STEP), f"boiler stopped after {duration:.0f}s with {burn.headroom:.1f}°C headroom")
        elif duration >= LONG_BURN_SECONDS:
            self._set_headroom(min(self._headroom, burn.headroom - HEADROOM_STEP), f"burn lasted {duration:.0f}s with {burn.headroom:.1f}°C headroom")

    def _set_headroom(self, value: float, reason: str) -> None:
        value = clamp(value, MINIMUM_HEADROOM, MAXIMUM_HEADROOM)
        if value == self._headroom:
            return

        _LOGGER.info("Flow headroom %.1f°C -> %.1f°C (%s).", self._headroom, value, reason)
        self._headroom = value

        if self._store is not None:
            self._store.async_delay_save(lambda: {"headroom": self._headroom})
