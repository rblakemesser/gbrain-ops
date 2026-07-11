#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export GBRAIN_OPS_GMAIL_ROOT="${GBRAIN_OPS_GMAIL_ROOT:-$HERE}"
ENV_FILE="${GBRAIN_OPS_ENV_FILE:-$HERE/runtime.env}"
if [[ -f "$ENV_FILE" ]]; then set -a; source "$ENV_FILE"; set +a; fi
PYTHON="${PYTHON:-python3}"
"$PYTHON" "$HERE/email_collector.py" recent --days "${GBRAIN_OPS_RECENT_DAYS:-2}" --workers 1
"$PYTHON" "$HERE/classify_archive.py" >/dev/null
"$PYTHON" "$HERE/promote_archive.py" >/dev/null
printf '%s
' 'collector=gmail status=collected'
