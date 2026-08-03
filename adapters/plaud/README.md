# Plaud → GBrain

First-class Plaud personal-library ingestion using the official `@plaud-ai/mcp` stdio package.

## Data contract

- Stable source identity: `plaud:<recording_id>`.
- One canonical Markdown page per recording under `brain/plaud/<year>/`.
- Full paginated `transaction` transcript with Plaud timestamps and current speaker labels.
- Every generated item in Plaud's `note_list`.
- Private current normalized source snapshots plus immutable semantic revisions.
- Temporary signed audio/data URLs and OAuth material are redacted before archival.
- Recent recordings are refreshed every run; a complete catalog/content audit runs at least daily because Plaud exposes no `updated_at`, webhook, or tombstone feed.
- The MCP child receives only a minimal Plaud/runtime environment and runs with official telemetry disabled.
- Linked note bodies are fetched in memory from Plaud's HTTPS capability URL, then the URL is removed before archival.
- No audio download, speaker inference, contact matching, or upstream deletion propagation.

## Runtime

The live directory is `~/.gbrain/integrations/plaud-to-brain`. Code files are installed as symlinks by `scripts/install_runtime_links.py`; raw data, state, logs, and `runtime.env` stay private and untracked.

Required runtime variables:

```text
PLAUD_MCP_COMMAND=/absolute/path/to/plaud-mcp
GBRAIN_OPS_REPO=/absolute/path/to/gbrain-ops
GBRAIN_OPS_PLAUD_PYTHON=/absolute/path/to/python3
GBRAIN_OPS_OWNER_PYTHON=/absolute/path/to/python3
GBRAIN_OWNER_CREDENTIALS=~/.gbrain/owner/clients/plaud.json
```

Optional tuning:

```text
GBRAIN_OPS_RECENT_DAYS=30
GBRAIN_OPS_FULL_AUDIT_HOURS=24
```

Authenticate once after installing the official MCP package:

```bash
umask 077
set -a
source ~/.gbrain/integrations/plaud-to-brain/runtime.env
set +a
~/.gbrain/integrations/plaud-to-brain/plaud_collector.py auth
```

Backfill and recurring smoke checks:

```bash
~/.gbrain/integrations/plaud-to-brain/plaud_collector.py backfill
~/.gbrain/integrations/plaud-to-brain/run_fresh_sync.sh
```

The recurring wrapper reconciles through the Plaud source-bound GBrain owner client and prints only a bounded status line. Meeting content remains in private archives and GBrain.
