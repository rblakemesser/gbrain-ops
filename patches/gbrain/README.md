# GBrain carried patches

The manifest pins a public upstream revision and applies patches with `git am --3way` into a disposable worktree. The primary vendor checkout remains clean.

## Ordered series

1. `0001-feat-ingestion-add-supervised-single-owner-source-se.patch`
   - One coherent implementation patch because several files contain shared contract and lifecycle hunks.
   - Candidate upstream review slices, in dependency order:
     1. exact Bun 1.3.13 toolchain pin and version-sync check;
     2. parallel verifier exit-code preservation;
     3. canonical single PGLite constructor/open gate;
     4. persistent brain identity across schema, migrations, engines, reset, and evaluation;
     5. persistence-acknowledged, source-qualified ingestion with queue backpressure and secret-free errors;
     6. configured single-owner autonomous ingestion, command/archive adapters, HTTP lifecycle, and health reporting.
2. `0002-fix-pglite-surface-lock-filesystem-errors-immediately.patch`
   - Independent lock correctness fix. Permission, read-only-filesystem, and invalid-path errors fail immediately with the lock path and filesystem code instead of becoming a misleading contention timeout.

## Verification

```bash
gbrain-ops vendor-promote \
  --vendor "$GBRAIN_VENDOR_CHECKOUT" \
  --manifest patches/gbrain/manifest.json \
  --output "$GBRAIN_VENDOR_RUNTIME"
cd "$GBRAIN_VENDOR_RUNTIME"
bun install --ignore-scripts --frozen-lockfile
bun run typecheck
bun test test/pglite-lock.test.ts \
  test/ingestion/service.test.ts \
  test/ingestion/sources/command-archive.test.ts \
  test/serve-http-owner-lifecycle.test.ts
```

The first patch may be mechanically split when preparing upstream pull requests. Do not reorder or partially apply those candidate slices without retesting their shared contracts. Neither patch contains private runtime material.
