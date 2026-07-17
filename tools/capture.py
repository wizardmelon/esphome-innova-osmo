#!/usr/bin/env python3
"""
Cattura RAW passiva del traffico seriale (Fase 1 - Innova OSMO).

Logga ogni chunk di byte con timestamp in ms su un file JSONL, mostra a video
hex+ASCII con evidenziazione dei gap temporali (candidati confini di frame
Modbus: silenzio > 3.5 caratteri).

Mentre gira, puoi digitare un testo + INVIO per inserire un MARKER nel log
(es. "power on da app") — serve a correlare azioni sull'app con i byte visti.

Uso:
  .venv/bin/python capture.py --baud 9600 --parity N
  .venv/bin/python capture.py --port /dev/cu.usbserial-021L3Z7T --baud 19200 --parity E

Stop: Ctrl+C. Il file di log finisce in logs/.
"""

import argparse
import glob
import json
import select
import sys
import time
from datetime import datetime

import serial


def autodetect_port() -> str:
    candidates = []
    for pattern in ("/dev/cu.wchusbserial*", "/dev/cu.usbserial*", "/dev/cu.SLAB*"):
        candidates.extend(sorted(glob.glob(pattern)))
    if not candidates:
        sys.exit("Nessuna porta seriale USB trovata. Specificane una con --port.")
    if len(candidates) > 1:
        print(f"ATTENZIONE: più porte trovate, uso {candidates[0]}. Alternative: {candidates[1:]}")
    return candidates[0]


def main() -> None:
    ap = argparse.ArgumentParser(description="Cattura passiva seriale con timestamp")
    ap.add_argument("--port", default=None, help="Porta seriale (default: autodetect CH340/USB)")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--parity", choices=["N", "E", "O"], default="N")
    ap.add_argument("--stopbits", type=int, choices=[1, 2], default=1)
    ap.add_argument("--out", default=None, help="File di log JSONL (default: logs/capture_<ts>_<cfg>.jsonl)")
    ap.add_argument("--markfile", default=None,
                    help="File di testo pollato ogni 200 ms: ogni riga nuova diventa un marker "
                         "(per inserire marker quando lo script gira in background)")
    ap.add_argument("--duration", type=float, default=None, help="Stop automatico dopo N secondi")
    args = ap.parse_args()

    port = args.port or autodetect_port()
    cfg = f"{args.baud}-8{args.parity}{args.stopbits}"
    out_path = args.out or f"logs/capture_{datetime.now():%Y%m%d_%H%M%S}_{cfg}.jsonl"

    # Silenzio inter-frame Modbus: 3.5 caratteri (1 char = 11 bit con start/parità/stop).
    # Sotto i 19200 baud lo standard usa il tempo reale; il jitter USB (~1-2 ms) può
    # allungare i gap apparenti, quindi la soglia qui è solo indicativa a video:
    # la segmentazione vera la fa analyze.py (gap + CRC).
    char_time_ms = 11.0 / args.baud * 1000.0
    gap_ms = max(3.5 * char_time_ms, 5.0)

    ser = serial.Serial(
        port=port,
        baudrate=args.baud,
        bytesize=serial.EIGHTBITS,
        parity={"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD}[args.parity],
        stopbits=serial.STOPBITS_ONE if args.stopbits == 1 else serial.STOPBITS_TWO,
        timeout=0,
    )

    print(f"Porta:  {port}")
    print(f"Config: {cfg}   soglia gap a video: {gap_ms:.1f} ms")
    print(f"Log:    {out_path}")
    print("Digita un testo + INVIO per inserire un marker. Ctrl+C per terminare.\n")

    t0 = time.monotonic()
    last_byte_t = None
    total = 0
    line_bytes = 0
    mark_pos = 0
    last_mark_poll = 0.0

    def now_ms() -> float:
        return (time.monotonic() - t0) * 1000.0

    with open(out_path, "w") as log:
        log.write(json.dumps({"meta": {"port": port, "baud": args.baud,
                                       "parity": args.parity, "stopbits": args.stopbits,
                                       "started": datetime.now().isoformat()}}) + "\n")
        try:
            while True:
                if args.duration is not None and now_ms() / 1000.0 > args.duration:
                    break

                inputs = [ser.fileno()]
                if sys.stdin.isatty():
                    inputs.append(sys.stdin)
                rlist, _, _ = select.select(inputs, [], [], 0.05)

                if sys.stdin in rlist:
                    text = sys.stdin.readline().strip()
                    t = now_ms()
                    log.write(json.dumps({"t": round(t, 3), "mark": text}) + "\n")
                    log.flush()
                    print(f"\n=== MARKER @ {t:.0f} ms: {text} ===")
                    line_bytes = 0

                if args.markfile and time.monotonic() - last_mark_poll > 0.2:
                    last_mark_poll = time.monotonic()
                    try:
                        with open(args.markfile) as mf:
                            mf.seek(mark_pos)
                            for text in mf.read().splitlines():
                                if text.strip():
                                    t = now_ms()
                                    log.write(json.dumps({"t": round(t, 3), "mark": text.strip()}) + "\n")
                                    log.flush()
                                    print(f"\n=== MARKER @ {t:.0f} ms: {text.strip()} ===")
                                    line_bytes = 0
                            mark_pos = mf.tell()
                    except FileNotFoundError:
                        pass

                if ser.fileno() in rlist or ser.in_waiting:
                    data = ser.read(ser.in_waiting or 1)
                    if data:
                        t = now_ms()
                        gap = None if last_byte_t is None else t - last_byte_t
                        last_byte_t = t
                        total += len(data)
                        log.write(json.dumps({"t": round(t, 3), "data": data.hex(),
                                              "gap": None if gap is None else round(gap, 3)}) + "\n")
                        log.flush()
                        if gap is not None and gap > gap_ms:
                            print(f"\n--- gap {gap:.1f} ms ---")
                            line_bytes = 0
                        hexs = " ".join(f"{b:02x}" for b in data)
                        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
                        print(f"{hexs}  |{asc}|", end=" ", flush=True)
                        line_bytes += len(data)
                        if line_bytes >= 16:
                            print()
                            line_bytes = 0
        except KeyboardInterrupt:
            pass

    dur = now_ms() / 1000.0
    print(f"\n\nCattura terminata: {total} byte in {dur:.1f} s -> {out_path}")
    if total == 0:
        print("Nessun byte ricevuto: linea muta, baud sbagliato, o stai sondando il pad sbagliato.")


if __name__ == "__main__":
    main()
