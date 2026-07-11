#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export GBRAIN_OPS_CALENDAR_ROOT="${GBRAIN_OPS_CALENDAR_ROOT:-$HERE}"
ENV_FILE="${GBRAIN_OPS_ENV_FILE:-$HERE/runtime.env}"
if [[ -f "$ENV_FILE" ]]; then set -a; source "$ENV_FILE"; set +a; fi
PYTHON="${PYTHON:-python3}"
CURRENT_MONTH="$(date +%Y-%m)"
"$PYTHON" "$HERE/calendar_collector.py" backfill --start-month "$CURRENT_MONTH" --max-months 1
"$PYTHON" "$HERE/classify_archive.py" >/dev/null
"$PYTHON" "$HERE/promote_archive.py" >/dev/null
printf '%s
' 'collector=calendar status=collected'
