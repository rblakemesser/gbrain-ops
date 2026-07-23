# WhatsApp → GBrain adapter

This adapter reads the signed-in native WhatsApp for macOS local Core Data cache and renders a deterministic, private GBrain archive. It is intentionally a **local-cache source**: it does not claim complete WhatsApp server history.

## Source and scope

- Default source: `$HOME/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite`
- Override: `GBRAIN_OPS_WHATSAPP_DB`
- Included: every chat represented in the local cache, including archived, hidden, group, status, and broadcast sessions.
- Media: metadata only. The collector does not download media or persist media encryption keys, receipt blobs, remote media URLs, or full local paths.

## Identity and lifecycle

- Stable key: `(account fingerprint, chat fingerprint, stanza ID)`.
- Raw observations are private JSONL and retain prior payloads.
- A recent overlap rereads the configured day window and appends a lifecycle `edit` only when the stable payload changes.
- Core Data row IDs/versions are provenance fields but are excluded from payload identity.
- Current local schema does not expose authoritative reaction or deletion events. The collector preserves opaque source type/status values and never converts cache disappearance into a confirmed deletion.
- A database/account identity change or required-schema drift fails closed before archive mutation.

## Privacy and deterministic rendering

- Runtime directories are `0700`; files are `0600`.
- Raw JSONL retains source identity for provenance. Searchable Markdown uses hashed chat paths and display names instead of JIDs/phone-number paths.
- Likely OTP/security-code messages remain in private raw observations but are redacted from GBrain Markdown.
- Untrusted message text is rendered as quoted one-line evidence with Markdown/frontmatter/comment/fence controls escaped.
- Pages are partitioned by stable chat and UTC day, then split below the configured byte limit.

## Operations

```bash
# Structural check only; writes no archive.
python3 adapters/whatsapp/whatsapp_collector.py validate

# Initial local-cache backfill.
python3 adapters/whatsapp/whatsapp_collector.py backfill

# Bounded overlap refresh.
python3 adapters/whatsapp/whatsapp_collector.py recent --days 7
```

Production uses `run_fresh_sync.sh`: collect, render, submit through the source-bound loopback owner, verify canonical readback, and write private persistence receipts. `scripts/whatsapp_fresh_sync_cron.sh` wraps that command in the shared process-group timeout/heartbeat runner.

## Acceptance boundaries

A successful deployment requires: schema validation, full local-cache backfill equality, zero duplicate stable identities, acknowledged owner receipts, canonical source/slug/hash readback, source-qualified lexical retrieval, an immediate all-unchanged replay, scheduled-run success, and monitor coverage. Counts or rendered files alone are not acceptance.
