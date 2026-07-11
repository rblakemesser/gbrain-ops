# Architecture

## Ownership

`gbrain-ops` owns deployment and lifecycle; GBrain owns storage, indexing, retrieval, and MCP semantics. The vendor checkout is never used as an operational state directory and never contains private configuration.

```text
collectors -> deterministic archives + receipts -> one GBrain owner -> HTTP MCP clients
                 |                                  |
                 +-> DB-free watchdog               +-> one serialized embedding loop
```

## Tracked/public

- generic collector and rendering code
- synthetic fixtures and conformance tests
- configuration schema and examples
- service/watchdog/recovery code
- vendor patch manifests and patches
- generic architecture and runbooks

## External/private

- OAuth/MTProto/API credentials and sessions
- account IDs, addresses, peer IDs, calendar IDs, and personal correction maps
- raw API responses, archives, manifests, watermarks, receipts, logs, databases, and recovery bundles
- launchd environment files and client bearer-token files

## Vendor patches

Patches are linear commits based on a recorded upstream commit. `scripts/vendor_sync.py` fetches upstream, creates a disposable worktree, applies the manifest in order, and verifies that the resulting worktree is clean. Runtime releases are built from that pinned result, never from the mutable vendor checkout.
