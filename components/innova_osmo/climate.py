import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import climate, modbus, sensor, binary_sensor

from esphome.const import (
    DEVICE_CLASS_TEMPERATURE,
    DEVICE_CLASS_PROBLEM,
    STATE_CLASS_MEASUREMENT,
    UNIT_CELSIUS,
    UNIT_PERCENT,
    ICON_FAN,
    ENTITY_CATEGORY_DIAGNOSTIC,
)

AUTO_LOAD = ["modbus", "sensor", "binary_sensor"]

innova_osmo_ns = cg.esphome_ns.namespace("innova_osmo")
InnovaOsmo = innova_osmo_ns.class_(
    "InnovaOsmo", climate.Climate, cg.PollingComponent, modbus.ModbusDevice
)

CONF_AIR_TEMPERATURE = "air_temperature"
CONF_WATER_TEMPERATURE = "water_temperature"
CONF_FAN_SPEED_PERCENT = "fan_speed_percent"
CONF_WATER_ALARM = "water_alarm"
CONF_STATUS_RAW = "status_raw"

CONFIG_SCHEMA = (
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
        }
    )
    .extend(cv.polling_component_schema("30s"))
    .extend(modbus.modbus_device_schema(0x01))
)


async def to_code(config):
    var = await climate.new_climate(config)
    await cg.register_component(var, config)
    await modbus.register_modbus_device(var, config)

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
