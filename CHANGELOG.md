# Changelog

## v0.3.1 — 2026-07-28

**Expose tenth-of-a-degree setpoint resolution.** Register 305 is already
written as `target * 10` (i.e. natively in tenths of a degree) — nothing in
the write path rounds it to halves. The only thing forcing coarser steps was
ESPHome's climate `visual` block being left unset. `example-fancoil.yaml` now
sets it explicitly:

```yaml
visual:
  min_temperature: 16 °C
  max_temperature: 30 °C
  temperature_step:
    target_temperature: 0.1
    current_temperature: 0.1
```

`min_temperature`/`max_temperature` are set to match the 16–30 °C clamp
already used by the setpoint-compensation path, since without a reference
sensor configured `control()` writes the raw setpoint unclamped.

Whether the OSMO mainboard actually *honors* tenth-of-a-degree setpoints, or
silently snaps them to the nearest 0.5° internally, is unverified — every
setpoint tested so far has ended in `.0` or `.5`. Worth checking against the
debug log (`ESP_LOGD` dump of the setpoint register) after setting something
like 21.3 °C.

## v0.3.0 — 2026-07-28

**Added: optional external reference sensor for compensated setpoint control.**
The fancoil's on-board sensor sits ~10 cm off the floor and doesn't represent
the actual room temperature well. You can now point the climate at an
existing Home Assistant thermometer entity (any integration) instead:

- `current_temperature` is taken from the external sensor, falling back to
  the on-board one if it goes stale.
- The setpoint written to register 305 is dynamically compensated — using
  the on-board sensor as reference — so the fancoil keeps running until the
  *real* room, not its own sensor, reaches the HA-set target. A configurable
  deadband (default 0.3 °C) avoids the correction flip-flopping direction
  from sensor noise near convergence, with a 0.5 °C minimum margin to keep
  decent airflow close to target.
- A diagnostic `problem` binary sensor flags when the external sensor hasn't
  reported within a configurable timeout (default 60 min); while stale, the
  component writes the target directly with no compensation.
- The user-facing target temperature is persisted to flash, since register
  305 may now hold the compensated value instead of it.

Entirely optional — without configuring `reference_temperature_sensor`,
behavior is unchanged. See the commented-out block in `example-fancoil.yaml`
and the "External reference sensor" section in README.md.

## v0.2.0 — 2026-07-17

**Fix: `climate.action` was always reported as idle**, even while the unit was
actively heating/cooling. Root cause: no register in the original map carried
a running/idle feedback bit, so it was hardcoded.

Found the fix by sniffing register 15 (fan speed on the AirLeaf) live through
an A/B test — force the unit to stop by pushing the setpoint above room
temperature, then let it run again, repeatedly. On the OSMO this register
turned out to be a real-time inverter fan reading: exactly 0 when the fan is
stopped, and a load-dependent value (~1100 auto, ~1500 max) while running.
`climate.action` now derives from it. Verified on cooling; heating should
behave identically but is unconfirmed (see docs/protocol.md).

Also investigated register 9 (the AirLeaf's relay/output register) as a
candidate — ruled out: it behaves like a counter/timer on the OSMO, not a
status bitfield.

Added:
- `water_temperature` sensor (register 1, water supply temperature — inferred
  from behavior, not documented; same address as AirLeaf's water temp register).
- `fan_speed_percent` sensor (register 15, scaled against the empirically
  observed max reading).

## v0.1.0 — 2026-07-16

Initial release: Modbus RTU protocol reverse-engineered from the OSMO
mainboard's TTL UART, `innova_osmo` ESPHome component (climate entity, room
temperature, water-out-of-range alarm, raw status), wiring guide, and the
capture/analyze sniffing toolkit.
