#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export GBRAIN_OPS_GRANOLA_ROOT="${GBRAIN_OPS_GRANOLA_ROOT:-$HERE}"
ENV_FILE="${GBRAIN_OPS_ENV_FILE:-$HERE/runtime.env}"
if [[ -f "$ENV_FILE" ]]; then set -a; source "$ENV_FILE"; set +a; fi
PYTHON="${PYTHON:-python3}"
"$PYTHON" "$HERE/granola_collector.py" recent --days "${GBRAIN_OPS_RECENT_DAYS:-3}" --overlap-hours 24 --page-size 20
printf '%s
' 'collector=granola status=collected'
