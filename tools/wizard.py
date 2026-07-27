#!/usr/bin/env python3
"""
Wizard interattivo per mappare i registri Modbus di fancoil Innova diversi
dall'OSMO (es. controller multi-zona), usando l'ESP come sniffer passivo
via WiFi al posto di un adattatore USB-seriale fisico davanti alla scheda.

Prerequisiti:
  - Flasha example-sniffer.yaml sull'ESP collegato in RX (vedi commenti nel
    file): non trasmette nulla sul bus, quindi puoi lasciare il modulo
    WiFi/cloud originale e i termostati collegati e funzionanti.
  - esphome CLI installato.

Uso:
  tools/wizard.py <config.yaml> [--device host] [--seconds 6] [--out file.jsonl]

Ad ogni round: chiede una breve etichetta per l'azione che stai per fare
(es. "zona3 fan on"), dice di eseguirla sull'app/telecomando/termostato,
sniffa per N secondi, decodifica i frame Modbus visti e li registra.
Etichetta vuota per terminare: alla fine stampa una tabella che confronta
i valori dei registri round per round, evidenziando quelli cambiati
(candidati per lo stato che stai cercando).

Il log grezzo (bytes + etichette, formato compatibile con analyze.py) viene
salvato in --out per poterlo rianalizzare o condividere.
"""
import argparse
import json
import re
import subprocess
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from analyze import scan_modbus, decode_frame, KNOWN_REGS  # noqa: E402

LINE_RE = re.compile(r"\[sniff:\d+\]:\s*RX\s+([0-9A-Fa-f ]+)")


def stream_esphome_logs(config, device, byte_queue, queue_lock, stop_event):
    cmd = ["esphome", "logs", config]
    if device:
        cmd += ["--device", device]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, bufsize=1)
    try:
        for line in proc.stdout:
            if stop_event.is_set():
                break
            m = LINE_RE.search(line)
            if m:
                try:
                    chunk = bytes.fromhex(m.group(1).replace(" ", ""))
                except ValueError:
                    continue
                with queue_lock:
                    byte_queue.append((time.monotonic(), chunk))
    finally:
        stop_event.set()
        proc.terminate()


def drain(byte_queue, queue_lock, consumed):
    """Ritorna (nuovi_byte, nuovo_consumed) senza ri-consumare quelli gia' letti."""
    with queue_lock:
        new_items = byte_queue[consumed:]
        consumed = len(byte_queue)
    collected = bytearray()
    for _, chunk in new_items:
        collected.extend(chunk)
    return bytes(collected), consumed


def summarize_round(label, stream):
    frames = scan_modbus(stream)
    reg_values = defaultdict(list)
    pending = {}
    print(f"\n--- round '{label}': {len(frames)} frame Modbus, {len(stream)} byte grezzi ---")
    for _, f in frames:
        base = f[1] & 0x7F
        print(f"   {decode_frame(f, dict(pending))}")
        if base in (3, 4) and len(f) == 8:
            pending[f[0]] = (f[2] << 8 | f[3], f[4] << 8 | f[5])
        elif base in (3, 4) and len(f) == 5 + f[2] and f[0] in pending:
            reg, _ = pending[f[0]]
            for j in range(f[2] // 2):
                reg_values[reg + j].append(f[3 + j * 2] << 8 | f[4 + j * 2])
        elif base == 6:
            reg_values[f[2] << 8 | f[3]].append(f[4] << 8 | f[5])
    if not frames:
        print("   (nessun frame Modbus valido in questa finestra: nessun traffico o CRC non valido)")
    return reg_values


def print_comparison(rounds):
    print("\n" + "=" * 70)
    print("CONFRONTO REGISTRI TRA ROUND")
    print("=" * 70)
    all_regs = sorted({r for rv in rounds.values() for r in rv})
    if not all_regs:
        print("Nessun registro osservato in nessun round.")
        return
    for reg in all_regs:
        per_label = {label: sorted(set(rv.get(reg, []))) for label, rv in rounds.items()}
        distinct = {tuple(v) for v in per_label.values() if v}
        marker = "  <-- CANDIDATO (valore diverso tra round)" if len(distinct) > 1 else ""
        label_str = "   ".join(f"{label}={vals}" for label, vals in per_label.items() if vals)
        print(f"reg {reg:5d} [{KNOWN_REGS.get(reg, '?'):45s}]{marker}\n    {label_str}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config", help="Config ESPHome dello sniffer (es. example-sniffer.yaml)")
    ap.add_argument("--device", default=None, help="Host/IP dell'ESP (altrimenti selezione interattiva)")
    ap.add_argument("--seconds", type=float, default=6.0, help="Durata di ogni finestra di sniffing")
    ap.add_argument("--out", default=None, help="File JSONL di output (default: logs/wizard_<ts>.jsonl)")
    args = ap.parse_args()

    out_path = args.out or f"logs/wizard_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    log_f = open(out_path, "w")
    log_f.write(json.dumps({"meta": {"config": args.config, "started": datetime.now().isoformat()}}) + "\n")

    byte_queue = []
    queue_lock = threading.Lock()
    stop_event = threading.Event()
    thread = threading.Thread(target=stream_esphome_logs,
                               args=(args.config, args.device, byte_queue, queue_lock, stop_event),
                               daemon=True)
    thread.start()

    print(f"Log: {out_path}")
    print("Connessione all'ESP in corso (via 'esphome logs')... attendo qualche secondo.\n")
    time.sleep(3)
    if stop_event.is_set():
        sys.exit("Connessione a 'esphome logs' terminata subito: controlla config/device/rete.")

    rounds = {}
    consumed = 0
    try:
        while True:
            label = input("\nEtichetta azione (INVIO vuoto per terminare): ").strip()
            if not label:
                break
            input(f"Esegui ora '{label}' sull'app/telecomando/termostato, poi premi INVIO "
                  f"per sniffare {args.seconds:.0f}s... ")
            log_f.write(json.dumps({"t": round((time.monotonic() - t0) * 1000, 3), "mark": label}) + "\n")
            log_f.flush()

            # scarta quanto arrivato prima dell'azione (rumore del round precedente)
            _, consumed = drain(byte_queue, queue_lock, consumed)
            time.sleep(args.seconds)
            stream, consumed = drain(byte_queue, queue_lock, consumed)

            if stream:
                log_f.write(json.dumps({"t": round((time.monotonic() - t0) * 1000, 3),
                                         "data": stream.hex()}) + "\n")
                log_f.flush()

            rounds[label] = summarize_round(label, stream)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        log_f.close()

    if rounds:
        print_comparison(rounds)
    else:
        print("Nessun round registrato.")
    print(f"\nLog completo salvato in: {out_path}")


if __name__ == "__main__":
    main()
