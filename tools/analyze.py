#!/usr/bin/env python3
"""
Analisi del log di cattura (Fase 1 - Innova OSMO).

Dato un log JSONL prodotto da capture.py:
1. ricostruisce lo stream di byte con timestamp,
2. cerca frame Modbus RTU validi con scansione a finestra scorrevole
   (struttura plausibile + CRC16 poly 0xA001) — robusta anche se il jitter
   USB ha fuso frame adiacenti,
3. in parallelo segmenta per gap temporali e, sui frame non-Modbus,
   caratterizza il framing (lunghezze ricorrenti, header/footer fissi,
   checksum alternativi),
4. emette un verdetto: MODBUS (con indirizzi, function code e mappa registri)
   oppure NON-Modbus (con caratterizzazione).

Uso:
  .venv/bin/python analyze.py logs/capture_XXXX.jsonl
  .venv/bin/python analyze.py logs/capture_XXXX.jsonl --gap-ms 8
"""

import argparse
import json
import sys
from collections import Counter, defaultdict

# Mappa registri nota dai componenti pico1881 (Innova AirLeaf) — usata solo
# per etichettare, non per validare.
KNOWN_REGS = {
    0: "air_temperature (x10)",
    1: "water_temperature (x10)",
    9: "output_flags (bit3=boiler, bit2=chiller)",
    15: "fan_speed",
    201: "program (bit0-2=fan, bit4=keylock, bit7=standby)",
    231: "setpoint (x10)",
    233: "season (3=heat, 5=cool)",
}

FUNC_NAMES = {1: "Read Coils", 2: "Read Discrete In", 3: "Read Holding Reg",
              4: "Read Input Reg", 5: "Write Coil", 6: "Write Single Reg",
              15: "Write Mult Coils", 16: "Write Mult Reg"}


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def crc_ok(frame: bytes) -> bool:
    return len(frame) >= 4 and crc16_modbus(frame[:-2]) == (frame[-2] | frame[-1] << 8)


def load(path: str):
    """Ritorna (bytes_stream, times_ms_per_byte, markers, meta)."""
    stream = bytearray()
    times = []
    markers = []
    meta = {}
    with open(path) as f:
        for line in f:
            rec = json.loads(line)
            if "meta" in rec:
                meta = rec["meta"]
            elif "mark" in rec:
                markers.append((rec["t"], rec["mark"]))
            elif "data" in rec:
                chunk = bytes.fromhex(rec["data"])
                stream.extend(chunk)
                # tempo del chunk assegnato a ogni byte (non abbiamo di meglio)
                times.extend([rec["t"]] * len(chunk))
    return bytes(stream), times, markers, meta


# --- Riconoscimento frame Modbus a lunghezza deducibile -----------------

def candidate_lengths(buf: bytes, i: int):
    """Lunghezze plausibili per un frame Modbus che inizia a buf[i]."""
    if len(buf) - i < 4:
        return
    addr, func = buf[i], buf[i + 1]
    if not (1 <= addr <= 247):
        return
    base = func & 0x7F
    if func & 0x80:  # exception response
        yield 5
        return
    if base in (1, 2, 3, 4):
        yield 8                       # richiesta
        if len(buf) - i >= 3:
            yield 5 + buf[i + 2]      # risposta: addr func bytecount ... crc
    elif base in (5, 6):
        yield 8                       # richiesta e risposta (echo)
    elif base == 15:
        if len(buf) - i >= 7:
            yield 9 + buf[i + 6]      # richiesta
        yield 8                       # risposta
    elif base == 16:
        if len(buf) - i >= 7:
            yield 9 + buf[i + 6]
        yield 8


def scan_modbus(buf: bytes):
    """Scansione greedy: lista di (start, frame) validi non sovrapposti."""
    frames = []
    i = 0
    n = len(buf)
    while i < n - 3:
        matched = None
        for L in sorted(set(candidate_lengths(buf, i) or [])):
            if 4 <= L <= 256 and i + L <= n and crc_ok(buf[i:i + L]):
                matched = buf[i:i + L]
                break
        if matched:
            frames.append((i, matched))
            i += len(matched)
        else:
            i += 1
    return frames


def decode_frame(frame: bytes, pending_reads: dict) -> str:
    addr, func = frame[0], frame[1]
    base = func & 0x7F
    if func & 0x80:
        return f"slave {addr}: ECCEZIONE func {base} code {frame[2]}"
    if base in (3, 4) and len(frame) == 8:
        reg = frame[2] << 8 | frame[3]
        cnt = frame[4] << 8 | frame[5]
        pending_reads[addr] = (reg, cnt)
        label = KNOWN_REGS.get(reg, "?")
        return f"master -> slave {addr}: {FUNC_NAMES[base]} reg {reg} x{cnt}  [{label}]"
    if base in (3, 4) and len(frame) == 5 + frame[2]:
        vals = [frame[3 + j * 2] << 8 | frame[4 + j * 2] for j in range(frame[2] // 2)]
        reg, _ = pending_reads.get(addr, (None, None))
        label = f"reg {reg} [{KNOWN_REGS.get(reg, '?')}]" if reg is not None else "reg ?"
        return f"slave {addr} -> master: risposta {label} = {vals}"
    if base == 6 and len(frame) == 8:
        reg = frame[2] << 8 | frame[3]
        val = frame[4] << 8 | frame[5]
        label = KNOWN_REGS.get(reg, "?")
        return f"write/echo slave {addr}: reg {reg} = {val}  [{label}]"
    return f"slave {addr} func {base}: {frame.hex(' ')}"


# --- Caratterizzazione non-Modbus ----------------------------------------

def segment_by_gaps(stream: bytes, times, gap_ms: float):
    frames = []
    if not stream:
        return frames
    start = 0
    for i in range(1, len(stream)):
        if times[i] - times[i - 1] > gap_ms:
            frames.append(stream[start:i])
            start = i
    frames.append(stream[start:])
    return frames


def try_checksums(frames):
    """Conta per quanti frame l'ultimo/i byte combaciano con checksum comuni."""
    def crc8(data, poly):
        crc = 0
        for b in data:
            crc ^= b
            for _ in range(8):
                crc = ((crc << 1) ^ poly) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
        return crc

    checks = {
        "sum8 (ultimo byte = somma mod 256)": lambda f: sum(f[:-1]) & 0xFF == f[-1],
        "sum8 negato": lambda f: (-sum(f[:-1])) & 0xFF == f[-1],
        "xor8": lambda f: __import__("functools").reduce(lambda a, b: a ^ b, f[:-1], 0) == f[-1],
        "crc8 poly 0x07": lambda f: crc8(f[:-1], 0x07) == f[-1],
        "crc8 poly 0x31": lambda f: crc8(f[:-1], 0x31) == f[-1],
        "crc16 modbus (LE)": crc_ok,
        "crc16 modbus (BE)": lambda f: len(f) >= 4 and crc16_modbus(f[:-2]) == (f[-1] | f[-2] << 8),
    }
    usable = [f for f in frames if len(f) >= 4]
    results = {}
    for name, fn in checks.items():
        hits = sum(1 for f in usable if fn(f))
        if hits:
            results[name] = (hits, len(usable))
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--gap-ms", type=float, default=None,
                    help="Soglia gap per segmentazione (default: max(3.5 char, 5 ms))")
    ap.add_argument("--dump", action="store_true", help="Stampa ogni frame Modbus decodificato")
    args = ap.parse_args()

    stream, times, markers, meta = load(args.log)
    baud = meta.get("baud", 9600)
    gap_ms = args.gap_ms or max(3.5 * 11.0 / baud * 1000.0, 5.0)

    print(f"Log: {args.log}  ({meta.get('baud')}-8{meta.get('parity')}{meta.get('stopbits')})")
    print(f"Byte totali: {len(stream)}   marker: {len(markers)}   soglia gap: {gap_ms:.1f} ms\n")
    if not stream:
        sys.exit("Stream vuoto, niente da analizzare.")

    # 1. Scansione Modbus
    frames = scan_modbus(stream)
    covered = sum(len(f) for _, f in frames)
    coverage = covered / len(stream)

    # 2. Segmentazione per gap (per statistiche e per il ramo non-Modbus)
    gap_frames = segment_by_gaps(stream, times, gap_ms)

    print(f"Frame Modbus validi (CRC ok): {len(frames)}  "
          f"({covered}/{len(stream)} byte = {coverage:.0%} dello stream)")
    print(f"Frame da segmentazione temporale: {len(gap_frames)}\n")

    if coverage >= 0.5 and len(frames) >= 5:
        print("=" * 60)
        print("VERDETTO: QUESTO È MODBUS RTU")
        print("=" * 60)
        addrs = Counter(f[0] for _, f in frames)
        funcs = Counter(f[1] & 0x7F for _, f in frames)
        print(f"Indirizzi slave visti: {dict(addrs)}")
        print(f"Function code: { {FUNC_NAMES.get(k, k): v for k, v in funcs.items()} }\n")

        pending = {}
        reg_values = defaultdict(list)
        decoded = []
        for pos, f in frames:
            d = decode_frame(f, dict(pending))
            base = f[1] & 0x7F
            if base in (3, 4) and len(f) == 8:
                pending[f[0]] = (f[2] << 8 | f[3], f[4] << 8 | f[5])
            elif base in (3, 4) and len(f) == 5 + f[2] and f[0] in pending:
                reg, _ = pending[f[0]]
                for j in range(f[2] // 2):
                    reg_values[reg + j].append(f[3 + j * 2] << 8 | f[4 + j * 2])
            elif base == 6:
                reg_values[f[2] << 8 | f[3]].append(f[4] << 8 | f[5])
            decoded.append((times[pos], d))

        print("Mappa registri osservata:")
        for reg in sorted(reg_values):
            vals = reg_values[reg]
            uniq = sorted(set(vals))
            shown = uniq if len(uniq) <= 8 else uniq[:8] + ["..."]
            print(f"  reg {reg:5d}  [{KNOWN_REGS.get(reg, '?'):45s}]  "
                  f"{len(vals)} letture, valori: {shown}")

        if markers:
            print("\nCorrelazione marker <-> traffico:")
            events = sorted([(t, "MARK", m) for t, m in markers] +
                            [(t, "FRAME", d) for t, d in decoded])
            for t, kind, txt in events:
                prefix = ">>>" if kind == "MARK" else "   "
                print(f"{prefix} {t:10.0f} ms  {txt}")
        elif args.dump:
            for t, d in decoded:
                print(f"   {t:10.0f} ms  {d}")
    else:
        print("=" * 60)
        print("VERDETTO: NON è Modbus RTU standard" if coverage < 0.1
              else f"VERDETTO INCERTO: solo {coverage:.0%} dello stream valida come Modbus")
        print("=" * 60)
        lens = Counter(len(f) for f in gap_frames)
        print(f"\nLunghezze frame ricorrenti (da gap): {lens.most_common(10)}")

        firsts = Counter(f[0] for f in gap_frames if f)
        lasts = Counter(f[-1] for f in gap_frames if f)
        print(f"Primo byte più comune:  {[(hex(b), c) for b, c in firsts.most_common(5)]}")
        print(f"Ultimo byte più comune: {[(hex(b), c) for b, c in lasts.most_common(5)]}")

        # header comune: prefisso condiviso dai frame della lunghezza più frequente
        if lens:
            top_len = lens.most_common(1)[0][0]
            same = [f for f in gap_frames if len(f) == top_len]
            if len(same) >= 3:
                prefix_len = 0
                for i in range(top_len):
                    if len(set(f[i] for f in same)) == 1:
                        prefix_len += 1
                    else:
                        break
                if prefix_len:
                    print(f"Header fisso (frame da {top_len} byte): "
                          f"{same[0][:prefix_len].hex(' ')}")

        cs = try_checksums(gap_frames)
        if cs:
            print("\nChecksum candidati (hit/frame testati):")
            for name, (h, tot) in sorted(cs.items(), key=lambda kv: -kv[1][0]):
                print(f"  {name}: {h}/{tot}")
        else:
            print("\nNessun checksum comune riconosciuto sugli ultimi byte.")

        print("\nPrimi 10 frame (hex):")
        for f in gap_frames[:10]:
            asc = "".join(chr(b) if 32 <= b < 127 else "." for b in f)
            print(f"  [{len(f):3d}] {f.hex(' ')}  |{asc}|")

        if markers:
            print("\nMarker registrati:")
            for t, m in markers:
                print(f"  {t:10.0f} ms  {m}")


if __name__ == "__main__":
    main()
