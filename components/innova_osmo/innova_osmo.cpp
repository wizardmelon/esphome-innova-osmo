#include "innova_osmo.h"
#include "esphome/core/log.h"
#include <algorithm>

namespace esphome {
namespace innova_osmo {

static const char *const TAG = "innova_osmo";

// Ciclo di poll: 1=T aria, 2=setpoint, 3=program, 4=season, 5=status,
// 6=T acqua, 7=velocita' ventola (quest'ultima determina anche climate::action).
static const uint16_t POLL_REGISTERS[] = {REG_AIR_TEMP,  REG_SETPOINT, REG_PROGRAM,   REG_SEASON,
                                           REG_STATUS,    REG_WATER_TEMP, REG_FAN_SPEED};
static const int POLL_STATES = sizeof(POLL_REGISTERS) / sizeof(POLL_REGISTERS[0]);

void InnovaOsmo::setup() {}

void InnovaOsmo::on_modbus_data(const std::vector<uint8_t> &data) {
  this->waiting_ = false;

  // La risposta a una scrittura (func 0x06) e' l'echo di registro+valore: 4 byte.
  if (this->waiting_for_write_ack_) {
    this->waiting_for_write_ack_ = false;
    if (data.size() == 4) {
      ESP_LOGD(TAG, "Write ok");
    } else {
      ESP_LOGW(TAG, "Risposta scrittura inattesa (%d byte)", data.size());
    }
    return;
  }

  if (data.size() < 2) {
    ESP_LOGW(TAG, "Risposta troppo corta (%d byte) per stato %d", data.size(), this->state_);
    return;
  }

  uint16_t value = (uint16_t(data[0]) << 8) | uint16_t(data[1]);
  float f_value = value / 10.0f;

  switch (this->state_) {
    case 1:  // REG_AIR_TEMP
      this->current_temperature = f_value;
      if (this->air_temperature_sensor_ != nullptr)
        this->air_temperature_sensor_->publish_state(f_value);
      break;
    case 2:  // REG_SETPOINT
      // A unita' OFF il cloud puo' lasciare la sentinella 255: non e' un setpoint.
      if (value != SETPOINT_OFF_SENTINEL)
        this->target_temperature = f_value;
      break;
    case 3:  // REG_PROGRAM
      this->program_ = value;
      switch (value & PROGRAM_FAN_MASK) {
        case 1: this->fan_mode = climate::CLIMATE_FAN_QUIET; break;  // night
        case 2: this->fan_mode = climate::CLIMATE_FAN_HIGH; break;   // max
        default: this->fan_mode = climate::CLIMATE_FAN_AUTO; break;
      }
      break;
    case 4:  // REG_SEASON
      this->season_ = value;
      if (this->program_ & PROGRAM_STANDBY_MASK) {
        this->mode = climate::CLIMATE_MODE_OFF;
        this->action = climate::CLIMATE_ACTION_OFF;
      } else {
        switch (value) {
          case 1: this->mode = climate::CLIMATE_MODE_HEAT; break;
          case 2: this->mode = climate::CLIMATE_MODE_COOL; break;
          default: this->mode = climate::CLIMATE_MODE_HEAT_COOL; break;
        }
        // action determinata piu' avanti nel ciclo (case REG_FAN_SPEED), dal
        // feedback reale del motore ventola.
      }
      break;
    case 5:  // REG_STATUS
      if (this->water_alarm_sensor_ != nullptr)
        this->water_alarm_sensor_->publish_state((value & STATUS_WATER_ALARM_MASK) != 0);
      if (this->status_raw_sensor_ != nullptr)
        this->status_raw_sensor_->publish_state(value);
      break;
    case 6:  // REG_WATER_TEMP
      if (this->water_temperature_sensor_ != nullptr)
        this->water_temperature_sensor_->publish_state(f_value);
      break;
    case 7: {  // REG_FAN_SPEED
      float pct = std::min(100.0f, (value / FAN_SPEED_MAX_READING) * 100.0f);
      if (this->fan_speed_percent_sensor_ != nullptr)
        this->fan_speed_percent_sensor_->publish_state(pct);

      // climate::action: la ventola inverter gira solo quando l'unita' sta
      // davvero scaldando/raffreddando. Confermato empiricamente (vedi header).
      if (!(this->program_ & PROGRAM_STANDBY_MASK)) {
        if (value > FAN_SPEED_RUNNING_THRESHOLD) {
          switch (this->season_) {
            case 1: this->action = climate::CLIMATE_ACTION_HEATING; break;
            case 2: this->action = climate::CLIMATE_ACTION_COOLING; break;
            default: this->action = climate::CLIMATE_ACTION_IDLE; break;
          }
        } else {
          this->action = climate::CLIMATE_ACTION_IDLE;
        }
      }
      break;
    }
  }

  if (++this->state_ > POLL_STATES) {
    this->state_ = 0;
    this->publish_state();
  }
}

void InnovaOsmo::loop() {
  uint32_t now = millis();

  // La scheda risponde in ~50 ms; 2 s coprono anche i retry del modulo originale.
  if (this->waiting_ && (now - this->last_send_ > 2000)) {
    ESP_LOGW(TAG, "Timeout in attesa di risposta (stato %d)", this->state_);
    this->waiting_ = false;
  }

  if (this->waiting_ || (this->state_ == 0))
    return;

  if (!this->writequeue_.empty()) {
    write_modbus_register(this->writequeue_.front());
    this->writequeue_.pop_front();
  } else {
    send(CMD_READ_REG, POLL_REGISTERS[this->state_ - 1], 1);
  }

  this->last_send_ = now;
  this->waiting_ = true;
}

void InnovaOsmo::update() { this->state_ = 1; }

void InnovaOsmo::add_to_queue(uint8_t function, uint16_t new_value, uint16_t address) {
  this->writequeue_.emplace_back(WriteableData{function, address, new_value});
}

void InnovaOsmo::write_modbus_register(WriteableData write_data) {
  uint8_t payload[] = {uint8_t(write_data.write_value >> 8), uint8_t(write_data.write_value)};
  send(write_data.function_value, write_data.register_value, 1, sizeof(payload), payload);
  this->waiting_for_write_ack_ = true;
}

void InnovaOsmo::control(const climate::ClimateCall &call) {
  if (call.get_mode().has_value()) {
    climate::ClimateMode mode = *call.get_mode();
    this->mode = mode;
    uint16_t prg = this->program_;
    switch (mode) {
      case climate::CLIMATE_MODE_OFF:
        // OFF osservato dall'app: scrive program con bit4 alzato (es. 17)
        add_to_queue(CMD_WRITE_REG, prg | PROGRAM_STANDBY_MASK, REG_PROGRAM);
        break;
      case climate::CLIMATE_MODE_HEAT:
        add_to_queue(CMD_WRITE_REG, 1, REG_SEASON);
        add_to_queue(CMD_WRITE_REG, prg & ~PROGRAM_STANDBY_MASK, REG_PROGRAM);
        break;
      case climate::CLIMATE_MODE_COOL:
        add_to_queue(CMD_WRITE_REG, 2, REG_SEASON);
        add_to_queue(CMD_WRITE_REG, prg & ~PROGRAM_STANDBY_MASK, REG_PROGRAM);
        break;
      case climate::CLIMATE_MODE_HEAT_COOL:
        add_to_queue(CMD_WRITE_REG, 0, REG_SEASON);
        add_to_queue(CMD_WRITE_REG, prg & ~PROGRAM_STANDBY_MASK, REG_PROGRAM);
        break;
      default:
        ESP_LOGW(TAG, "Modo non supportato: %d", mode);
        break;
    }
  }

  if (call.get_fan_mode().has_value()) {
    climate::ClimateFanMode fan_mode = *call.get_fan_mode();
    this->fan_mode = fan_mode;
    uint16_t prg = this->program_ & ~PROGRAM_FAN_MASK;
    switch (fan_mode) {
      case climate::CLIMATE_FAN_QUIET: prg |= 1; break;  // night
      case climate::CLIMATE_FAN_HIGH: prg |= 2; break;   // max
      case climate::CLIMATE_FAN_AUTO:
      default: break;                                    // auto = 0
    }
    add_to_queue(CMD_WRITE_REG, prg, REG_PROGRAM);
  }

  if (call.get_target_temperature().has_value()) {
    float target = *call.get_target_temperature();
    this->target_temperature = target;
    add_to_queue(CMD_WRITE_REG, uint16_t(target * 10.0f + 0.5f), REG_SETPOINT);
  }

  this->state_ = 1;  // riallinea subito lo stato dopo i comandi
}

void InnovaOsmo::dump_config() {
  LOG_CLIMATE("", "Innova OSMO Climate", this);
  ESP_LOGCONFIG(TAG, "  Modbus address: 0x%02X", this->address_);
}

}  // namespace innova_osmo
}  // namespace esphome
