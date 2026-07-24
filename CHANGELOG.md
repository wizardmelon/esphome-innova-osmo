# Changelog

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
