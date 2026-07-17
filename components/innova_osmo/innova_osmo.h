#pragma once

// Componente ESPHome per fancoil Innova OSMO (scheda ESE845II / INNOVA-M7-V0_3).
// Protocollo reverse-engineered il 2026-07-16 sniffando il link TTL tra scheda
// madre e modulo INNOVA-WIFI-V0_3: Modbus RTU 9600 8N1, slave address 1,
// l'ESP fa da master (come il modulo originale).
//
// Mappa registri OSMO (diversa dall'AirLeaf dei repo pico1881!):
//   0   T aria x10 (RO)
//   151 status/allarmi (RO) - bit9 = acqua fuori range (osservato attivo)
//   305 setpoint x10 (R/W) - 255 = sentinella scritta dal cloud a unita' OFF
//   553 program (R/W) - bit0-2: fan 0=auto 1=night 2=max; bit4 = standby/OFF
//   556 season (R/W) - 0=auto 1=caldo 2=freddo
// Derivato dal componente innova_climate di @pico1881 (MIT).

#include "esphome/components/modbus/modbus.h"
#include "esphome/components/climate/climate.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/components/binary_sensor/binary_sensor.h"
#include "esphome/core/helpers.h"
#include <deque>

namespace esphome {
namespace innova_osmo {

static const uint8_t CMD_READ_REG = 0x03;
static const uint8_t CMD_WRITE_REG = 0x06;

static const uint16_t REG_AIR_TEMP = 0;     // x10
static const uint16_t REG_STATUS = 151;     // bitfield allarmi
static const uint16_t REG_SETPOINT = 305;   // x10
static const uint16_t REG_PROGRAM = 553;    // fan + standby
static const uint16_t REG_SEASON = 556;

static const uint16_t PROGRAM_FAN_MASK = 0x0007;   // 0=auto 1=night 2=max
static const uint16_t PROGRAM_STANDBY_MASK = 0x0010;
static const uint16_t STATUS_WATER_ALARM_MASK = 0x0200;  // bit9, da confermare altri bit
static const uint16_t SETPOINT_OFF_SENTINEL = 255;

struct WriteableData {
  uint8_t function_value;
  uint16_t register_value;
  uint16_t write_value;
};

class InnovaOsmo : public esphome::climate::Climate, public PollingComponent, public modbus::ModbusDevice {
 public:
  void set_air_temperature_sensor(sensor::Sensor *s) { air_temperature_sensor_ = s; }
  void set_status_raw_sensor(sensor::Sensor *s) { status_raw_sensor_ = s; }
  void set_water_alarm_sensor(binary_sensor::BinarySensor *s) { water_alarm_sensor_ = s; }

  void setup() override;
  void loop() override;
  void dump_config() override;
  void update() override;
  void on_modbus_data(const std::vector<uint8_t> &data) override;
  void add_to_queue(uint8_t function, uint16_t new_value, uint16_t address);

  climate::ClimateTraits traits() override {
    auto traits = climate::ClimateTraits();
    traits.add_feature_flags(climate::CLIMATE_SUPPORTS_ACTION | climate::CLIMATE_SUPPORTS_CURRENT_TEMPERATURE);
    traits.set_supported_modes({
        climate::CLIMATE_MODE_OFF,
        climate::CLIMATE_MODE_HEAT,
        climate::CLIMATE_MODE_COOL,
        climate::CLIMATE_MODE_HEAT_COOL,  // "auto" dell'app
    });
    traits.set_visual_min_temperature(16.0);
    traits.set_visual_max_temperature(30.0);
    traits.set_visual_target_temperature_step(0.5);
    traits.set_visual_current_temperature_step(0.1);
    traits.set_supported_fan_modes({
        climate::CLIMATE_FAN_AUTO,   // 0
        climate::CLIMATE_FAN_QUIET,  // 1 = night
        climate::CLIMATE_FAN_HIGH,   // 2 = max
    });
    return traits;
  }

 protected:
  int state_{0};
  bool waiting_{false};
  uint32_t last_send_{0};
  bool waiting_for_write_ack_{false};
  uint16_t program_{0};
  uint16_t season_{0};
  std::deque<WriteableData> writequeue_;
  void write_modbus_register(WriteableData write_data);

  void control(const climate::ClimateCall &call) override;

  sensor::Sensor *air_temperature_sensor_{nullptr};
  sensor::Sensor *status_raw_sensor_{nullptr};
  binary_sensor::BinarySensor *water_alarm_sensor_{nullptr};
};

}  // namespace innova_osmo
}  // namespace esphome
