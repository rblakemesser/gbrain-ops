---
title: "GBrain - Single Owner Collector and Client Cutover - Architecture Plan"
date: 2026-07-12
status: active
implementation_status: complete
verification_status: passed
fallback_policy: forbidden
owners: [Blake Messer]
reviewers: [Arch Epic critic]
doc_type: architectural_change
related:
  - docs/EPIC_OPERATIONAL_PERSONAL_ASSISTANT_BRAIN_2026-07-11.md
  - docs/NORTH_STAR_PERSONAL_ASSISTANT_BRAIN_2026-07-11.md
---

# TL;DR

- **Outcome:** Gmail, Calendar, Messages, and Granola refresh their deterministic archives and receive source-qualified canonical persistence acknowledgments from one supervised loopback GBrain owner; readers use the same owner.
- **Problem:** Messages now uses the owner, but Gmail, Calendar, and Granola wrappers still stop after collection and their existing schedules previously timed out or contended on direct PGLite ownership.
- **Approach:** Apply the verified Phase 01 owner-client/receipt contract to each source, preserve source-specific archive roots and date semantics, install source-bound private clients, and provide one read-only owner credential for Hermes.
- **Non-negotiables:** no direct PGLite opener while the owner runs; all freshness claims require canonical receipts; private credentials stay outside Git; Telegram remains waived.

<!-- arch_skill:block:planning_passes:start -->
<!--
arch_skill:planning_passes
deep_dive_pass_1: done 2026-07-12
external_research_grounding: not required — Phase 01 live proof and current call-site inventory settle the implementation
deep_dive_pass_2: done 2026-07-12
recommended_flow: deep dive -> external research grounding -> deep dive again -> phase plan -> implement
-->
<!-- arch_skill:block:planning_passes:end -->

<!-- arch_skill:block:auto_plan_receipts:start -->
{
  "version": 1,
  "digest": "sha256:fce5020287f4e6bf0a5d7df450d23818a32df04a5bd91c72ac67695277b1eda2",
  "receipts": [
    {
      "stage": "research",
      "command": "research",
      "status": "complete",
      "started_at": "2026-07-12T00:54:05Z",
      "command_ref_hash": "sha256:5ad5dc9efcb3c7d0d42e1d9014e3ee66fd24b8d2f1c85eef2c5ee96543e05c96",
      "doc_hash_before": "sha256:991f15c4876b5908c8113905ddbc3d9a334e930b98a28edefe6c2d148f4babef",
      "completed_at": "2026-07-12T00:57:22Z",
      "doc_hash_after": "sha256:e905a1d76eb2e4541f6e32b60497349e37904a9c51558acf419390b698521195"
    },
    {
      "stage": "deep-dive-pass-1",
      "command": "deep-dive",
      "status": "complete",
      "started_at": "2026-07-12T00:57:22Z",
      "command_ref_hash": "sha256:a70fc06739f4a02466585892607a7dccc5476123c3c4af9c01fad587284ae649",
      "doc_hash_before": "sha256:e905a1d76eb2e4541f6e32b60497349e37904a9c51558acf419390b698521195",
      "completed_at": "2026-07-12T00:58:56Z",
      "doc_hash_after": "sha256:83a75ccbcc70b694daabdaf524673c17b486ce7709da25b096839383fa05ccee"
    },
    {
      "stage": "deep-dive-pass-2",
      "command": "deep-dive",
      "status": "complete",
      "started_at": "2026-07-12T00:59:03Z",
      "command_ref_hash": "sha256:a70fc06739f4a02466585892607a7dccc5476123c3c4af9c01fad587284ae649",
      "doc_hash_before": "sha256:83a75ccbcc70b694daabdaf524673c17b486ce7709da25b096839383fa05ccee",
      "completed_at": "2026-07-12T00:59:22Z",
      "doc_hash_after": "sha256:2f0481a1fb02ca2994c615da48bf62d3473747e9922fabc6c86739deb3b4bc65"
    },
    {
      "stage": "phase-plan",
      "command": "phase-plan",
      "status": "complete",
      "started_at": "2026-07-12T00:59:22Z",
      "command_ref_hash": "sha256:1ce4687beab44819933a8a404a02b8e1345823a7a996f7d651f3dd25a0c54aa3",
      "doc_hash_before": "sha256:2f0481a1fb02ca2994c615da48bf62d3473747e9922fabc6c86739deb3b4bc65",
      "completed_at": "2026-07-12T00:59:47Z",
      "doc_hash_after": "sha256:5400e1cfa430f6a30543de807506fc979d2d42cab538f3f871f5053fff0a3080"
    },
    {
      "stage": "consistency-pass",
      "command": "consistency-pass",
      "status": "complete",
      "started_at": "2026-07-12T00:59:47Z",
      "command_ref_hash": "sha256:7489e588432f0d742aeb3fc2f19649224b462dc9cd95ad442675037b1c616137",
      "doc_hash_before": "sha256:5400e1cfa430f6a30543de807506fc979d2d42cab538f3f871f5053fff0a3080",
      "completed_at": "2026-07-12T01:00:04Z",
      "doc_hash_after": "sha256:7af0563d83c9daaddfa8a82c1198f568faf3a0f617c38054441007aea6049119"
    }
  ]
}
<!-- arch_skill:block:auto_plan_receipts:end -->

# 0) North Star

## 0.1 Definition of Done

- The four enabled collectors exit 0 only after changed canonical pages are acknowledged.
- Every enabled source has a source-bound writer client and private receipts tied to the active brain identity.
- The only live PGLite holder is `com.gbrain.owner`; restart preserves brain identity and retrieval.
- A stable read credential and MCP endpoint are prepared for Hermes without exposing secrets.

## 0.2 Scope

In scope: wrapper changes, source-specific bounded selection, private client provisioning, runtime links, live sync verification, restart/holder checks, Hermes MCP registration. Out of scope: Telegram and native PostgreSQL migration.

# 1) Problem and Constraints

Gmail produces `brain/email/**/YYYY-MM-DD.md` and promoted aggregate pages. Calendar rebuilds `brain/daily/calendar/YYYY/YYYY-MM-DD.md`, including future days, and promoted aggregates. Granola writes `brain/granola/YYYY/YYYY-MM-DD--slug.md`. Their wrappers currently collect only. The owner is already live at loopback port 3131 and Phase 01 proves acknowledged writes and hybrid reads.

# 2) Repository and Runtime Grounding

- `adapters/{gmail,calendar,granola}/run_fresh_sync.sh`: collector-only wrappers.
- `scripts/install_runtime_links.py`: private deployment map.
- `scripts/provision_owner_client.py`: source-bound client creation without secret output.
- `scripts/reconcile_archive.py`: generic archive-to-owner reconciler.
- Private integrations under `~/.gbrain/integrations/*-to-brain` and receipts under `~/.gbrain/owner/receipts`.
- `com.gbrain.owner`: sole live PGLite owner.

<!-- arch_skill:block:research_grounding:start -->
# 3) Research Grounding

Phase 01 proves the local design: authenticated source binding, two hash layers, canonical readback, private receipts, and owner-local embedding. The remaining work is call-site migration, not a new storage architecture. Calendar's future-date pages require a lower-bound-only date filter; Granola date prefixes require parsing the first ISO date token.
<!-- arch_skill:block:research_grounding:end -->

<!-- arch_skill:block:current_architecture:start -->
# 4) Current Architecture

Messages follows `collector -> archive -> owner persisted acknowledgment -> receipt`. Gmail, Calendar, and Granola stop at `collector -> archive`. Existing cron success therefore cannot establish brain freshness. Hermes has no stable owner read credential registered in the active profile.
<!-- arch_skill:block:current_architecture:end -->

<!-- arch_skill:block:target_architecture:start -->
# 5) Target Architecture

Each source wrapper runs its collector and transformations, then invokes the same reconciler with:

| Source | Root | Date-bounded pattern | Aggregate pattern |
|---|---|---|---|
| Gmail | `brain` | `email/**/*.md` | `promoted/**/*.md` |
| Calendar | `brain` | `daily/calendar/**/*.md` | `promoted/**/*.md` |
| Messages | `brain/daily` | `messages/**/*.md` | none |
| Granola | `brain` | `granola/**/*.md` | none |

Date selection uses the ISO date prefix in each filename and a lower bound, preserving future Calendar pages. Receipts suppress unchanged network writes even when aggregate renderers rewrite files. Hermes reads at `http://127.0.0.1:3131/mcp` with a private read token.
<!-- arch_skill:block:target_architecture:end -->

# 6) System-Wide Impact

- All collector schedules become archive producers plus owner clients.
- The owner remains the only database lifecycle boundary.
- Freshness is measured by receipt brain identity and hashes, not cron exit alone.
- A source outage leaves the prior canonical page and receipt intact.

<!-- arch_skill:block:call_site_audit:start -->
## 6.1 Call-Site Audit

| Call site | Current behavior | Required destination | Proof |
|---|---|---|---|
| Gmail fresh wrapper | collect/classify/promote only | owner reconciler for daily and promoted pages | source-bound receipts + search |
| Calendar fresh wrapper | collect/classify/promote only | owner reconciler for daily/future and promoted pages | source-bound receipts + search |
| Messages fresh wrapper | acknowledged owner path | retain date-bounded owner path | idempotent replay |
| Granola fresh wrapper | collect only | owner reconciler for dated meeting pages | source-bound receipts + search |
| Hermes GBrain client | direct CLI/unspecified | owner MCP endpoint | restart-stable read |
| GBrain launchd | one owner active | unchanged sole holder | holder/listener audit |
<!-- arch_skill:block:call_site_audit:end -->

<!-- arch_skill:block:phase_plan:start -->
# 7) Phased Implementation Plan

## Phase 1 — Generalize bounded candidate selection

- [x] Parse ISO dates from the first ten filename characters.
- [x] Preserve lower-bound-only behavior for future Calendar pages.
- [x] Add tests for daily pages, Granola names, and non-dated aggregate exclusion.

## Phase 2 — Migrate wrappers and deployment links

- [x] Add reconciler calls to Gmail, Calendar, and Granola wrappers.
- [x] Reconcile date-bounded pages and receipt-skipped promoted aggregates.
- [x] Link all three wrappers via the runtime installer.
- [x] Add shell syntax and contract tests.

## Phase 3 — Provision and cut over live sources

- [x] Provision source-bound clients privately for Gmail, Calendar, and Granola.
- [x] Install private runtime env files.
- [x] Deploy runtime links.
- [x] Run each source sync and verify zero failed acknowledgments and current brain identity.

## Phase 4 — Prepare owner-only reads and restart proof

- [x] Create a non-expiring local read token without printing it.
- [x] Register the owner MCP endpoint in Hermes using environment indirection.
- [x] Restart `com.gbrain.owner`; verify same brain identity, all source searches, and exactly one holder.
- [x] Retire or document remaining direct-open schedules.
<!-- arch_skill:block:phase_plan:end -->

# 8) Verification Strategy

- Full Python suite, shell syntax, privacy scan, Gitleaks.
- Per-source live wrapper exit status and receipt counts.
- Authenticated search for one known non-sensitive marker per source.
- Owner restart, health, stable brain identity, one listener, and one PGLite holder.
- No credential values, message bodies, email bodies, calendar details, or meeting content in public artifacts.

# 9) Deployment and Rollback

Deploy one source at a time. Existing archives are immutable recovery inputs. On source failure, restore its backed-up wrapper and leave its schedule failed/paused rather than opening PGLite directly. On owner failure, boot out launchd, restore the prior promoted runtime/wrapper activation receipt, and keep the sealed pre-cutover brain untouched.

# 10) Decision Log

## Planning Receipts

- Research stage reviewed 2026-07-12: current wrappers, archive layouts, live owner, and Phase 01 receipts establish the implementation boundary.
- Deep-dive pass 1 reviewed 2026-07-12: source-specific archive/date semantics and existing direct-open failure modes are fully mapped.
- Deep-dive pass 2 reviewed 2026-07-12: destination ownership, credential boundaries, rollback, and each call-site migration are decision-complete.
- Phase plan reviewed 2026-07-12: bounded selection, wrapper migration, private cutover, and restart proof are ordered with explicit gates.
- Consistency pass reviewed 2026-07-12: North Star, target architecture, call sites, implementation phases, verification, and rollback agree on one owner.

<!-- arch_skill:block:consistency_pass:start -->
## Consistency Pass — 2026-07-12

- Destination, call sites, source semantics, privacy, verification, and rollback all preserve the one-owner contract.
- Decision-complete: yes
- Unresolved decisions: none
- Unauthorized scope cuts: none
- Decision: proceed to implement? yes
<!-- arch_skill:block:consistency_pass:end -->

## 2026-07-12 - Reuse Phase 01 contract for all enabled sources

- **Decision:** Apply one generic reconciler and source-bound credentials; source wrappers only choose roots, patterns, and windows.
- **Consequences:** No source-specific database client or direct CLI import remains.

# 11) Implementation Log

- Implemented ISO-prefix date filtering, future-safe Calendar selection, list-valued MCP payloads, privacy-safe summaries, and private collector logs.
- Migrated Messages, Gmail, Calendar, and Granola wrappers plus runtime links to source-bound owner reconciliation.
- Live rerun results: Messages 9/9 unchanged; Gmail 5 daily plus 552 promoted with zero failures; Calendar 65 daily/future plus 422 promoted with zero failures; Granola 27 pages with zero failures.
- Installed a Hermes owner MCP endpoint with 26 read-only selected tools, environment-indirected authentication, and successful 94-tool upstream discovery/connection test.
- Restart proof preserved brain identity `d3d13cc8-e6db-4e9c-b608-4443b49cb6bf`, 10,805 pages, 30,327 chunks, exactly one holder, and successful post-restart Messages retrieval.
- Updated and manually executed all four collector crons successfully; replaced the stale heartbeat monitor with a deterministic silent owner/receipt watchdog whose live run exited 0.
