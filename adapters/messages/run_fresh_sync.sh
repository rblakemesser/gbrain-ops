#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export GBRAIN_OPS_MESSAGES_ROOT="${GBRAIN_OPS_MESSAGES_ROOT:-$HERE}"
ENV_FILE="${GBRAIN_OPS_ENV_FILE:-$HERE/runtime.env}"
if [[ -f "$ENV_FILE" ]]; then set -a; source "$ENV_FILE"; set +a; fi
PYTHON="${PYTHON:-python3}"
RECENT_DAYS="${GBRAIN_OPS_RECENT_DAYS:-3}"
: "${GBRAIN_OPS_REPO:?GBRAIN_OPS_REPO is required}"
: "${GBRAIN_OWNER_CREDENTIALS:?GBRAIN_OWNER_CREDENTIALS is required}"
ARCHIVE_ROOT="${GBRAIN_OPS_MESSAGES_ARCHIVE_ROOT:-$HERE/brain/daily}"
RECEIPT_ROOT="${GBRAIN_OWNER_RECEIPT_ROOT:-$HOME/.gbrain/owner/receipts/messages}"
LOG_ROOT="${GBRAIN_OPS_LOG_ROOT:-$HERE/logs}"
umask 077
mkdir -p "$LOG_ROOT"

"$PYTHON" "$HERE/messages_collector.py" recent --days "$RECENT_DAYS" >"$LOG_ROOT/collector-latest.log" 2>&1
PYTHONPATH="$GBRAIN_OPS_REPO/src${PYTHONPATH:+:$PYTHONPATH}" \
  "$PYTHON" "$GBRAIN_OPS_REPO/scripts/reconcile_archive.py" \
  --credentials "$GBRAIN_OWNER_CREDENTIALS" \
  --root "$ARCHIVE_ROOT" \
  --receipt-root "$RECEIPT_ROOT" \
  --glob 'messages/**/*.md' \
  --dated-within-days "$((RECENT_DAYS + 2))" \
  --summary-only
printf '%s\n' 'collector=messages status=acknowledged'
