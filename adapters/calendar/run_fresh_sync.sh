#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export GBRAIN_OPS_CALENDAR_ROOT="${GBRAIN_OPS_CALENDAR_ROOT:-$HERE}"
ENV_FILE="${GBRAIN_OPS_ENV_FILE:-$HERE/runtime.env}"
if [[ -f "$ENV_FILE" ]]; then set -a; source "$ENV_FILE"; set +a; fi
PYTHON="${PYTHON:-python3}"
CURRENT_MONTH="$(date +%Y-%m)"
DATE_WINDOW_DAYS="${GBRAIN_OPS_DATE_WINDOW_DAYS:-45}"
: "${GBRAIN_OPS_REPO:?GBRAIN_OPS_REPO is required}"
: "${GBRAIN_OWNER_CREDENTIALS:?GBRAIN_OWNER_CREDENTIALS is required}"
ARCHIVE_ROOT="${GBRAIN_OPS_CALENDAR_ARCHIVE_ROOT:-$HERE/brain}"
RECEIPT_ROOT="${GBRAIN_OWNER_RECEIPT_ROOT:-$HOME/.gbrain/owner/receipts/calendar}"
LOG_ROOT="${GBRAIN_OPS_LOG_ROOT:-$HERE/logs}"
umask 077
mkdir -p "$LOG_ROOT"

{
  "$PYTHON" "$HERE/calendar_collector.py" backfill --start-month "$CURRENT_MONTH" --max-months 1
  "$PYTHON" "$HERE/classify_archive.py"
  "$PYTHON" "$HERE/promote_archive.py"
} >"$LOG_ROOT/collector-latest.log" 2>&1
PYTHONPATH="$GBRAIN_OPS_REPO/src${PYTHONPATH:+:$PYTHONPATH}" \
  "$PYTHON" "$GBRAIN_OPS_REPO/scripts/reconcile_archive.py" \
  --credentials "$GBRAIN_OWNER_CREDENTIALS" \
  --root "$ARCHIVE_ROOT" \
  --receipt-root "$RECEIPT_ROOT" \
  --glob 'daily/calendar/**/*.md' \
  --dated-within-days "$DATE_WINDOW_DAYS" \
  --summary-only
PYTHONPATH="$GBRAIN_OPS_REPO/src${PYTHONPATH:+:$PYTHONPATH}" \
  "$PYTHON" "$GBRAIN_OPS_REPO/scripts/reconcile_archive.py" \
  --credentials "$GBRAIN_OWNER_CREDENTIALS" \
  --root "$ARCHIVE_ROOT" \
  --receipt-root "$RECEIPT_ROOT" \
  --glob 'promoted/**/*.md' \
  --summary-only
printf '%s\n' 'collector=calendar status=acknowledged'
