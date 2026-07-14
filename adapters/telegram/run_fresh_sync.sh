#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
export GBRAIN_OPS_TELEGRAM_ROOT="${GBRAIN_OPS_TELEGRAM_ROOT:-$HERE}"
ENV_FILE="${GBRAIN_OPS_ENV_FILE:-$HERE/runtime.env}"
if [[ -f "$ENV_FILE" ]]; then set -a; source "$ENV_FILE"; set +a; fi

TELEGRAM_PYTHON="${GBRAIN_OPS_TELEGRAM_PYTHON:-$HERE/.venv/bin/python}"
OWNER_PYTHON="${GBRAIN_OPS_OWNER_PYTHON:-$HOME/.pyenv/versions/3.13.3/bin/python3}"
GBRAIN_OPS_REPO="${GBRAIN_OPS_REPO:-$HOME/workspace/gbrain-ops}"
GBRAIN_OWNER_CREDENTIALS="${GBRAIN_OWNER_CREDENTIALS:-$HOME/.gbrain/owner/clients/telegram.json}"
RECEIPT_ROOT="${GBRAIN_OWNER_RECEIPT_ROOT:-$HOME/.gbrain/owner/receipts/telegram}"
LOG_ROOT="${GBRAIN_OPS_LOG_ROOT:-$HERE/logs}"
RECENT_DAYS="${GBRAIN_OPS_RECENT_DAYS:-3}"
LOCK_DIR="$HERE/.fresh-sync.lock"

umask 077
mkdir -p "$LOG_ROOT"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  printf '%s\n' 'collector=telegram status=overlap-denied'
  exit 75
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

{
  "$TELEGRAM_PYTHON" "$HERE/telegram_collector.py" inventory --config "$HERE/config.json"
  "$TELEGRAM_PYTHON" "$HERE/telegram_collector.py" sync --mode recent --config "$HERE/config.json"
  "$TELEGRAM_PYTHON" "$HERE/telegram_collector.py" render --source-id telegram
} >"$LOG_ROOT/collector-latest.log" 2>&1

PYTHONPATH="$GBRAIN_OPS_REPO/src${PYTHONPATH:+:$PYTHONPATH}" \
  "$OWNER_PYTHON" "$GBRAIN_OPS_REPO/scripts/reconcile_archive.py" \
  --credentials "$GBRAIN_OWNER_CREDENTIALS" \
  --root "$HERE/brain/telegram" \
  --receipt-root "$RECEIPT_ROOT" \
  --glob '**/*.md' \
  --dated-within-days "$((RECENT_DAYS + 2))" \
  --summary-only
printf '%s\n' 'collector=telegram status=acknowledged'
