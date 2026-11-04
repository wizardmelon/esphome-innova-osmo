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

// --- parametri runtime (entita' HA opzionali, altrimenti default di configurazione) ---
static float number_or(number::Number *n, float fallback) {
  return (n != nullptr && n->has_state() && !std::isnan(n->state)) ? n->state : fallback;
}
bool InnovaOsmo::use_reference_() const {
  return this->reference_temperature_sensor_ != nullptr &&
         (this->use_reference_switch_ == nullptr || this->use_reference_switch_->state);
}
bool InnovaOsmo::proportional_() const {
  return this->proportional_switch_ != nullptr ? this->proportional_switch_->state : this->compensation_proportional_;
}
bool InnovaOsmo::boost_enabled_() const {
  return this->boost_switch_ != nullptr ? this->boost_switch_->state : this->boost_on_ > 0.0f;
}
float InnovaOsmo::boost_on_value_() const { return number_or(this->boost_on_number_, this->boost_on_); }
float InnovaOsmo::boost_off_value_() const {
  float on = this->boost_on_value_();
  float off = this->boost_hysteresis_number_ != nullptr ? on - number_or(this->boost_hysteresis_number_, 0.5f)
                                                         : this->boost_off_;
  return off < 0.0f ? 0.0f : off;
}
float InnovaOsmo::deadband_value_() const { return number_or(this->deadband_number_, this->reference_temperature_deadband_); }
float InnovaOsmo::offset_filter_value_() const { return number_or(this->offset_filter_number_, this->offset_filter_); }
uint32_t InnovaOsmo::timeout_ms_value_() const {
  if (this->timeout_number_ != nullptr && this->timeout_number_->has_state() && !std::isnan(this->timeout_number_->state))
    return uint32_t(this->timeout_number_->state * 60.0f * 1000.0f);
  return this->reference_temperature_timeout_ms_;
}

void InnovaOsmo::setup() {
  if (this->reference_temperature_sensor_ == nullptr)
    return;

  // X (target reale) deve sopravvivere al reboot: una volta attiva la
  // compensazione, il registro 305 puo' contenere W (compensato) invece di X,
  // quindi non possiamo piu' ricavare X rileggendolo all'avvio.
  // Di default NAN: se non c'e' ancora una preference salvata (primo avvio in
  // assoluto) update_setpoint_compensation_() deve poter riconoscerlo ed
  // astenersi dallo scrivere finche' l'utente non imposta un target da HA.
  this->target_temperature = NAN;
  // Chiave fissa: una sola climate per dispositivo. In precedenza la chiave includeva l'hash dell'object id,
  // che cambia se si aggiunge/toglie "id:" o si rinomina l'entita': il target salvato andava perso al flash
  // e al suo posto si rileggeva W (setpoint compensato) dal registro 305. La vecchia chiave resta come migrazione.
  const uint32_t base_key = fnv1_hash("innova_osmo_target_temp");
  this->target_temp_pref_ = global_preferences->make_preference<float>(base_key);
  float loaded_target;
  if (this->target_temp_pref_.load(&loaded_target)) {
    this->target_temperature = loaded_target;
    this->target_temperature_known_ = true;
  } else {
    auto legacy_pref = global_preferences->make_preference<float>(base_key ^ this->get_object_id_hash());
    if (legacy_pref.load(&loaded_target)) {
      this->target_temperature = loaded_target;
      this->target_temperature_known_ = true;
      this->target_temp_pref_.save(&loaded_target);
      ESP_LOGI(TAG, "Target %.1f recuperato dalla chiave preferenze precedente", loaded_target);
    }
  }

  this->reference_temperature_sensor_->add_on_state_callback([this](float state) {
    if (std::isnan(state))
      return;
    this->reference_temperature_ = state;
    this->reference_temperature_last_update_ = millis();
    this->has_reference_data_ = true;
    this->fresh_reference_sample_ = true;
  });
  if (this->reference_temperature_sensor_->has_state() && !std::isnan(this->reference_temperature_sensor_->state)) {
    this->reference_temperature_ = this->reference_temperature_sensor_->state;
    this->reference_temperature_last_update_ = millis();
    this->has_reference_data_ = true;
  }
}

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

  // Dump temporaneo per confronto incrociato registri vs stato reale del fancoil.
  // Rimuovere una volta identificato il bit di feedback attivo/idle.
  ESP_LOGD(TAG, "poll state=%d reg=%u raw=%u (0x%04X)", this->state_,
           POLL_REGISTERS[this->state_ - 1], value, value);

  switch (this->state_) {
    case 1:  // REG_AIR_TEMP
      this->internal_air_temperature_ = f_value;
      this->y_filtered_ = std::isnan(this->y_filtered_) ? f_value : this->y_filtered_ + 0.3f * (f_value - this->y_filtered_);
      if (this->air_temperature_sensor_ != nullptr)
        this->air_temperature_sensor_->publish_state(f_value);

      if (this->reference_temperature_sensor_ != nullptr) {
        this->update_reference_temperature_status_();
        this->current_temperature = this->reference_temperature_valid_ ? this->reference_temperature_ : f_value;
        this->update_setpoint_compensation_();
        this->update_boost_();
      } else {
        this->current_temperature = f_value;
      }
      break;
    case 2:  // REG_SETPOINT
      // Con compensazione attiva il registro puo' contenere W (calcolato da noi)
      // invece di X: in quel caso il target reale vive solo in target_temperature
      // (persistito su flash in setup()) e non va risincronizzato da qui.
      //
      // Eccezione: primissimo avvio con la compensazione appena abilitata, senza
      // ancora nessuna preference salvata. In quel momento 305 contiene ancora
      // l'ultimo X reale (nessuna scrittura compensata e' mai partita), quindi
      // e' l'unica occasione per recuperarlo invece di restare con target NAN
      // (altrimenti climate non avrebbe un target impostabile finche' l'utente
      // non chiama target_temperature da HA "alla cieca").
      if (this->reference_temperature_sensor_ == nullptr || !this->target_temperature_known_) {
        // A unita' OFF il cloud puo' lasciare la sentinella 255: non e' un setpoint.
        if (value != SETPOINT_OFF_SENTINEL) {
          this->target_temperature = f_value;
          if (this->reference_temperature_sensor_ != nullptr) {
            this->target_temperature_known_ = true;
            this->target_temp_pref_.save(&this->target_temperature);
          }
        }
      }
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
    // 553 e' un registro read-modify-write con due campi indipendenti (fan sui
    // bit 0-2, standby sul bit 4): ogni scrittura deve partire dal valore
    // corrente. program_ viene rinfrescato dal poll (case 3), che pero' arriva
    // centinaia di ms dopo: senza riallineare la copia in RAM qui, una seconda
    // scrittura ravvicinata (o il blocco fan_mode piu' sotto, nella stessa
    // chiamata) ripartirebbe dal valore vecchio e cancellerebbe questa modifica.
    uint16_t prg = this->program_;
    switch (mode) {
      case climate::CLIMATE_MODE_OFF:
        // OFF osservato dall'app: scrive program con bit4 alzato (es. 17)
        prg |= PROGRAM_STANDBY_MASK;
        add_to_queue(CMD_WRITE_REG, prg, REG_PROGRAM);
        this->program_ = prg;
        break;
      case climate::CLIMATE_MODE_HEAT:
        add_to_queue(CMD_WRITE_REG, 1, REG_SEASON);
        prg &= ~PROGRAM_STANDBY_MASK;
        add_to_queue(CMD_WRITE_REG, prg, REG_PROGRAM);
        this->program_ = prg;
        break;
      case climate::CLIMATE_MODE_COOL:
        add_to_queue(CMD_WRITE_REG, 2, REG_SEASON);
        prg &= ~PROGRAM_STANDBY_MASK;
        add_to_queue(CMD_WRITE_REG, prg, REG_PROGRAM);
        this->program_ = prg;
        break;
      case climate::CLIMATE_MODE_HEAT_COOL:
        add_to_queue(CMD_WRITE_REG, 0, REG_SEASON);
        prg &= ~PROGRAM_STANDBY_MASK;
        add_to_queue(CMD_WRITE_REG, prg, REG_PROGRAM);
        this->program_ = prg;
        break;
      default:
        // Nessuna scrittura in coda: program_ NON va toccato.
        ESP_LOGW(TAG, "Modo non supportato: %d", mode);
        break;
    }
  }

  if (call.get_fan_mode().has_value()) {
    // Cambio manuale della ventola: il boost non la tocca piu' finche' l'errore non rientra sotto boost_off.
    if (this->boost_enabled_()) {
      this->boost_active_ = false;
      this->boost_suppressed_ = true;
    }
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
    this->program_ = prg;  // idem: tiene allineata la copia in RAM
  }

  if (call.get_target_temperature().has_value()) {
    float target = *call.get_target_temperature();
    this->target_temperature = target;
    if (this->reference_temperature_sensor_ != nullptr) {
      this->target_temperature_known_ = true;
      this->target_temp_pref_.save(&target);
    }
    // Scrittura immediata per reattivita': se la compensazione e' attiva verra'
    // raffinata al giro di polling successivo (update_setpoint_compensation_).
    add_to_queue(CMD_WRITE_REG, uint16_t(target * 10.0f + 0.5f), REG_SETPOINT);
  }

  this->state_ = 1;  // riallinea subito lo stato dopo i comandi
}

void InnovaOsmo::update_reference_temperature_status_() {
  bool use = this->use_reference_();
  bool fresh = use && this->has_reference_data_ &&
               (millis() - this->reference_temperature_last_update_ <= this->timeout_ms_value_());
  this->reference_temperature_valid_ = fresh;
  // Termometro escluso a mano (interruttore spento) non e' un problema.
  if (this->reference_temperature_problem_sensor_ != nullptr)
    this->reference_temperature_problem_sensor_->publish_state(use && !fresh);
}

// Ricalcola il setpoint da scrivere sul registro 305 (W) per compensare la
// posizione del sensore di bordo, usando il sensore esterno come riferimento
// reale della stanza. X (target utente) resta invariato e viene esposto in HA;
// W e' un dettaglio implementativo scritto sull'hardware, mai mostrato.
//
//   g = Z - X (con segno)
//   se |g| <= a (deadband): nessuna correzione, W = Y
//   altrimenti: margine = max(0.25*|g|, 0.5), W = Y - segno(g) * (|g| + margine)
//
// Senza dato fresco dal sensore esterno si torna a scrivere X direttamente
// (comportamento identico a quando la funzione non e' configurata).
// Modalita' proporzionale: la scheda confronta il setpoint scritto (W) con la propria temperatura Y.
// Scrivendo W = X + offset, con offset = Y - Z misurato quando arriva un nuovo valore del termometro
// esterno Z (filtrato), l'errore che la scheda vede e' W - Y = X - (Y - offset) = X - Z_stimato:
// l'errore VERO della stanza. Tra due valori di Z, Y insegue la stanza e la scheda ricalcola da sola,
// quindi non serve riscrivere W: cambia solo quando cambia l'offset. La ventola automatica e
// l'isteresi della scheda lavorano come in un fancoil normale, con il sensore giusto.
float InnovaOsmo::compute_proportional_setpoint_(float x) {
  float y = this->internal_air_temperature_;
  if (std::isnan(y))
    return x;
  if (this->fresh_reference_sample_) {
    this->fresh_reference_sample_ = false;
    float y_f = std::isnan(this->y_filtered_) ? y : this->y_filtered_;
    float measured = y_f - this->reference_temperature_;
    if (!this->offset_valid_) {
      this->offset_ = measured;
      this->offset_valid_ = true;
    } else {
      this->offset_ += this->offset_filter_value_() * (measured - this->offset_);
    }
    ESP_LOGD(TAG, "offset Y-Z: misurato %.2f, filtrato %.2f (Y_f %.2f, Z %.2f)", measured, this->offset_, y_f,
             this->reference_temperature_);
  }
  if (!this->offset_valid_)
    return x;
  float off = this->offset_;
  if (off > 5.0f) off = 5.0f;
  if (off < -5.0f) off = -5.0f;
  return x + off;
}

void InnovaOsmo::update_setpoint_compensation_() {
  if (std::isnan(this->target_temperature))
    return;  // target non ancora noto (primo avvio, prima di un set point da HA)

  float x = this->target_temperature;
  float w;

  if (!this->reference_temperature_valid_) {
    w = x;
  } else if (this->proportional_()) {
    w = this->compute_proportional_setpoint_(x);
  } else {
    float y = this->internal_air_temperature_;
    float g = this->reference_temperature_ - x;
    float abs_g = fabsf(g);
    if (abs_g <= this->deadband_value_()) {
      w = y;
    } else {
      float sign = (g > 0) ? 1.0f : -1.0f;
      float margin = 0.25f * abs_g;
      if (margin < 0.5f)
        margin = 0.5f;
      w = y - sign * (abs_g + margin);
    }
  }

  // Limite di sicurezza sul range fisico del fancoil.
  if (w < 16.0f) w = 16.0f;
  if (w > 30.0f) w = 30.0f;

  uint16_t raw = uint16_t(w * 10.0f + 0.5f);
  if (raw == SETPOINT_OFF_SENTINEL)
    raw += 1;  // evita la sentinella "unita' OFF" (25.5 -> 25.6)

  if (raw != this->last_written_setpoint_raw_) {
    add_to_queue(CMD_WRITE_REG, raw, REG_SETPOINT);
    this->last_written_setpoint_raw_ = raw;
    if (this->written_setpoint_sensor_ != nullptr)
      this->written_setpoint_sensor_->publish_state(raw / 10.0f);
  }
  if (this->offset_sensor_ != nullptr && this->offset_valid_ && this->reference_temperature_valid_ &&
      this->proportional_())
    this->offset_sensor_->publish_state(this->offset_);
}

// Boost ventola: errore vero e = X - Z (riscaldamento) o Z - X (raffreddamento). Sopra boost_on la ventola
// va al massimo (programma bit0-2 = 2), sotto boost_off torna in automatico (0). Non interviene in standby,
// senza termometro esterno fresco, in modo auto (HEAT_COOL) o se l'utente ha scelto una ventola a mano.
void InnovaOsmo::update_boost_() {
  float x = this->target_temperature;
  float on = this->boost_on_value_();
  float off = this->boost_off_value_();
  bool ref_ok = this->reference_temperature_valid_ && !std::isnan(x) &&
                (this->mode == climate::CLIMATE_MODE_HEAT || this->mode == climate::CLIMATE_MODE_COOL);
  float err = 0.0f;
  if (ref_ok)
    err = (this->mode == climate::CLIMATE_MODE_HEAT) ? (x - this->reference_temperature_)
                                                    : (this->reference_temperature_ - x);
  if (this->true_error_sensor_ != nullptr && ref_ok)
    this->true_error_sensor_->publish_state(err);

  bool usable = ref_ok && this->boost_enabled_() && on > 0.0f && !(this->program_ & PROGRAM_STANDBY_MASK);

  if (!usable || err < off) {
    this->boost_suppressed_ = false;  // errore rientrato: l'utente riprende il controllo della ventola
    if (this->boost_active_) {
      this->boost_active_ = false;
      if ((this->program_ & PROGRAM_FAN_MASK) == 2) {
        uint16_t prg = this->program_ & ~PROGRAM_FAN_MASK;  // 0 = auto
        add_to_queue(CMD_WRITE_REG, prg, REG_PROGRAM);
        this->program_ = prg;
        this->fan_mode = climate::CLIMATE_FAN_AUTO;
        ESP_LOGI(TAG, "Boost off (errore %.2f)", err);
      }
    }
  } else if (err >= on && !this->boost_active_ && !this->boost_suppressed_ &&
             (this->program_ & PROGRAM_FAN_MASK) == 0) {
    uint16_t prg = (this->program_ & ~PROGRAM_FAN_MASK) | 2;  // max
    add_to_queue(CMD_WRITE_REG, prg, REG_PROGRAM);
    this->program_ = prg;
    this->fan_mode = climate::CLIMATE_FAN_HIGH;
    this->boost_active_ = true;
    ESP_LOGI(TAG, "Boost on (errore %.2f)", err);
  }
  if (this->boost_active_sensor_ != nullptr)
    this->boost_active_sensor_->publish_state(this->boost_active_);
}

void InnovaOsmo::dump_config() {
  LOG_CLIMATE("", "Innova OSMO Climate", this);
  ESP_LOGCONFIG(TAG, "  Modbus address: 0x%02X", this->address_);
}

}  // namespace innova_osmo
}  // namespace esphome
