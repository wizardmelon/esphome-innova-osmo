#!/usr/bin/env bash
# Compila, flasha via OTA (WiFi) e resta in ascolto sui log via API.
#
# Va lanciato IN LOCALE, su una macchina che sta sulla stessa rete WiFi del
# fancoil (questo script non funziona da una sandbox remota senza accesso
# alla LAN di casa).
#
# Uso:
#   tools/watch-fancoil.sh <config.yaml> [device]
#
# Esempio:
#   tools/watch-fancoil.sh example-fancoil.yaml fancoil-1.local
#
# Se "device" viene omesso, esphome chiede interattivamente quale dispositivo
# usare (utile se non conosci l'hostname/IP).
#
# Richiede nella root del repo:
#   - esphome CLI installato (pip install esphome, oppure .venv/bin/esphome)
#   - secrets.yaml con: wifi_ssid, wifi_password, fancoil_api_key,
#     fancoil_ota_password
#
# Il logger seriale e' disabilitato (UART0 e' occupata dal Modbus), quindi i
# log arrivano solo via WiFi/API: esphome li mostra automaticamente dopo il
# flash OTA riuscito, senza bisogno di un comando separato.
set -euo pipefail

CONFIG="${1:?Uso: $0 <config.yaml> [device]}"
DEVICE="${2:-}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

ESPHOME=esphome
if [ -x ".venv/bin/esphome" ]; then
  ESPHOME=".venv/bin/esphome"
fi

BRANCH="claude/fancoil-climate-status-9s24ms"
echo "==> Aggiorno il branch locale ($BRANCH)"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git merge --ff-only "origin/$BRANCH"

mkdir -p logs
TS="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="logs/${TS}-$(basename "${CONFIG%.yaml}").log"

echo "==> Compilo e flasho via OTA: $CONFIG"
if [ -n "$DEVICE" ]; then
  "$ESPHOME" run "$CONFIG" --device "$DEVICE" 2>&1 | tee "$LOG_FILE"
else
  "$ESPHOME" run "$CONFIG" 2>&1 | tee "$LOG_FILE"
fi

echo
echo "==> Log salvati in: $LOG_FILE"
echo "==> Mandami questo file (o il suo contenuto) in chat per l'analisi incrociata."
