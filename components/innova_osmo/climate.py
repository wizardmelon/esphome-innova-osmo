import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import climate, modbus, sensor, binary_sensor, number, switch

from esphome.const import (
    DEVICE_CLASS_TEMPERATURE,
    DEVICE_CLASS_PROBLEM,
    STATE_CLASS_MEASUREMENT,
    UNIT_CELSIUS,
    UNIT_PERCENT,
    ICON_FAN,
    ENTITY_CATEGORY_DIAGNOSTIC,
)

AUTO_LOAD = ["modbus", "sensor", "binary_sensor", "number", "switch"]

innova_osmo_ns = cg.esphome_ns.namespace("innova_osmo")
InnovaOsmo = innova_osmo_ns.class_(
    "InnovaOsmo", climate.Climate, cg.PollingComponent, modbus.ModbusDevice
)

CONF_AIR_TEMPERATURE = "air_temperature"
CONF_WATER_TEMPERATURE = "water_temperature"
CONF_FAN_SPEED_PERCENT = "fan_speed_percent"
CONF_WATER_ALARM = "water_alarm"
CONF_STATUS_RAW = "status_raw"
CONF_REFERENCE_TEMPERATURE_SENSOR = "reference_temperature_sensor"
CONF_REFERENCE_TEMPERATURE_TIMEOUT = "reference_temperature_timeout"
CONF_REFERENCE_TEMPERATURE_DEADBAND = "reference_temperature_deadband"
CONF_REFERENCE_TEMPERATURE_PROBLEM = "reference_temperature_problem"
CONF_REFERENCE_COMPENSATION = "reference_compensation"
CONF_REFERENCE_OFFSET_FILTER = "reference_offset_filter"
CONF_BOOST_ON = "boost_on"
CONF_BOOST_OFF = "boost_off"
# Entita' opzionali che rendono modificabili da Home Assistant i parametri (senza riflashare).
CONF_USE_REFERENCE_SWITCH = "use_reference_switch"
CONF_PROPORTIONAL_SWITCH = "proportional_switch"
CONF_BOOST_SWITCH = "boost_switch"
CONF_BOOST_ON_NUMBER = "boost_on_number"
CONF_BOOST_HYSTERESIS_NUMBER = "boost_hysteresis_number"
CONF_DEADBAND_NUMBER = "deadband_number"
CONF_OFFSET_FILTER_NUMBER = "offset_filter_number"
CONF_TIMEOUT_NUMBER = "timeout_number"
CONF_OFFSET_SENSOR = "offset_sensor"
CONF_WRITTEN_SETPOINT_SENSOR = "written_setpoint_sensor"
CONF_TRUE_ERROR_SENSOR = "true_error_sensor"
CONF_BOOST_ACTIVE_SENSOR = "boost_active_sensor"


def _validate_reference_temperature(config):
    if CONF_REFERENCE_TEMPERATURE_PROBLEM in config and CONF_REFERENCE_TEMPERATURE_SENSOR not in config:
        raise cv.Invalid(
            f"'{CONF_REFERENCE_TEMPERATURE_PROBLEM}' richiede '{CONF_REFERENCE_TEMPERATURE_SENSOR}'"
        )
    return config


CONFIG_SCHEMA = cv.All(
    climate.climate_schema(InnovaOsmo)
    .extend(
        {
            cv.Optional(CONF_AIR_TEMPERATURE): sensor.sensor_schema(
                unit_of_measurement=UNIT_CELSIUS,
                accuracy_decimals=1,
                device_class=DEVICE_CLASS_TEMPERATURE,
                state_class=STATE_CLASS_MEASUREMENT,
            ),
            cv.Optional(CONF_WATER_TEMPERATURE): sensor.sensor_schema(
                unit_of_measurement=UNIT_CELSIUS,
                accuracy_decimals=1,
                device_class=DEVICE_CLASS_TEMPERATURE,
                state_class=STATE_CLASS_MEASUREMENT,
            ),
            cv.Optional(CONF_FAN_SPEED_PERCENT): sensor.sensor_schema(
                unit_of_measurement=UNIT_PERCENT,
                accuracy_decimals=0,
                state_class=STATE_CLASS_MEASUREMENT,
                icon=ICON_FAN,
            ),
            cv.Optional(CONF_WATER_ALARM): binary_sensor.binary_sensor_schema(
                device_class=DEVICE_CLASS_PROBLEM,
            ),
            cv.Optional(CONF_STATUS_RAW): sensor.sensor_schema(
                accuracy_decimals=0,
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            # Sensore esterno opzionale (es. importato da Home Assistant via
            # "platform: homeassistant") usato al posto del sensore di bordo per
            # la temperatura corrente e per compensare il setpoint scritto sulla
            # scheda madre. Si veda README.md per i dettagli.
            cv.Optional(CONF_REFERENCE_TEMPERATURE_SENSOR): cv.use_id(sensor.Sensor),
            cv.Optional(
                CONF_REFERENCE_TEMPERATURE_TIMEOUT, default="60min"
            ): cv.positive_time_period_minutes,
            cv.Optional(CONF_REFERENCE_TEMPERATURE_DEADBAND, default=0.3): cv.float_range(
                min=0.0, max=5.0
            ),
            # "deadband" (storica): banda +-deadband con salti; "proportional": W = X + offset
            # (T interna - T esterna, filtrato), errore vero alla scheda. Si veda innova_osmo.cpp.
            cv.Optional(CONF_REFERENCE_COMPENSATION, default="deadband"): cv.one_of(
                "deadband", "proportional", lower=True
            ),
            cv.Optional(CONF_REFERENCE_OFFSET_FILTER, default=0.25): cv.float_range(
                min=0.01, max=1.0
            ),
            # Boost ventola al massimo quando l'errore vero supera boost_on (0 = disabilitato);
            # torna in automatico sotto boost_off (default meta' di boost_on). Richiede il termometro esterno.
            cv.Optional(CONF_BOOST_ON, default=0.0): cv.float_range(min=0.0, max=5.0),
            cv.Optional(CONF_BOOST_OFF): cv.float_range(min=0.0, max=5.0),
            cv.Optional(CONF_USE_REFERENCE_SWITCH): cv.use_id(switch.Switch),
            cv.Optional(CONF_PROPORTIONAL_SWITCH): cv.use_id(switch.Switch),
            cv.Optional(CONF_BOOST_SWITCH): cv.use_id(switch.Switch),
            cv.Optional(CONF_BOOST_ON_NUMBER): cv.use_id(number.Number),
            cv.Optional(CONF_BOOST_HYSTERESIS_NUMBER): cv.use_id(number.Number),
            cv.Optional(CONF_DEADBAND_NUMBER): cv.use_id(number.Number),
            cv.Optional(CONF_OFFSET_FILTER_NUMBER): cv.use_id(number.Number),
            cv.Optional(CONF_TIMEOUT_NUMBER): cv.use_id(number.Number),
            cv.Optional(CONF_OFFSET_SENSOR): sensor.sensor_schema(
                unit_of_measurement=UNIT_CELSIUS,
                accuracy_decimals=2,
                state_class=STATE_CLASS_MEASUREMENT,
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Optional(CONF_WRITTEN_SETPOINT_SENSOR): sensor.sensor_schema(
                unit_of_measurement=UNIT_CELSIUS,
                accuracy_decimals=1,
                device_class=DEVICE_CLASS_TEMPERATURE,
                state_class=STATE_CLASS_MEASUREMENT,
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Optional(CONF_TRUE_ERROR_SENSOR): sensor.sensor_schema(
                unit_of_measurement=UNIT_CELSIUS,
                accuracy_decimals=2,
                state_class=STATE_CLASS_MEASUREMENT,
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Optional(CONF_BOOST_ACTIVE_SENSOR): binary_sensor.binary_sensor_schema(
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Optional(CONF_REFERENCE_TEMPERATURE_PROBLEM): binary_sensor.binary_sensor_schema(
                device_class=DEVICE_CLASS_PROBLEM,
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
        }
    )
    .extend(cv.polling_component_schema("30s"))
    .extend(modbus.modbus_device_schema(0x01)),
    _validate_reference_temperature,
)


async def to_code(config):
    var = await climate.new_climate(config)
    await cg.register_component(var, config)
    await modbus.register_modbus_client_device(var, config)

    if CONF_AIR_TEMPERATURE in config:
        sens = await sensor.new_sensor(config[CONF_AIR_TEMPERATURE])
        cg.add(var.set_air_temperature_sensor(sens))
    if CONF_WATER_TEMPERATURE in config:
        sens = await sensor.new_sensor(config[CONF_WATER_TEMPERATURE])
        cg.add(var.set_water_temperature_sensor(sens))
    if CONF_FAN_SPEED_PERCENT in config:
        sens = await sensor.new_sensor(config[CONF_FAN_SPEED_PERCENT])
        cg.add(var.set_fan_speed_percent_sensor(sens))
    if CONF_WATER_ALARM in config:
        sens = await binary_sensor.new_binary_sensor(config[CONF_WATER_ALARM])
        cg.add(var.set_water_alarm_sensor(sens))
    if CONF_STATUS_RAW in config:
        sens = await sensor.new_sensor(config[CONF_STATUS_RAW])
        cg.add(var.set_status_raw_sensor(sens))
    if CONF_REFERENCE_TEMPERATURE_SENSOR in config:
        sens = await cg.get_variable(config[CONF_REFERENCE_TEMPERATURE_SENSOR])
        cg.add(var.set_reference_temperature_sensor(sens))
        cg.add(
            var.set_reference_temperature_timeout(
                config[CONF_REFERENCE_TEMPERATURE_TIMEOUT].total_milliseconds
            )
        )
        cg.add(var.set_reference_temperature_deadband(config[CONF_REFERENCE_TEMPERATURE_DEADBAND]))
        cg.add(var.set_compensation_proportional(config[CONF_REFERENCE_COMPENSATION] == "proportional"))
        cg.add(var.set_reference_offset_filter(config[CONF_REFERENCE_OFFSET_FILTER]))
        cg.add(var.set_boost_on(config[CONF_BOOST_ON]))
        cg.add(var.set_boost_off(config.get(CONF_BOOST_OFF, config[CONF_BOOST_ON] / 2.0)))
    if CONF_REFERENCE_TEMPERATURE_PROBLEM in config:
        sens = await binary_sensor.new_binary_sensor(config[CONF_REFERENCE_TEMPERATURE_PROBLEM])
        cg.add(var.set_reference_temperature_problem_sensor(sens))

    for key, setter in (
        (CONF_USE_REFERENCE_SWITCH, var.set_use_reference_switch),
        (CONF_PROPORTIONAL_SWITCH, var.set_proportional_switch),
        (CONF_BOOST_SWITCH, var.set_boost_switch),
        (CONF_BOOST_ON_NUMBER, var.set_boost_on_number),
        (CONF_BOOST_HYSTERESIS_NUMBER, var.set_boost_hysteresis_number),
        (CONF_DEADBAND_NUMBER, var.set_deadband_number),
        (CONF_OFFSET_FILTER_NUMBER, var.set_offset_filter_number),
        (CONF_TIMEOUT_NUMBER, var.set_timeout_number),
    ):
        if key in config:
            cg.add(setter(await cg.get_variable(config[key])))
    for key, setter in (
        (CONF_OFFSET_SENSOR, var.set_offset_sensor),
        (CONF_WRITTEN_SETPOINT_SENSOR, var.set_written_setpoint_sensor),
        (CONF_TRUE_ERROR_SENSOR, var.set_true_error_sensor),
    ):
        if key in config:
            cg.add(setter(await sensor.new_sensor(config[key])))
    if CONF_BOOST_ACTIVE_SENSOR in config:
        cg.add(var.set_boost_active_sensor(await binary_sensor.new_binary_sensor(config[CONF_BOOST_ACTIVE_SENSOR])))
