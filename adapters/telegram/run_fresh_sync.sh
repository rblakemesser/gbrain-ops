#!/usr/bin/env bash
# Account-wide Telegram fresh sync. It never sends Telegram messages.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export GBRAIN_OPS_TELEGRAM_ROOT="${GBRAIN_OPS_TELEGRAM_ROOT:-$HERE}"
ENV_FILE="${GBRAIN_OPS_ENV_FILE:-$HERE/runtime.env}"
PYTHON="${PYTHON:-$HERE/.venv/bin/python}"
LOCK_DIR="$GBRAIN_OPS_TELEGRAM_ROOT/data/fresh-sync.lock"
if [[ ! -x "$PYTHON" ]]; then echo "telegram_fresh_sync_error=collector_python_missing" >&2; exit 2; fi
if [[ ! -f "$ENV_FILE" ]]; then echo "telegram_fresh_sync_error=runtime_env_missing" >&2; exit 2; fi
if [[ ! -f "$GBRAIN_OPS_TELEGRAM_ROOT/data/telegram.session" ]]; then echo "telegram_fresh_sync_error=mtproto_session_missing" >&2; exit 2; fi
if ! mkdir "$LOCK_DIR" 2>/dev/null; then echo "telegram_fresh_sync_error=lock_held" >&2; exit 75; fi
trap 'rmdir "$LOCK_DIR"' EXIT
set -a
source "$ENV_FILE" >/dev/null
set +a
"$PYTHON" "$HERE/telegram_collector.py" sync --config "$GBRAIN_OPS_TELEGRAM_ROOT/config.json" --mode recent
"$PYTHON" "$HERE/telegram_collector.py" render --source-id telegram
printf '%s\n' 'collector=telegram status=collected'
