---
title: Native Plaud meeting ingestion for GBrain
date: 2026-08-03
status: active
owners:
  - Blake Messer
  - Hermes
reviewers:
  - independent read-only implementation reviewer
fallback_policy: Pause the Plaud sync job and leave the immutable local archive and existing canonical pages intact; no destructive source removal during rollback.
related:
  - adapters/granola/granola_collector.py
  - adapters/granola/run_fresh_sync.sh
  - scripts/reconcile_archive.py
  - scripts/install_runtime_links.py
  - https://docs.plaud.ai/plaud-mcp-cli/mcp
---

# TL;DR

Add Plaud as a first-class, source-qualified GBrain meeting source using Plaud's official structured local MCP transport rather than scraping human CLI tables. The integration will ingest the full raw timestamped transcript, all Plaud-generated notes, stable recording metadata, and current speaker labels; preserve immutable source revisions; deterministically render one canonical Markdown page per recording; and reconcile every changed page through the acknowledged single-owner boundary with private receipts. A recent-plus-rotating refresh policy will pick up later speaker-name changes even though Plaud does not expose a recording-level `updated_at` field.

# North Star

**Claim:** Any Plaud recording Blake can read in Plaud becomes a source-isolated, provenance-bearing GBrain meeting page whose transcript, notes, and speaker labels converge after later Plaud edits without duplicate canonical pages.

**In scope**

- Plaud recording catalog discovery through the official Plaud MCP package.
- Full `transaction` transcript segments with timestamps and source speaker labels.
- Plaud-generated `note_list` content, including summaries, topics, and action items when present.
- Stable recording metadata and a deterministic page identity based on Plaud recording ID.
- Immutable raw revisions plus a current normalized raw snapshot with expiring capability URLs redacted.
- Source-bound owner reconciliation, receipts, cron scheduling, freshness monitoring, bounded backfill, and live retrieval proof.

**Out of scope**

- Audio download or storage.
- Re-transcription, speaker inference, contact matching, or attendee claims not provided by Plaud.
- Replacing or modifying Granola ingestion.
- Installing Plaud as a general-purpose MCP connector in Hermes, Claude, or Codex.
- Plaud write-back, webhooks, or polling undocumented/private APIs.

**Definition of done**

1. A dedicated `plaud` source exists in the active GBrain owner and cannot overwrite another source.
2. A bounded backfill creates one canonical page per accessible Plaud recording with full transcript and all generated notes.
3. Running sync twice without a Plaud change produces no changed page or owner resubmission.
4. A fixture-level speaker-label edit creates a new immutable raw revision and updates the same page identity.
5. The enabled recurring job is non-overlapping, monitored, healthy, and source-scoped retrieval returns a live Plaud page with matching persistence receipt.

<!-- lilarch:block:requirements:start -->
# Requirements

| ID | Requirement | Authority | Acceptance evidence |
|---|---|---|---|
| R1 | Ingest every accessible recording using Plaud's stable recording ID and paginated catalog. | Blake: “full transcripts” and match Granola. | Pagination and all-recording fixture tests; bounded live catalog count reconciliation. |
| R2 | Persist the complete raw `transaction` transcript with start/end times, content, and Plaud speaker labels; do not substitute polished transcript for source truth. | Blake: “full transcripts.” | Multi-page transcript fixture test and live transcript segment/count check. |
| R3 | Persist all generated notes returned in `note_list`, not only the first summary; hydrate link-only note bodies without archiving the temporary capability URL. | Blake: match meeting-note ingestion and provenance. | Inline/multi-note and link-only fixture tests plus live page section check. |
| R4 | Re-fetch recent recordings every run and run a complete daily ID/content audit so later Plaud speaker-label/people edits converge despite the missing update cursor. | Blake: later people updates must also be pulled. | Selection-policy tests plus speaker-edit re-sync test. |
| R5 | Preserve immutable normalized raw revisions keyed by semantic source hash; exclude fetched time and expiring audio/data capability URLs from identity and redact them on disk. | GBrain provenance and secret-safety contract. | Hash/redaction/revision tests and private file-mode checks. |
| R6 | Render one deterministic Markdown page per recording ID with `source: plaud`, source ID, source hash, transcript count, note count, and speaker labels. | Native GBrain source/provenance model. | Golden rendering test and stable-path test across title/speaker edits. |
| R7 | Reconcile only through the source-bound owner client and verify canonical source, slug, hashes, brain identity, and private receipt. | Existing acknowledged-owner architecture. | Existing reconciler contract tests plus live receipt/get-page verification. |
| R8 | Use an exclusive lock, bounded failures, atomic writes, secret-free logs/state, a minimal telemetry-disabled MCP child environment, and a non-overlapping script-only cron monitored by the owner health watchdog. | Existing Granola/runtime operations contract. | Wrapper/environment tests, two-run smoke, cron status, health observe-only check. |

**Defaults**

- MCP executable is configurable and defaults to `plaud-mcp` on `PATH`.
- Recent window is 30 days; recent recordings are re-read every run.
- A complete catalog audit runs at least every 24 hours; between audits, new and last-30-day recordings are refreshed every scheduled run.
- The canonical page path is year plus recording date plus Plaud recording ID; titles live in content, so renames do not fork identity.
- Missing transcript or notes are represented explicitly and retried on later runs.

**Non-requirements**

- No semantic participant inference from calendar, email, contacts, or audio.
- No preservation of temporary audio URLs.
- No deletion of canonical pages when a recording disappears upstream without a separately authorized deletion policy.
<!-- lilarch:block:requirements:end -->

# Scope and Simplicity Contract

**Human-authorized outcome and anchors:** Blake explicitly requested a Plaud equivalent of the live Granola-to-GBrain flow, full transcripts, provenance, and subsequent people/speaker updates.

**Smallest sufficient solution:** one new Plaud adapter, its tests and runtime-link mapping, one source-bound owner client/config, one local script-only recurring job, and one health-monitor registry entry. Reuse the existing generic reconciler and owner client unchanged.

**Initial minimal convergence closure:** register Plaud in the existing runtime linker, sync-monitor default job list, and live owner-health source/job map. These are the only directly competing deployment/monitoring owner paths for the new source. No generic connector framework or Granola refactor is authorized.

**Scope-freeze boundary:** freeze before editing adapter or runtime code after the current architecture, target architecture, call sites, and phase audit are complete.

**Enough proof:** fixture tests for pagination/rendering/revisions/edits/idempotency; clean full repo suite; bounded live backfill; second-run idempotency; owner receipt/hash/source verification; one source-scoped retrieval; enabled cron and observe-only health pass.

**Do-not-build boundary:** no audio archive, private API client, AI reprocessing, participant enrichment, generalized connector framework, Granola refactor, or cross-agent Plaud installation.

**Accepted residual risk:** Plaud exposes no documented recording `updated_at` or webhook in the current personal-account surface, so changes to old recordings converge on the bounded rotating refresh interval rather than instantly.

<!-- utility_skill:block:research_grounding:start -->
# Research grounding

First-party Plaud documentation and the published package confirm `list_files`, `get_file`, `get_note`, and paginated `get_transcript`; `get_transcript` returns the raw `transaction` block with speaker labels and timestamps. The documented recording schema does not expose `updated_at`, attendees, or a change feed. The official local MCP package emits structured JSON and stores a separate OAuth token, while the human CLI only prints tables and formatted text. A live, content-free account probe found seven accessible recordings: six currently have transcripts and generated notes, and one has neither yet, so missing derivative content must remain retriable rather than fail the whole catalog.
<!-- utility_skill:block:research_grounding:end -->

<!-- utility_skill:block:external_research:start -->
# External research

Primary source: Plaud developer documentation and the published `@plaud-ai/mcp` package. Use only the documented MCP tools; do not call the underlying `/open/third-party` endpoints directly. The package's temporary audio/data URLs are capability-bearing and must not be persisted.
<!-- utility_skill:block:external_research:end -->

<!-- utility_skill:block:current_architecture:start -->
# Current architecture

Granola's collector reads its official API, writes private raw JSON plus deterministic Markdown under `brain/granola`, and merges prior non-empty transcript/notes when a transient fetch returns less data. Its `run_fresh_sync.sh` takes an exclusive lock, writes private logs, and invokes the generic `reconcile_archive.py` with Granola's source-bound owner credentials. The reconciler skips unchanged pages and verifies the returned source, slug, content hash, persistence hash, and GBrain identity before writing a private receipt. Runtime code is symlinked from this repo, and a script-only Hermes cron runs hourly at minute 35 through the shared timeout/state runner.

Plaud CLI 0.3.5 is installed and authenticated, but there is no Plaud source, adapter, owner client, runtime directory, cron, monitor state, or receipt tree. The existing CLI token file was created mode `0644`; the new MCP credential must be created under a `077` umask and locked to `0600`.
<!-- utility_skill:block:current_architecture:end -->

<!-- utility_skill:block:target_architecture:start -->
# Target architecture

Planned data flow:

`plaud-mcp stdio (official structured tools)` → `PlaudCollector` → `normalized current snapshot + immutable semantic revisions` → `deterministic plaud Markdown page` → `ArchiveReconciler` → `source-bound owner /ingest` → `canonical source=plaud page + private receipt`.

Deterministic code is the primary lever. No agent prompt or generative transformation is required or allowed in the ingestion path.

The collector will page through the complete catalog, hydrate selected recordings with `get_file` and every `get_transcript` page, and retain all `note_list` entries. Link-only note bodies are fetched in memory from Plaud's returned HTTPS capability URL, then the URL is redacted before persistence. The official MCP child receives only Plaud/runtime essentials and runs with its supported telemetry opt-out set. The collector will refresh new and last-30-day recordings every two hours and complete a full catalog audit at least daily. The semantic source hash excludes observation time and transient signed URLs but includes recording metadata, every transcript segment, every generated note, and speaker labels. A changed hash creates an immutable normalized raw revision and updates the same ID-derived Markdown path. One current snapshot and one state/summary file remain private and atomic. Missing transcript/notes are explicit, preserve prior non-empty content if a transient read regresses, and are retried later.
<!-- utility_skill:block:target_architecture:end -->

<!-- utility_skill:block:call_site_audit:start -->
# Call-site audit

| Surface | Action | Competing path checked | Decision |
|---|---|---|---|
| `adapters/plaud/plaud_collector.py` | Add MCP session, selection/state, normalization, revisions, and renderer. | Granola collector and generic archive reconciler. | New source-specific adapter only; leave generic owner code unchanged. |
| `adapters/plaud/run_fresh_sync.sh` | Add lock, private log, collector, and acknowledged reconcile call. | Granola/WhatsApp wrappers. | Reuse the established wrapper contract. |
| `adapters/plaud/README.md` and `adapters/plaud/tests/*` | Document supported boundary and prove behavior with synthetic data. | Existing adapter-local test layout. | Keep Plaud tests next to the adapter. |
| `scripts/install_runtime_links.py` | Add `plaud-to-brain` mapping. | Existing integration linker is the sole runtime-code installer. | Extend the existing map; no separate installer. |
| `scripts/plaud_fresh_sync_cron.sh` | Add shared timeout/state runner entry. | Live ad hoc Granola wrapper and tracked WhatsApp wrapper. | Track the Plaud scheduler wrapper in this repo. |
| `src/gbrain_ops/sync_monitor.py` | Add the scheduler's exact `plaud-fresh-sync` state key to default monitored jobs. | Existing default list and sync-runner state filename. | One-list convergence closure without an alias mismatch. |
| Private runtime | Install pinned official MCP binary, OAuth token, `runtime.env`, source/client credentials, and symlinks. | No existing Plaud runtime. | Create source-qualified private state only. |
| Hermes cron and live owner health script | Add Plaud schedule/source/job thresholds. | Existing cron and owner-health job map. | Extend existing operational owners; do not create a second watchdog. |

No call site requires a GBrain schema change, vendor-repo edit, owner API change, embedding change, or Granola modification.
<!-- utility_skill:block:call_site_audit:end -->

<!-- utility_skill:block:phase_plan:start -->
# Phase plan

1. **Adapter and deterministic contracts** — add `adapters/plaud/{plaud_collector.py,run_fresh_sync.sh,README.md,tests/*}`, `scripts/plaud_fresh_sync_cron.sh`, the runtime-link map entry, and the monitor default. Prove MCP pagination, transcript pagination, note preservation, URL redaction, stable page identity, immutable revisions, missing derivative handling, recent/daily-full selection, speaker-edit convergence, idempotency, locking, and executable wiring.
2. **Protected publication** — run the full suite and changed-file quality checks, obtain an independent read-only review, address high-confidence findings, rebase onto current `origin/main`, publish a PR through the required signoff gate, and merge before activating any live runtime path.
3. **Runtime activation, backfill, and acceptance** — update the durable main checkout; install and pin official `@plaud-ai/mcp` 0.3.7 as a local integration dependency without auto-configuring agent clients; authenticate its separate OAuth token under private permissions; deploy runtime links/config; register source `plaud` and its owner client; add the tracked script-only cron at a non-conflicting two-hour cadence; extend the existing owner-health map; run the seven-recording backfill; prove a second no-change sync plus source/hash/receipt/retrieval behavior; and verify no live symlink targets the disposable worktree.
<!-- utility_skill:block:phase_plan:end -->

<!-- lilarch:block:plan_audit:start -->
# Plan audit

**Authority and traceability:** R1–R8 map directly to Blake's requested Granola parity, full transcripts, native provenance, and subsequent people/speaker updates, plus the standing acknowledged-owner and secret-safety contracts.

**Complexity:** three implementation phases, one new adapter, and three narrow existing-owner registrations. No generalized connector framework, schema migration, or competing scheduler/watchdog is introduced.

**Runtime testability:** the MCP boundary is injectable and fully synthetic in tests; deterministic normalization/rendering/state functions can be exercised without Plaud credentials; live acceptance uses source-scoped probes and receipts without exposing meeting content.

**Abstraction quality:** Plaud-specific source semantics remain in the adapter. Existing generic reconciliation, receipt, timeout-runner, runtime-link, and health mechanisms remain the operational owners.

**Auditability and invariance:** recording ID fixes page identity; semantic source hash covers all durable source content; every changed source hash has one immutable private revision; signed URLs and fetch time cannot perturb identity; source-bound credentials prevent cross-source overwrite.

**Completeness and edge cases:** the plan covers catalog/transcript pagination, title changes, speaker renames, multiple notes, one currently unprocessed recording, transient derivative regression, auth failure, overlap, partial failure, long transcripts, signed URL redaction, no upstream deletions, and the missing update cursor.

**User acceptance:** source-scoped GBrain retrieval must return at least one live Plaud page whose title, transcript presence, note presence, current speaker labels, source hash, and private receipt agree with the archived source—without printing the meeting body into logs or review artifacts.

**Scope freeze:** active as of 2026-08-03 after direct repository/runtime mapping and first-party source inspection. Expansion beyond the surfaces in the call-site table requires a new explicit justification in this document before code changes.
<!-- lilarch:block:plan_audit:end -->

<!-- utility_skill:block:implementation_audit:start -->
# Implementation audit

## Phase 1 — Adapter and deterministic contracts

**Implemented:** official MCP stdio client boundary including the documented `{data: [...]}` catalog envelope and empty-transcript response; complete catalog and transcript pagination; recent plus daily-full selection; inline and HTTPS-linked note hydration; capability-URL redaction; a minimal telemetry-disabled child environment; private atomic current/revision/state writes; stable ID-derived page identity; all generated notes; full timestamped transcript and current Plaud speaker labels; transient non-empty derivative preservation; acknowledged-owner wrapper; shared scheduler wrapper; runtime-link and correctly named `plaud-fresh-sync` monitor registrations; adapter documentation and synthetic tests.

**Proof:** `PYTHONPATH=src python3 -m pytest -q adapters/plaud/tests` → 25 passed. Full local signoff → 124 passed, 1 skipped; Ruff, privacy scan, and Gitleaks passed. Both Bash surfaces pass `bash -n`. A real stdio startup handshake against official `@plaud-ai/mcp` 0.3.7 returned `server=plaud version=0.3.7`; authentication was correctly absent before deployment. Independent review probes identified and drove fixes for the official MCP catalog/empty-transcript response shapes, the exact monitor heartbeat key, runtime environment propagation plus MCP-child telemetry/environment containment, and linked-note redirect, connected-peer, and alternate numeric-IP SSRF defenses. Plaud adapter Python files pass `ruff format --check`; all touched Python files pass Ruff lint.

**Scope check:** no GBrain schema, vendor, owner API, embedding, Granola, audio, attendee inference, or generalized connector work was added. Phase 1 matches the frozen call-site table.
<!-- utility_skill:block:implementation_audit:end -->
