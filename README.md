# gbrain-ops

`gbrain-ops` is an operations control plane around an **unmodified vendor checkout** of [GBrain](https://github.com/garrytan/gbrain). It owns collector adapters, deterministic archives and receipts, runtime promotion, carried-patch application, one-owner supervision, health/watchdog behavior, recovery tooling, and multi-client acceptance.

## Boundary

- `gbrain` remains a clean vendor checkout.
- Generic product gaps are carried as explicit `git format-patch` files under `patches/gbrain/` and are suitable for upstream PRs.
- Personal data, credentials, source inventories, database files, runtime receipts, and machine-specific configuration are external state and are ignored.
- Collectors write deterministic archives only. One supervised GBrain HTTP+ingestion owner is the sole PGLite opener.

## North Star

Every enabled personal channel is complete through an evidenced watermark, represented once per source-qualified canonical item, searchable from one healthy brain, self-healing within bounded policy, and independently reachable by multiple MCP clients. New channels use one adapter contract and one installed configuration entry.

## Open-source posture

The repository is designed to be public. `python -m gbrain_ops.privacy_scan .` fails on private absolute paths, likely credentials, email addresses outside synthetic fixtures, phone numbers, and tracked runtime/data directories. Real configuration belongs outside the checkout.

## Implemented control-plane contracts

- Declarative TOML configuration expands environment references and rejects unknown or unsafe fields.
- Collector, source-inventory, archive-manifest, and recovery-receipt JSON schemas ship in the Python wheel.
- Source inventories use relative paths and deterministic SHA-256 digests.
- Recovery readiness blocks on missing source inventories or import/replay inequality.
- Sync jobs execute argv without a shell and terminate their full process group on timeout.
- launchd service definitions are rendered with `ProgramArguments`, never shell command strings.
- The carried vendor patch series is recreated and tested from its pinned upstream commit in CI.

The repository migration is complete, but promotion of a replacement production database remains a separate, receipt-gated operation. Repository tooling never bypasses a missing-source completeness blocker.

## Commands

```bash
gbrain-ops config-check config/operator.toml
gbrain-ops inventory --source-id mail --root "$GBRAIN_GMAIL_ARCHIVE" --output "$GBRAIN_OPS_STATE_ROOT/inventories/mail.json"
gbrain-ops recovery-readiness \
  --inventory "$GBRAIN_OPS_STATE_ROOT/inventories/mail.json" \
  --required-source mail --imported 10 --replayed 10
gbrain-ops vendor-promote --vendor "$GBRAIN_VENDOR_CHECKOUT" \
  --manifest patches/gbrain/manifest.json --output "$GBRAIN_VENDOR_RUNTIME"
gbrain-ops activate-runtime --runtime "$GBRAIN_VENDOR_RUNTIME" --bun "$GBRAIN_BUN" \
  --wrapper "$GBRAIN_WRAPPER" --receipt "$GBRAIN_OPS_STATE_ROOT/active-vendor.json"
gbrain-ops privacy-check .
```

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,google,telegram]'
.venv/bin/pytest -q
.venv/bin/python -m gbrain_ops.privacy_scan .
```

See `docs/ARCHITECTURE.md` and `config/example.toml`.
