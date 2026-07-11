# Implementation Log

Active scope: vendor-boundary migration and open-source-ready operations repository
Last updated: 2026-07-11T15:15:18Z

## Resume Snapshot

- GBrain's primary checkout is reset to its upstream commit with a clean worktree.
- Generic product changes are preserved as two ordered format patches and on a local carried branch.
- A reproducible promoted worktree was built from the pinned upstream commit and patch manifest.
- Collector and monitor source files in the existing runtime are symlinked to this repository; data, state, logs, credentials, sessions, and personal configuration remain external.
- A lossless pre-migration snapshot and local migration backup exist outside both repositories.

## Scope Ledger

| Item | Status | Code/proof |
| --- | --- | --- |
| Externalize collector/control-plane code | Complete | adapters, validated config/contracts, deterministic inventories, recovery readiness, service rendering, and monitor/runner |
| Keep personal/runtime material outside Git | Complete | `.gitignore`, privacy scanner, CI gate |
| Preserve generic GBrain changes | Complete | `patches/gbrain/manifest.json` and ordered patches |
| Reproduce promoted vendor release | Complete | `gbrain-ops vendor-promote`; clean detached worktree |
| Fix misleading read-only lock timeout | Complete | second carried patch; focused lock tests |
| Normalize vendor checkout | Complete | upstream HEAD equals `origin/master`; clean status |
| Publish private remote | Complete | private GitHub remote on `main`; remote commit verified |
| Public-release readiness | Complete | tracked privacy scan, built-wheel scan, and full-history Gitleaks scan passed; visibility intentionally remains private |
| Production data/service cutover | Out of scope for repository migration; remains gated by live-data acceptance receipts |

## Proof Freshness

| Proof | Result | Rerun trigger |
| --- | --- | --- |
| Python suite | 36 passed | adapter/control-plane change |
| Repository privacy scan | passed | any tracked-file change |
| Promoted GBrain TypeScript typecheck | passed | patch/base change |
| Promoted focused GBrain suite | 88 passed | patch/base change |
| Carried-branch extended suite | 138 passed | GBrain source change |
| Runtime link smoke | 17 source links, wrapper syntax and Python compilation passed | installer/runtime-link change |
| Git history secret scan | Gitleaks 8.30.1 full-history scan: no leaks | any history rewrite or new commit |
| Hosted CI | test/privacy and vendor-compat workflows required | any pushed commit |

## Late independent-audit reconciliation

Three read-only audits completed after the first migration report. Their findings were reconciled before closing the work:

- The three private architecture/worklog files remained excluded rather than being copied verbatim.
- Recovery receipts, inventories, databases, source archives, salvage material, and logs remain external.
- Sync execution no longer uses `shell=True`; commands are parsed to argv and process-group cleanup is tested.
- Configuration and five control-plane contract schemas are validated and included in the built wheel.
- Deterministic source inventories and lossless recovery-readiness checks are implemented with synthetic tests.
- Gitleaks was added to hosted CI, and vendor compatibility now rebuilds the patch series from the pinned upstream revision.

The product implementation is currently carried as one coherent ingestion patch plus one independent lock-error patch. The ingestion patch contains six documented upstream candidate slices; splitting it is an upstream-review optimization, not a reproducibility or privacy dependency.

## Side Doors and Deletes

- Planning documents and private operational history were removed from the vendor checkout after a verified external snapshot.
- A prior unrelated local content commit was removed from `master` and retained only on an archive branch.
- Existing runtime data directories were not moved, copied into Git, or deleted.
- The live brain pointer and sealed rollback database were not changed during this repository migration.
