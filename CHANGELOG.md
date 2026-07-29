# Changelog

## v0.3.4 — 2026-07-29

**Fix: a mode change and a fan change can silently cancel each other.**
Register 553 packs two independent fields — fan speed (bits 0-2) and standby
(bit 4) — so every write to it is a read-modify-write built on `program_`,
the cached copy of the register. `control()` never refreshed that cache after
queueing a write; only the poll cycle did (`on_modbus_data()`, state 3). Any
second write issued before that poll landed rebuilt the word from the stale
cache and overwrote the first change.

Two ways to hit it:

- **Same `climate.control` call carrying both mode and fan_mode** — no timing
  involved, fails every time. Turning the unit off while setting the fan to
  max queues `PROGRAM=16` then `PROGRAM=2`; last write wins, the unit stays
  on. Reachable from the native API, a script, or a Node-RED flow setting
  both attributes at once.
- **Two calls in quick succession.** The exposure window is *not* the 10 s
  `update_interval`: `control()` ends with `state_ = 1`, restarting the poll
  burst, and `loop()` drains the write queue before reading registers 1-7
  with REG_PROGRAM at state 3 — so the cache re-syncs roughly 200-250 ms
  after the call. Short, but trivially hit by back-to-back automation
  commands.

Fix: `control()` now assigns `program_` right after queueing each REG_PROGRAM
write, in both the mode and fan_mode blocks. The unsupported-mode `default:`
branch deliberately leaves it untouched, since it queues no write. If a write
fails on the bus the cache is corrected by the next poll, exactly as before.

`season_` is intentionally left alone: REG_SEASON is written wholesale rather
than read-modify-write, so a stale copy cannot corrupt a later write, and it
is refreshed (state 4) before its only consumer reads it (state 7).

## v0.3.3 — 2026-07-28

**Fix: climate stuck with no adjustable target on first activation of the
external reference sensor.** With `reference_temperature_sensor` configured,
`target_temperature` starts as `NAN` until a flash preference exists (see
v0.3.0), and register 305 readback is intentionally ignored so it doesn't
get overwritten by a compensated value. On a brand-new activation neither
had happened yet: no preference, and the register read that could have
bootstrapped one was skipped — leaving `target_temperature` at `NAN`
indefinitely, with no target for the HA climate card to show or let you
drag (confirmed live on fancoil-sala: card showed current temperature only,
no way to set a target).

Fix: on first boot with the feature enabled and no preference yet, register
305 is read *once* — at that point it still holds the real last-set target,
since no compensated write has happened yet — to seed `target_temperature`,
which is then immediately persisted. After that one-time bootstrap, register
305 is ignored again as before.

No config changes needed; existing installs with a reference sensor already
configured for more than one boot cycle are unaffected (they already have a
preference).

## v0.3.2 — 2026-07-28

**Fix deprecation warning:** `modbus.register_modbus_device` is deprecated
upstream (ESPHome will remove it in 2026.12.0) in favor of
`register_modbus_client_device` — same behavior, just a rename. Switched
`climate.py` over. No config changes needed on existing fancoils.

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
