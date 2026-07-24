# Innova OSMO — serial protocol notes

How the protocol was reverse-engineered, and everything observed on the bus.
Hardware: Innova OSMO fancoil, mainboard `ESE845II` / `INNOVA-M7-V0_3`, on-board
control `EWA844II`, stock WiFi module `INNOVA-WIFI-V0_3` (ESP32-based, plugs
into the mainboard via two pin-header connectors J3/J4).

## Method: passive sniffing

The WiFi module's back silkscreen labels the interface pins: `12V GND TX RX EN`,
an ESP32 programming group (`EN BOOT GND RX TX 3V3`), and two `GND B A` groups
(an RS485 bus, present on the connector but not investigated — the TTL link
turned out to be enough).

1. Multimeter first, ideally with the unit powered off between measurements.
   Note the bus is a **5 V domain**: with the module removed the mainboard TX
   — the pin the module silkscreen labels `RX`, 1st from the top — idles at
   ~5 V (with the module inserted the level sits lower). A 3.3 V
   adapter reads it fine for sniffing, but keep this in mind for anything you
   connect permanently — and stay clear of the EN pin, which sits next to the
   two 12 V pins on the left header (see the warning in the README).
2. Adapter **RX** probe on one line at a time, GND to GND, adapter TX left
   floating (never inject). Unit powered and stock module operating normally.
3. `tools/capture.py` logs every byte with ms timestamps while you drive the
   official app; typed markers correlate app actions with bus traffic.
4. `tools/analyze.py` segments frames (inter-byte gaps + sliding-window Modbus
   CRC16) and decodes them.

First 10 seconds of capture already settled it: `01 03 02 29 00 01 54 7a`
repeated — a textbook Modbus RTU read request, valid CRC.

## Link layer

- Modbus RTU over TTL UART (5 V domain), **9600 8N1**.
- Mainboard = slave, address **1**. WiFi module = master.
- Functions observed: `0x03` Read Holding Registers (single, plus one 2-register
  read), `0x06` Write Single Register.

## Register map (as observed)

| Register | Meaning | Access | Notes |
|----------|---------|--------|-------|
| 0 | Room temperature ×10 | RO | 259 = 25.9 °C, matches app display exactly |
| 1 | Water supply temperature ×10 | RO | Not from documentation — inferred from behavior: tracks a plausible chilled-water range (13-19°C observed) and drifts toward supply temperature when the fancoil's own valve closes (unit stopped) with reduced mixing. Same address as AirLeaf's water temperature register. |
| 9 | Unknown | RO | Same address as AirLeaf's relay/output register, but does **not** behave like a bitfield on the OSMO — observed incrementing/decrementing by 1 across consecutive ~10s polls, i.e. counter-like. Not used. If you figure out what it is, please open a PR. |
| 15 | Fan speed, raw reading | RO | Same address as AirLeaf's fan speed register. On the OSMO this is a live inverter reading, not a fixed set of levels: 0 when the fan is stopped, ~1100 with fan mode "auto" (throttled), ~1500-1504 with fan mode "max". **This is the cleanest "is the unit actually running" signal found so far** — see the `climate.action` section below. |
| 151 | Status/alarm bitfield | RO | `0x0200` (bit9) seen while the "water out of range" alarm was active. Other bits unmapped. |
| 305 | Setpoint ×10 | R/W | App writes e.g. 230 = 23.0 °C. Value **255** is a sentinel the cloud writes/leaves while the unit is OFF — ignore it when reading. |
| 553 | Program bitfield | R/W | bits 0-2: fan mode — 0 = auto, 1 = night, 2 = max. bit4 (`+16`): standby. Power OFF = write current value with bit4 set (e.g. 17 = night + standby); power ON = same with bit4 cleared. |
| 556 | Season | R/W | 0 = auto, 1 = heating, 2 = cooling |
| 60002 | Unknown, 32 bit (read ×2) | RO | Always 0 during captures |

## Finding the "actually running" feedback (register 15)

The component originally reported `climate.action` as always IDLE while the
unit was on, because no register in the initial map distinguished "enabled,
waiting" from "enabled, actively heating/cooling". Register 9 was the prime
suspect (it plays that role on the AirLeaf), but sniffing it live showed
values incrementing/decrementing by 1 across consecutive polls — a
counter/timer, not a status bitfield, and not safe to build logic on with the
data gathered so far.

Register 15 (fan speed on the AirLeaf) turned out to be the answer, found via
a simple A/B test: with the unit actively cooling, poll it repeatedly; then
push the setpoint far above room temperature to force the unit to stop, and
poll again.

```
cooling, fan "max"     -> reg15 ≈ 1497-1504
cooling, fan "auto"     -> reg15 ≈ 1098-1100  (throttled by the inverter)
stopped (setpoint above room temp) -> reg15 = 0, exactly, repeatedly
```

The fan is inverter-driven and power-modulated, so register 15 isn't a fixed
"speed level" — it's a close-to-real-time RPM-like reading that responds to
actual load. Zero means the fan is physically not spinning, which on this unit
only happens when it isn't heating/cooling. The component maps this (above a
small noise threshold) combined with the season register to
`CLIMATE_ACTION_HEATING`/`CLIMATE_ACTION_COOLING`, and to `CLIMATE_ACTION_IDLE`
otherwise. Verified on cooling only — heating should behave identically (same
physical fan feedback) but hasn't been tested (the heat pump was only
producing chilled water at the time).

## Master polling pattern (stock module)

- Unit OFF: reg 553 every 5 s (keepalive/status).
- Unit ON: regs 553, 305, 556 every ~500 ms; reg 0 every 20 s; reg 151 every
  30 s; reg 60002 (2 registers) every 15 s.

## Quirks worth knowing

- **Cloud loses commands.** During captures, one fan-mode change made in the app
  never produced any write on the bus. The app also only writes the final state
  when you tap through options quickly.
- The app's ON action right after cloud reconnection replayed a stale
  `305 = 255` write before restoring the real setpoint — treat 255 as "not a
  setpoint" everywhere.
- No register carrying actual fan speed / relay state has been found yet (the
  stock master never polled one). The `climate` action is therefore reported as
  off/idle only. Candidates to probe: AirLeaf used reg 15 for fan speed — the
  OSMO map is different, but a register scan from the ESP master could map more.

## Unexplored

- The RS485 `A/B` pairs on J3/J4 (idle ~1.7 V measured). Possibly the same
  Modbus slave on a different physical layer — untested.
- Register 151 bits other than bit9; registers beyond the ones the stock
  module polls.
