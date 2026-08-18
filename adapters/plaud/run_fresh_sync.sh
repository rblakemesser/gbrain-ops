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
RECENT_DAYS="${GBRAIN_OPS_RECENT_DAYS:-3}"
FULL_AUDIT_HOURS="${GBRAIN_OPS_FULL_AUDIT_HOURS:-24}"
MAX_RECORDINGS="${GBRAIN_OPS_MAX_RECORDINGS:-20}"
COLLECTION_TIMEOUT_SECONDS="${GBRAIN_OPS_PLAUD_COLLECTION_TIMEOUT_SECONDS:-900}"
LOCK_DIR="$HERE/.fresh-sync.lock"

umask 077
mkdir -p "$LOG_ROOT"
chmod 700 "$LOG_ROOT"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  printf '%s\n' 'collector=plaud status=overlap-denied'
  exit 75
fi
trap 'rm -rf "$LOCK_DIR"' EXIT
RUN_STARTED_AT_NS="$("$PLAUD_PYTHON" -c 'import time; print(time.time_ns())')"

COLLECTOR_STATUS=0
if "$PLAUD_PYTHON" "$HERE/plaud_collector.py" recent \
  --days "$RECENT_DAYS" \
  --full-audit-hours "$FULL_AUDIT_HOURS" \
  --max-recordings "$MAX_RECORDINGS" \
  --collection-timeout-seconds "$COLLECTION_TIMEOUT_SECONDS" \
  >"$LOG_ROOT/collector-latest.log" 2>&1; then
  :
else
  COLLECTOR_STATUS=$?
  "$PLAUD_PYTHON" -c '
import json
import os
import sys

try:
    if os.stat(sys.argv[1]).st_mtime_ns < int(sys.argv[2]):
        raise OSError("stale summary")
    with open(sys.argv[1], encoding="utf-8") as handle:
        payload = json.load(handle)
except (OSError, json.JSONDecodeError):
    payload = {"failure_count": 1, "failure_classes": {"unknown": 1}}
classes = payload.get("failure_classes") or {"unknown": payload.get("failure_count", 1)}
rendered = ",".join("%s:%s" % item for item in sorted(classes.items()))
print(
    "collector=plaud status=error failure_count=%s failure_classes=%s"
    % (payload.get("failure_count", 1), rendered)
)
' "$HERE/sync-summary.json" "$RUN_STARTED_AT_NS" >&2
fi
RECONCILE_STATUS=0
if PYTHONPATH="$GBRAIN_OPS_REPO/src${PYTHONPATH:+:$PYTHONPATH}" \
  "$OWNER_PYTHON" "$GBRAIN_OPS_REPO/scripts/reconcile_archive.py" \
  --credentials "$GBRAIN_OWNER_CREDENTIALS" \
  --root "$HERE/brain" \
  --receipt-root "$RECEIPT_ROOT" \
  --glob 'plaud/**/*.md' \
  --summary-only; then
  :
else
  RECONCILE_STATUS=$?
fi
if (( COLLECTOR_STATUS != 0 )); then
  exit "$COLLECTOR_STATUS"
fi
if (( RECONCILE_STATUS != 0 )); then
  exit "$RECONCILE_STATUS"
fi
printf '%s\n' 'collector=plaud status=acknowledged'
