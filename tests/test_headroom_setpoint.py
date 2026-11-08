"""Tests for the flow headroom minimum setpoint (dynamic minimum setpoint, version 3)."""

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.template import DOMAIN as TEMPLATE_DOMAIN
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sat.boiler import BoilerState
from custom_components.sat.climate import SatClimate
from custom_components.sat.const import *
from custom_components.sat.fake import SatFakeCoordinator
from custom_components.sat.headroom_setpoint import (
    DEFAULT_HEADROOM,
    HEADROOM_STEP,
    MAXIMUM_HEADROOM,
    HeadroomMinimumSetpoint,
)


def boiler_state(flow: float, flame: bool, setpoint: float | None = 50.0, hot_water: bool = False) -> BoilerState:
    return BoilerState(
        is_active=flame,
        is_inactive=not flame,
        status=BoilerStatus.INSUFFICIENT_DATA,
        flame_active=flame,
        hot_water_active=hot_water,
        setpoint=setpoint,
        flow_temperature=flow,
        return_temperature=None,
        relative_modulation_level=None,
    )


def burn(controller: HeadroomMinimumSetpoint, start: float, seconds: float, setpoint: float, ignition_flow: float, peak_flow: float, hot_water: bool = False) -> None:
    """Simulate burner off at the ignition flow, one burn, and burner off again."""
    controller.update(boiler_state(ignition_flow, flame=False), now=start - 1)
    controller.update(boiler_state(ignition_flow, flame=True, setpoint=setpoint, hot_water=hot_water), now=start)
    controller.update(boiler_state(peak_flow, flame=True, setpoint=setpoint, hot_water=hot_water), now=start + seconds - 1)
    controller.update(boiler_state(peak_flow, flame=False, setpoint=setpoint), now=start + seconds)


def test_no_value_before_first_flow_reading():
    controller = HeadroomMinimumSetpoint()

    assert controller.current is None
    assert controller.headroom == DEFAULT_HEADROOM


def test_current_is_flow_before_ignition_plus_headroom():
    controller = HeadroomMinimumSetpoint()
    controller.update(boiler_state(33.0, flame=False), now=0)

    assert controller.current == 33.0 + DEFAULT_HEADROOM


def test_current_is_frozen_while_burning():
    controller = HeadroomMinimumSetpoint()
    controller.update(boiler_state(33.0, flame=False), now=0)
    controller.update(boiler_state(45.0, flame=True), now=10)

    assert controller.current == 33.0 + DEFAULT_HEADROOM


def test_short_burn_cut_off_by_boiler_raises_headroom():
    controller = HeadroomMinimumSetpoint()

    # 50 °C setpoint from 33 °C: 17 °C headroom, boiler stops at 55 °C after one minute.
    burn(controller, start=100, seconds=60, setpoint=50.0, ignition_flow=33.0, peak_flow=55.0)

    assert controller.headroom == DEFAULT_HEADROOM + HEADROOM_STEP


def test_short_burn_not_cut_off_by_boiler_keeps_headroom():
    controller = HeadroomMinimumSetpoint()

    # Flow stayed below the setpoint: the burn was ended by the controller, not by the boiler.
    burn(controller, start=100, seconds=60, setpoint=50.0, ignition_flow=33.0, peak_flow=48.0)

    assert controller.headroom == DEFAULT_HEADROOM


def test_long_burn_lowers_headroom():
    controller = HeadroomMinimumSetpoint()

    burn(controller, start=100, seconds=1200, setpoint=50.0, ignition_flow=33.0, peak_flow=55.0)

    assert controller.headroom == DEFAULT_HEADROOM - HEADROOM_STEP


def test_burns_at_other_setpoints_do_not_move_headroom():
    controller = HeadroomMinimumSetpoint()

    # Short burn at a low setpoint (9 °C headroom): known to be too small already.
    burn(controller, start=100, seconds=60, setpoint=42.0, ignition_flow=33.0, peak_flow=47.0)
    # Long warm-up burn at the maximum setpoint (23 °C headroom): known to be enough already.
    burn(controller, start=1000, seconds=2400, setpoint=56.0, ignition_flow=33.0, peak_flow=61.0)

    assert controller.headroom == DEFAULT_HEADROOM


def test_hot_water_burn_is_ignored():
    controller = HeadroomMinimumSetpoint()

    burn(controller, start=100, seconds=60, setpoint=50.0, ignition_flow=33.0, peak_flow=55.0, hot_water=True)

    assert controller.headroom == DEFAULT_HEADROOM


def test_headroom_is_capped():
    controller = HeadroomMinimumSetpoint()

    for index in range(40):
        setpoint = 33.0 + controller.headroom
        burn(controller, start=100 + index * 1000, seconds=60, setpoint=setpoint, ignition_flow=33.0, peak_flow=setpoint + 5)

    assert controller.headroom == MAXIMUM_HEADROOM


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
            {
                CONF_MODE: MODE_FAKE,
                CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
                CONF_OVERSHOOT_PROTECTION: True,
                CONF_MINIMUM_SETPOINT: 38,
                CONF_MAXIMUM_SETPOINT: 75,
            },
            {
                CONF_HEATING_CURVE_COEFFICIENT: 1.3,
                CONF_DYNAMIC_MINIMUM_SETPOINT: True,
                CONF_DYNAMIC_MINIMUM_SETPOINT_VERSION: 3,
            },
            {
                TEMPLATE_DOMAIN: [
                    {
                        SENSOR_DOMAIN: [
                            {
                                "name": "test_inside_sensor",
                                "state": "{{ 20.0 | float }}",
                            },
                            {
                                "name": "test_outside_sensor",
                                "state": "{{ 10.0 | float }}",
                            }
                        ]
                    },
                ],
            },
    )],
])
async def test_climate_uses_flow_headroom(hass: HomeAssistant, entry: MockConfigEntry, climate: SatClimate, coordinator: SatFakeCoordinator) -> None:
    await coordinator.async_set_boiler_temperature(33)
    await climate.async_set_target_temperature(20.5)
    await climate.async_set_hvac_mode(HVACMode.HEAT)

    climate.schedule_control_heating_loop(force=True)
    await hass.async_block_till_done()

    # The burner is off at 33 °C, so the minimum setpoint is 33 + 17 = 50 °C.
    assert climate.minimum_setpoint_value == 50.0

    # The requested setpoint is far below 50 °C, so the heating runs in PWM blocks.
    assert climate.requested_setpoint < 50.0
    assert climate.pulse_width_modulation_enabled
