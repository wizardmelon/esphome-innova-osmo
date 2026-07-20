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
| 151 | Status/alarm bitfield | RO | `0x0200` (bit9) seen while the "water out of range" alarm was active. Other bits unmapped. |
| 305 | Setpoint ×10 | R/W | App writes e.g. 230 = 23.0 °C. Value **255** is a sentinel the cloud writes/leaves while the unit is OFF — ignore it when reading. |
| 553 | Program bitfield | R/W | bits 0-2: fan mode — 0 = auto, 1 = night, 2 = max. bit4 (`+16`): standby. Power OFF = write current value with bit4 set (e.g. 17 = night + standby); power ON = same with bit4 cleared. |
| 556 | Season | R/W | 0 = auto, 1 = heating, 2 = cooling |
| 60002 | Unknown, 32 bit (read ×2) | RO | Always 0 during captures |

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
