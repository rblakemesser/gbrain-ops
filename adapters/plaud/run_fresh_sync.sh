#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
if [[ -f "$HERE/runtime.env" ]]; then
  # shellcheck disable=SC1091
  set -a
  source "$HERE/runtime.env"
  set +a
fi

export GBRAIN_OPS_PLAUD_ROOT="${GBRAIN_OPS_PLAUD_ROOT:-$HERE}"
PLAUD_PYTHON="${GBRAIN_OPS_PLAUD_PYTHON:-$HOME/.pyenv/versions/3.13.3/bin/python3}"
OWNER_PYTHON="${GBRAIN_OPS_OWNER_PYTHON:-$HOME/.pyenv/versions/3.13.3/bin/python3}"
GBRAIN_OPS_REPO="${GBRAIN_OPS_REPO:-$HOME/workspace/gbrain-ops}"
GBRAIN_OWNER_CREDENTIALS="${GBRAIN_OWNER_CREDENTIALS:-$HOME/.gbrain/owner/clients/plaud.json}"
RECEIPT_ROOT="${GBRAIN_OWNER_RECEIPT_ROOT:-$HOME/.gbrain/owner/receipts/plaud}"
LOG_ROOT="${GBRAIN_OPS_LOG_ROOT:-$HERE/logs}"
RECENT_DAYS="${GBRAIN_OPS_RECENT_DAYS:-30}"
FULL_AUDIT_HOURS="${GBRAIN_OPS_FULL_AUDIT_HOURS:-24}"
LOCK_DIR="$HERE/.fresh-sync.lock"

umask 077
mkdir -p "$LOG_ROOT"
chmod 700 "$LOG_ROOT"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  printf '%s\n' 'collector=plaud status=overlap-denied'
  exit 75
fi
trap 'rm -rf "$LOCK_DIR"' EXIT

"$PLAUD_PYTHON" "$HERE/plaud_collector.py" recent \
  --days "$RECENT_DAYS" \
  --full-audit-hours "$FULL_AUDIT_HOURS" \
  >"$LOG_ROOT/collector-latest.log" 2>&1
PYTHONPATH="$GBRAIN_OPS_REPO/src${PYTHONPATH:+:$PYTHONPATH}" \
  "$OWNER_PYTHON" "$GBRAIN_OPS_REPO/scripts/reconcile_archive.py" \
  --credentials "$GBRAIN_OWNER_CREDENTIALS" \
  --root "$HERE/brain" \
  --receipt-root "$RECEIPT_ROOT" \
  --glob 'plaud/**/*.md' \
  --summary-only
printf '%s\n' 'collector=plaud status=acknowledged'
