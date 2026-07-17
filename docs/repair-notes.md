# Repair notes — ESE845II mainboard

Notes from diagnosing a damaged OSMO mainboard. Hopefully you never need this
page; if your board suddenly plays dead, start here.

## The EN ↔ 12 V hazard

On the **left module header** the `EN` pin sits directly next to the two 12 V
pins (5th and 6th position). The clearance is small: a multimeter probe tip or
a stray wire can bridge them. When that happens, 12 V reaches a net that is not
designed for it and the **proprietary main controller chip dies instantly** —
this is the QFP mounted at 45° between the two module headers.

That controller has no public part number, no datasheet and no distributor: it
appears to be a custom/proprietary part. **The only known fix is transplanting
the chip from a donor board.**

Practical rules:
- Do not probe `EN` on a powered board. This project never needs `EN`.
- Do resistance/continuity measurements with the unit disconnected from mains.
- If you must probe near the 12 V pins, use probe tips with insulating sleeves.

## Failure signature and diagnosis walkthrough

Symptoms observed after an EN↔12 V contact:

- The PSU enters **hiccup mode**: the 12 V rail oscillates ~0–2 V, the 5 V rail
  ~0–1 V, the on-board control panel is dead. This is the power supply
  protecting itself from a downstream short — the PSU itself is usually fine.
- With everything unplugged from the module headers, the short persists:
  **5 V rail to GND ≈ 2.3 Ω** (measured in resistance mode, board unpowered).

Diagnosis steps that worked:

1. Disconnect every removable connector and re-measure the 5 V rail after each —
   this rules out the panel and peripherals.
2. Check the 5 V regulator (an ST **LD1117-50** in DPAK near the input section):
   VIN↔VOUT should NOT be a short (a few hundred ohms, asymmetric, is normal).
   If it is shorted through, replace it before anything else or it will feed
   12 V into the 5 V rail at the next power-up.
3. Find the shorted component by **thermal signature**: inject a current-limited
   low voltage into the 5 V rail (bench PSU at 2 V, current limit 0.5–1 A; a
   plain 1.5 V AA battery also works) and feel which component warms up.
4. In this failure the hot part was the **45°-mounted proprietary controller**
   → donor transplant territory (hot-air rework).

Other components on the 5 V rail worth knowing about: the **ST485B** (SO-8)
RS485 transceiver near the `- B A +` terminal serves only the external RS485
interface — if it is the shorted one, you can simply remove it and run without
RS485 (this project uses the TTL UART, not RS485).
