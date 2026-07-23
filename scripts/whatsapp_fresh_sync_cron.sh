#!/usr/bin/env bash
set -euo pipefail

exec "$HOME/.pyenv/versions/3.13.3/bin/python3" \
  "$HOME/.gbrain/integrations/sync-monitor/sync_runner.py" \
  --job whatsapp-fresh-sync \
  --command "$HOME/.gbrain/integrations/whatsapp-to-brain/run_fresh_sync.sh" \
  --timeout 900 \
  --state-dir "$HOME/.gbrain/integrations/sync-monitor/state" \
  --log-dir "$HOME/.gbrain/integrations/sync-monitor/logs"
