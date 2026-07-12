---
title: "GBrain - Acknowledged Single-Owner Ingestion - Architecture Plan"
date: 2026-07-11
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

- **Outcome:** A collector-rendered page is persisted into the active source-qualified brain through one owner and read back with a matching content hash before the collector advances its receipt.
- **Problem:** Current fresh-sync wrappers stop after rendering archives, so active canonical pages remain stale; direct importers and readers also contend for PGLite ownership.
- **Approach:** Reuse GBrain's loopback HTTP owner and authenticated `/ingest` plus job/page read APIs, add a small ops client that submits rendered markdown and waits for persistence proof, and keep collectors as archive-only producers.
- **Plan:** Prove Messages end to end first, then establish the reusable reconciliation contract that later epic phases apply to every enabled source.
- **Non-negotiables:** one PGLite holder; source binding comes from authenticated client registration; content hash and source/slug must match on readback; no checkpoint advancement on queue admission alone; no credential or private corpus data in Git.

<!-- arch_skill:block:planning_passes:start -->
<!--
arch_skill:planning_passes
deep_dive_pass_1: done 2026-07-11
external_research_grounding: not required — current product code and the measured local oracle settle the first slice
deep_dive_pass_2: done 2026-07-11
recommended_flow: deep dive -> external research grounding -> deep dive again -> phase plan -> implement
note: This block tracks stage order only. It never overrides readiness blockers caused by unresolved decisions.
-->
<!-- arch_skill:block:planning_passes:end -->

<!-- arch_skill:block:auto_plan_receipts:start -->
{
  "version": 1,
  "digest": "sha256:e1e2862287bfff6c04defef91de3d89188442a1ad2f18cd54fddd593183a7979",
  "receipts": [
    {
      "stage": "research",
      "command": "research",
      "status": "complete",
      "started_at": "2026-07-12T00:05:05Z",
      "command_ref_hash": "sha256:5ad5dc9efcb3c7d0d42e1d9014e3ee66fd24b8d2f1c85eef2c5ee96543e05c96",
      "doc_hash_before": "sha256:126574b4188d30321924e1038ad2bd7602697dfbf7420eb3209c16086c85956a",
      "completed_at": "2026-07-12T00:05:41Z",
      "doc_hash_after": "sha256:760459a08afd89a6f1a4400221701297535d446489cff071236ea99f331abbe3"
    },
    {
      "stage": "deep-dive-pass-1",
      "command": "deep-dive",
      "status": "complete",
      "started_at": "2026-07-12T00:05:41Z",
      "command_ref_hash": "sha256:a70fc06739f4a02466585892607a7dccc5476123c3c4af9c01fad587284ae649",
      "doc_hash_before": "sha256:760459a08afd89a6f1a4400221701297535d446489cff071236ea99f331abbe3",
      "completed_at": "2026-07-12T00:06:39Z",
      "doc_hash_after": "sha256:4b5a3ee144aa0420d177f22743b45204035bd9ec1f9c92c3099a724287e17819"
    },
    {
      "stage": "deep-dive-pass-2",
      "command": "deep-dive",
      "status": "complete",
      "started_at": "2026-07-12T00:06:39Z",
      "command_ref_hash": "sha256:a70fc06739f4a02466585892607a7dccc5476123c3c4af9c01fad587284ae649",
      "doc_hash_before": "sha256:4b5a3ee144aa0420d177f22743b45204035bd9ec1f9c92c3099a724287e17819",
      "completed_at": "2026-07-12T00:07:02Z",
      "doc_hash_after": "sha256:ffebcf4bafc474370df0f7c3209380032e6631a9943cd8ebc7c321d6ad453d12"
    },
    {
      "stage": "phase-plan",
      "command": "phase-plan",
      "status": "complete",
      "started_at": "2026-07-12T00:07:02Z",
      "command_ref_hash": "sha256:1ce4687beab44819933a8a404a02b8e1345823a7a996f7d651f3dd25a0c54aa3",
      "doc_hash_before": "sha256:ffebcf4bafc474370df0f7c3209380032e6631a9943cd8ebc7c321d6ad453d12",
      "completed_at": "2026-07-12T00:08:00Z",
      "doc_hash_after": "sha256:c1b605c3fcaee0b2acc6e050ec22469f069a4a9aef542b71cb54d818377ce586"
    },
    {
      "stage": "consistency-pass",
      "command": "consistency-pass",
      "status": "complete",
      "started_at": "2026-07-12T00:08:01Z",
      "command_ref_hash": "sha256:7489e588432f0d742aeb3fc2f19649224b462dc9cd95ad442675037b1c616137",
      "doc_hash_before": "sha256:c1b605c3fcaee0b2acc6e050ec22469f069a4a9aef542b71cb54d818377ce586",
      "completed_at": "2026-07-12T00:08:28Z",
      "doc_hash_after": "sha256:32b1c6b82e4aad9cce985c7c7f794e84c518179da0c552a3bda817fffa5d6cc6"
    }
  ]
}
<!-- arch_skill:block:auto_plan_receipts:end -->

# 0) Holistic North Star

## 0.1 The claim (falsifiable)

With the loopback owner running, a changed Messages archive page can be submitted, persisted, embedded or explicitly queued for embedding, and retrieved under the same source-qualified slug with exact normalized content-hash equality while concurrent reads succeed and `lsof` reports exactly one PGLite-holder PID.

## 0.2 In scope

- The owner-local write contract and source-qualified persistence receipt.
- A reusable ops client for `/ingest`, OAuth client-credentials token acquisition, job completion polling, canonical page readback, and receipt writing.
- Messages fresh sync as the first real archive-to-canonical adopter.
- Private runtime configuration and owner-only credential files outside Git.
- Supervised loopback owner lifecycle sufficient for the first slice.
- Architectural convergence needed to remove direct PGLite opens from this slice.

### Epic Requirement Coverage

- **Owned here:** acknowledged archive-to-canonical ingestion; first single-owner runtime; Messages proving slice; source/slug/hash receipt; concurrent read/write owner proof.
- **Assigned to Phase 02:** Gmail, Calendar, and Granola adoption; all schedule migration; Hermes/Codex/Claude client cutover; durable restart-wide exactly-one-owner operations.
- **Assigned to Phase 03:** relationship aliases, event-time filtering, lexical/vector fusion, last-similar retrieval, assistant evidence packet.
- **Assigned to Phase 04:** full evaluation corpus, blue/green generation hardening, rollback drill, telemetry cleanup, final promotion and docs retirement.
- **Previously satisfied:** sealed pre-cutover rollback store and isolated keyword oracle evidence.

## 0.3 Out of scope

- Telegram ingestion, which remains explicitly waived.
- Native PostgreSQL migration; it remains an option only if the single-owner PGLite implementation fails measured gates.
- A second embedded LLM answer service; the assistant composes from evidence in the first operational slice.
- New binary attachment ingestion.

Compatibility posture: clean cutover for Messages ingestion from collector-only/direct-open behavior to owner-submitted acknowledged writes. There is no permanent dual path.

## 0.4 Definition of done (acceptance evidence)

- Unit tests prove token acquisition, submission headers, job terminal-state handling, timeouts, readback mismatch failure, and owner-only receipt writes without leaking credentials.
- A live Messages page update produces an accepted job, terminal persistence success, matching source/slug/content hash, and durable receipt.
- Search/get calls remain responsive while the owner persists the update.
- Process and `lsof` checks show one listener and exactly one PGLite holder.
- Restart preserves brain identity and resumes reads without direct-opener overlap.

## 0.5 Key invariants (fix immediately if violated)

- One logical and physical PGLite owner.
- Queue admission is not persistence acknowledgment.
- Source identity is authenticated server-side; caller-supplied source headers may only echo it.
- Checkpoint/receipt movement occurs only after source/slug/hash-matching readback.
- No fallback to direct import while the owner is running.
- The sealed rollback is never opened or mutated.
- Credentials, private paths, receipts, and corpus content stay outside public Git.

# 1) Key Design Considerations (what matters most)

## 1.1 Priorities (ranked)

1. Correct persistence and provenance.
2. Continuous read availability through one owner.
3. Idempotent replay and fail-loud mismatch handling.
4. Small operational surface built on existing GBrain APIs.
5. Bounded latency and recoverable retries.

## 1.2 Constraints

- PGLite direct readers cannot coexist reliably with one holder under the measured topology.
- Current collectors already render deterministic markdown; raw collection should not move into the database owner.
- `/ingest` returns HTTP 202 and therefore needs job polling plus readback before acknowledgment.
- Writer credentials must be source-bound and owner-only.
- Large embedding work can block or outlive cooperative cancellation; this slice treats persistence acknowledgment separately from embedding completion.

## 1.3 Architectural principles (rules we will enforce)

- Reuse `gbrain serve --http` as the sole engine owner.
- Reuse `/ingest`, `get_job`, `get_page`, `get_brain_identity`, and `search` rather than introducing a second database protocol.
- Keep archive generation deterministic and independently inspectable.
- Make every adapter advance only from persisted evidence.
- Fail loud on owner unavailability, terminal job failure, source mismatch, slug mismatch, or content-hash mismatch.

## 1.4 Known tradeoffs (explicit)

- OAuth client credentials add setup work but provide server-enforced write-source binding and independent revocation.
- Polling the persisted job is more expensive than accepting 202, but is required for truth.
- PGLite remains the backend for the first slice because ownership, not isolated FTS correctness, is the demonstrated defect.

# 2) Problem Statement (existing architecture + why change)

## 2.1 What exists today

Each external collector writes deterministic markdown under its integration root. The deployed fresh-sync wrappers call only the collector and print `status=collected`. The active GBrain database is a promoted PGLite runtime with source-qualified pages and complete embeddings as of the prior cutover, but it receives no new collector pages from those wrappers. CLI reads and writes each open the embedded store independently.

## 2.2 What’s broken / missing (concrete)

- Messages rendered 76 messages for July 11 while the active page still contained 11.
- `adapters/messages/run_fresh_sync.sh` has no import, ingestion, or acknowledgment step.
- The same collector-only shape exists for Gmail, Calendar, and Granola wrappers.
- A direct `gbrain capture` can update the page but writes a duplicate nested archive path because write-through uses the source root plus canonical slug.
- Direct readers time out while another process owns PGLite.
- Existing source sync metadata does not prove a collector page reached the active store.

## 2.3 Constraints implied by the problem

The first repair must cross the actual owner boundary, separate archive success from canonical persistence, and produce a receipt that can be checked without opening the store directly.

# 3) Research Grounding (external + internal “ground truth”)

<!-- arch_skill:block:research_grounding:start -->
## 3.1 External anchors (papers, systems, prior art)

- **Transactional outbox / acknowledged delivery:** adopt the invariant that producer progress follows durable consumer acknowledgment, not submission. Here the archive is immutable source evidence, `/ingest` is delivery, terminal job success is persistence, and `get_page` hash equality is the acknowledgment.
- **Single-writer embedded databases:** adopt one long-lived owner around PGLite. The isolated oracle showed storage and FTS correctness when idle, while every concurrent external reader timed out under one holder.
- **OAuth source binding:** adopt GBrain's existing client registration and authenticated source authority so a writer cannot spoof another source with a request header.
- **Content-addressed idempotency:** adopt normalized content hashes plus source-qualified slugs as the replay key; repeated identical pages must return duplicate/unchanged success without new canonical identity.

## 3.2 Internal ground truth (code as spec)

- `src/commands/serve.ts` already supports `gbrain serve --http`, loopback binding, owner lifecycle, optional owner-local ingestion, graceful HTTP shutdown, and engine disconnect.
- `src/commands/serve-http.ts` already implements authenticated `POST /ingest`, source binding, caller slug validation, content hashing, job submission, and a structured 202 response containing `job_id`, `content_hash`, and `source_id`.
- `src/core/minions/handlers/ingest-capture.ts` persists a source-bound event into the canonical page path.
- `src/core/operations.ts` exposes `get_job`, `get_page`, `get_brain_identity`, and `search` over both HTTP and stdio through shared dispatch.
- `src/core/ingestion/service.ts` demonstrates the correct owner-local rule: it receives an already connected engine, never constructs another one, persists serially, and runs stale embedding inside the owner.
- `adapters/messages/run_fresh_sync.sh` currently invokes only `messages_collector.py recent`; it neither submits nor acknowledges rendered pages.
- The same collector-only wrapper shape exists under `adapters/gmail`, `adapters/calendar`, and `adapters/granola`; Phase 02 owns their migration.
- `src/gbrain_ops/service.py` already renders and atomically installs launchd plists; extend this owner path rather than introducing a second supervisor.
- `src/gbrain_ops/sync_runner.py` already owns argv-safe child execution, process-group timeout termination, and heartbeat state; collector wrappers remain compatible with it.
- `tests/test_keyword_oracle.py` and the valid oracle receipt prove idle source-scoped keyword retrieval and post-holder recovery, and prove direct reader/writer coexistence is not acceptable.

Canonical owner path: fixed promoted GBrain runtime → one loopback `serve --http` process → `/ingest` and shared MCP operations → active PGLite store. Archive collectors never open the store.

Compatibility posture: hard cutover for the Messages wrapper. Existing collector output and sync-runner invocation remain stable; the post-collector persistence step changes from absent/direct-open to owner submission. No dual runtime path is retained.

## 3.3 Decision gaps that must be resolved before implementation

None. Repo truth settles the first slice: use client-credentials OAuth for the Messages writer, use `/ingest` for source-bound submission, poll `get_job`, verify with `get_page`, and write a private receipt only after equality. The owner service remains loopback-only and uses the promoted runtime.
<!-- arch_skill:block:research_grounding:end -->

# 4) Current Architecture (as-is)

<!-- arch_skill:block:current_architecture:start -->
## 4.1 On-disk structure

- `~/.gbrain/integrations/<source>/data/`: raw immutable or append-only source records and collector state.
- `~/.gbrain/integrations/<source>/brain/`: deterministic markdown projections.
- `~/.gbrain/brain.pglite`: active derived canonical/search store.
- `adapters/<source>/run_fresh_sync.sh`: public generic wrappers deployed as private integration scripts.
- `src/gbrain_ops/sync_runner.py`: heartbeat and process-boundary supervision.
- promoted GBrain runtime: versioned worktree behind `~/.n/bin/gbrain`.

## 4.2 Control paths (runtime)

Current Messages flow:

```text
Hermes cron -> sync_runner.py -> run_fresh_sync.sh
                                -> messages_collector.py recent
                                -> raw JSONL + deterministic markdown
                                -> prints status=collected and exits 0

Separate gbrain CLI command -> opens PGLite -> import/embed/search
```

There is no edge from the successful collector run to canonical persistence. Each later CLI command independently acquires the PGLite directory lock.

## 4.3 Object model + key abstractions

- `source_id + slug` is canonical page identity.
- Archive markdown content is source evidence; PGLite pages/chunks/embeddings are derived projections.
- `MinionQueue` and the `ingest-capture` handler already model asynchronous persistence jobs.
- OAuth clients carry `sourceId` and scopes as authenticated authority.
- `get_job` exposes job terminal state; `get_page` exposes canonical content and provenance.

## 4.4 Observability + failure behavior today

- Collector state proves only archive rendering.
- Sync-monitor has no Messages state file because the cron currently runs the wrapper directly rather than through the common heartbeat runner.
- Source metadata can report `last_sync_at: null` even when the archive is fresh.
- A stale active page is silent unless an acceptance probe compares it with the archive.
- Concurrent external readers time out while a writer holds PGLite; lock timeouts do not identify business-level operation ownership.

## 4.5 UI surfaces (ASCII mockups, if UI work)

No UI work. The user-visible surface is the assistant answer and its provenance.
<!-- arch_skill:block:current_architecture:end -->

# 5) Target Architecture (to-be)

<!-- arch_skill:block:target_architecture:start -->
## 5.1 On-disk structure (future)

- Public code:
  - `src/gbrain_ops/owner_client.py`: OAuth/token, `/ingest`, MCP job/page calls, hash verification, receipt model.
  - `src/gbrain_ops/archive_reconcile.py`: deterministic file-to-slug reconciliation with bounded retries and atomic private receipts.
  - `scripts/reconcile_archive.py`: CLI entry point used by collector wrappers.
  - `tests/test_owner_client.py` and `tests/test_archive_reconcile.py`: contract tests.
  - `adapters/messages/run_fresh_sync.sh`: collector then acknowledged reconcile.
  - `src/gbrain_ops/service.py`: owner launchd render/install helpers and hardening.
- Private state:
  - owner/client credentials under `~/.gbrain/owner/` mode 0600.
  - receipts under `~/.gbrain/owner/receipts/<source>/`.
  - launchd plist/logs outside Git.

## 5.2 Control paths (future)

```text
collector -> deterministic markdown -> archive reconciler
                                     -> OAuth client_credentials
                                     -> POST /ingest (source-bound)
                                     -> poll get_job until terminal
                                     -> get_page(source, slug)
                                     -> compare normalized content hash
                                     -> atomic private receipt

Hermes/clients -> one HTTP MCP owner -> same connected engine -> PGLite
```

The owner process is the only PGLite opener. The reconciler never imports, embeds, authenticates, or queries by opening the database directly.

## 5.3 Object model + abstractions (future)

- `OwnerCredentials`: private issuer/MCP/ingest URL plus client ID/secret; no token persistence required.
- `IngestSubmission`: source, slug, source URI, local normalized hash, job ID.
- `PersistenceReceipt`: schema version, source, slug, local/remote hashes, job ID/status, brain ID, observed times, and verification outcome.
- `ArchiveCandidate`: filesystem path, source-qualified slug, content bytes, source URI.
- `OwnerClient`: acquire short-lived token, submit, poll, get page, search, identity.
- `ArchiveReconciler`: select candidates, compare prior receipt/local hash, reconcile changed pages, and atomically advance receipt only after equality.

## 5.4 Invariants and boundaries

- Token responses and secrets never enter logs or receipts.
- `/ingest` 202 is a submission receipt only.
- Terminal job failure, timeout, owner mismatch, missing page, source mismatch, or hash mismatch exits nonzero.
- Replaying a matching candidate is a no-op verified against the prior receipt; a forced verification mode re-reads the owner.
- The service binds only to `127.0.0.1` and starts from the immutable promoted runtime.
- The Messages wrapper has one hard-cutover path: collector then owner reconcile. It contains no direct `gbrain import`, `capture`, or `embed` fallback.
- Client-credentials token acquisition is bounded and refreshed per reconcile run; access tokens are memory-only.
- Job polling accepts only documented terminal success states, rejects terminal failures, and times out with the job ID but no payload.
- Receipt replacement is atomic and owner-only (`0600`); interrupted writes leave the previous verified receipt intact.
- Live cutover requires zero holders before credential provisioning, then exactly one holder after owner start and after the concurrent acceptance probe.
- The active brain's persistent identity must match before and after restart; counts alone are not identity proof.

## 5.5 UI surfaces (ASCII mockups, if UI work)

No UI. CLI JSON is concise and receipt-oriented; private payload content is never printed.
<!-- arch_skill:block:target_architecture:end -->

# 6) Call-Site Audit (exhaustive change inventory)

<!-- arch_skill:block:call_site_audit:start -->
## 6.1 Change map (table)

| Area | File | Symbol / Call site | Current behavior | Required change | Why | New API / contract | Tests impacted |
|---|---|---|---|---|---|---|---|
| Owner client | `src/gbrain_ops/owner_client.py` | new module | absent | authenticate, ingest, poll, read, search, identity | cross owner without direct DB | `OwnerClient` | new unit tests |
| Reconcile | `src/gbrain_ops/archive_reconcile.py` | new module | absent | map archive files to slugs, verify, receipt | acknowledged adapter checkpoint | `ArchiveReconciler` | new unit/integration tests |
| CLI | `scripts/reconcile_archive.py` | new entry point | absent | source/root/pattern/receipt/config args, JSON result | wrapper-stable executable surface | exit/JSON contract | new CLI tests |
| Messages adapter | `adapters/messages/run_fresh_sync.sh` | post-collector flow | exits after collection | invoke reconciler for recent daily pages | active page freshness | collector then acknowledge | shell smoke + integration |
| Deployed Messages adapter | private installed copy | runtime script | collector only | reinstall from public owner | make runtime use code under test | install receipt | live acceptance |
| Service helpers | `src/gbrain_ops/service.py` | launchd payload | generic plist only | fixed cwd/env, KeepAlive/throttle, loopback owner contract | durable one owner | owner service spec | service tests |
| Runtime setup | private owner config | OAuth clients + launchd | absent | provision Messages writer and read client offline | source binding and clients | private config schema | live setup proof |
| Product runtime | vendor `src/core/ingestion/service.ts`, `src/commands/serve-http.ts` | `/ingest`, `IngestionService` | `/ingest` queues a job but the HTTP owner starts no Minion worker; a separate worker would be a second PGLite opener | expose owner-local service submission and an authenticated persisted-response mode | make network ingestion complete inside the sole owner | `Prefer: wait=persisted` / structured outcome | focused vendor unit + integration tests |
| Docs | `README.md`, architecture/runbook docs | ingestion topology | collector/control-plane only | document owner and receipt boundary generically | keep live truth current | operator runbook | privacy scan |

## 6.2 Migration notes

- Canonical owner path is the promoted runtime's HTTP service. No new database owner daemon is introduced.
- Messages changes first. Gmail, Calendar, and Granola are explicitly assigned to epic Phase 02 and are not silently treated as complete here.
- Existing collector CLI and archive layout are preserved.
- The deployed wrapper is replaced atomically after isolated tests and a live canary pass.
- Any temporary direct import used to repair current data ends before owner cutover and is recorded as pre-cutover maintenance, not a fallback path.
- Delete/retire the collector-only deployed Messages wrapper after activation; Git preserves history.
- Update current docs/comments that say collectors are complete after rendering.

## Pattern Consolidation Sweep (anti-blinders; scoped by plan)

- Gmail, Calendar, Granola, and Telegram wrappers share the collector-only pattern. Phase 02 migrates enabled non-waived sources; Telegram remains explicitly waived.
- `sync_runner.py` remains the common process/heartbeat owner and should call wrappers, not duplicate reconciliation logic.
- `put_page` write-through is not used for archive reconciliation because it can duplicate source-root prefixes; `/ingest` plus readback is the canonical path.
<!-- arch_skill:block:call_site_audit:end -->

# 7) Depth-First Phased Implementation Plan (authoritative)

> Rule: depth-first implementation protects the full destination while proving the path early. TL;DR, Section 0, Sections 5-6, and approved decisions are the destination map. The first phase proves the risk-bearing owner/acknowledgment seam. No fallback or parallel direct-open path survives cutover.

<!-- arch_skill:block:phase_plan:start -->
## Phase 1 — Build the acknowledged owner client contract

### Goal

Create the smallest reusable client that can cross the existing owner boundary and distinguish queue acceptance from verified persistence.

### Work

Implement credential loading, token acquisition, `/ingest` submission, MCP job/page/identity calls, normalized hashing, receipt modeling, and atomic receipt storage without touching live configuration.

### Checklist (must all be done)

- [ ] Add typed owner credential, submission, terminal-job, page-evidence, and receipt models.
- [ ] Add bounded client-credentials token acquisition with no token logging or persistence.
- [ ] Add source/slug/source-URI-bound `/ingest` submission.
- [ ] Add MCP `get_job`, `get_page`, `get_brain_identity`, and search calls over Streamable HTTP.
- [ ] Add terminal-state polling with explicit timeout and terminal-failure behavior.
- [ ] Add normalized local/remote content hashing consistent with GBrain ingestion.
- [ ] Add source/slug/hash/brain-identity readback verification.
- [ ] Add atomic `0600` receipt replacement and prior-receipt no-op handling.
- [ ] Add the generic reconcile CLI with JSON output that excludes content and credentials.
- [ ] Add unit tests for success, duplicate, mismatch, terminal failure, timeout, token failure, redaction, and interrupted receipt replacement.

### Verification (required proof)

Run targeted pytest for owner/reconcile modules, Python compilation, static privacy scanning, and CLI fixture tests with an in-process fake HTTP/MCP server.

### Docs/comments (propagation; only if needed)

Place one high-leverage comment at the acknowledgment boundary: HTTP 202 is not persistence proof; receipt movement requires terminal job success plus canonical readback equality.

### Exit criteria (all required)

- [ ] All contract tests pass.
- [ ] No public output, exception, or receipt contains client secret, access token, or payload body.
- [ ] No code path imports the direct GBrain CLI or opens PGLite.
- [ ] Replaying the same candidate is idempotent and does not replace a verified receipt unnecessarily.

### Rollback

Delete the new unused modules and tests; no live state has changed.

## Phase 2 — Prove one owner in an isolated brain

### Goal

Demonstrate the full owner → ingest → terminal job → page readback path under exactly one holder before touching the active personal brain.

### Work

Create an isolated GBrain home and source, provision an isolated writer client, start the promoted runtime owner on an ephemeral loopback port, submit a deterministic canary, run concurrent reads, restart, and verify identity and holder invariants.

### Checklist (must all be done)

- [ ] Initialize an isolated PGLite brain with a persistent identity and registered canary source.
- [ ] Provision a source-bound client without exposing its secret.
- [ ] Start one promoted-runtime HTTP owner bound to loopback.
- [ ] Submit and acknowledge one deterministic markdown canary through the new client.
- [ ] Verify source/slug/hash equality and search retrieval.
- [ ] Run concurrent read probes while a write is persisted.
- [ ] Verify exactly one PGLite-holder PID throughout.
- [ ] Restart the owner and verify the same persistent brain identity and canary.
- [ ] Exercise terminal failure/timeout without corrupting the prior receipt.

### Verification (required proof)

Produce a private structured acceptance receipt and run the isolated integration test repeatedly from a clean temporary home.

### Docs/comments (propagation; only if needed)

Document the isolated acceptance invocation and artifact locations generically in the public README/runbook.

### Exit criteria (all required)

- [ ] Isolated end-to-end acceptance passes.
- [ ] Concurrent reads succeed during the owner-local write.
- [ ] Holder count is exactly one before and after restart.
- [ ] A second direct opener fails loud and leaves the owner healthy.

### Rollback

Stop the isolated owner and delete only the temporary isolated home and private test credentials.

## Phase 3 — Cut Messages over to acknowledged owner ingestion

### Goal

Make the live Messages fresh-sync job update the active canonical page and remain queryable without a second PGLite opener.

### Work

Quiesce direct openers, preserve private configuration, provision source-bound credentials offline, start a supervised loopback owner, deploy the new wrapper, and run a real recent sync and acceptance probe.

### Checklist (must all be done)

- [ ] Wait for any in-flight embedding/import process to finish and inspect its terminal result.
- [ ] Back up private GBrain config, wrapper, launchd state, and current active brain identity.
- [ ] Require zero PGLite holders before credential provisioning.
- [ ] Provision a Messages writer client and a read client into owner-only private config without printing secrets.
- [ ] Install/start the loopback owner from the immutable promoted runtime.
- [ ] Require healthy `/health`, one listener, and exactly one PGLite-holder PID.
- [ ] Update `adapters/messages/run_fresh_sync.sh` to run collector then reconcile only changed recent pages.
- [ ] Atomically deploy the tested wrapper and preserve a rollback copy.
- [ ] Run the real Messages fresh-sync wrapper through the heartbeat runner.
- [ ] Verify the current daily archive and canonical page match source/slug/hash and message-count evidence.
- [ ] Run concurrent owner search/get calls while replaying the same page.
- [ ] Verify embeddings are healthy or record a bounded owner-local catch-up before Phase 03 of the epic.
- [ ] Disable/retire any direct-open Messages schedule or path superseded by the wrapper.

### Verification (required proof)

Use the actual current Messages page, the sync-runner heartbeat state, the owner receipt, the owner identity, search/get calls, process ownership, and a restart probe.

### Docs/comments (propagation; only if needed)

Update public architecture/README and the GBrain local-operations skill if the proven procedure differs from its current single-owner guidance.

### Exit criteria (all required)

- [ ] Fresh archive content reaches canonical storage in the same run.
- [ ] The receipt matches current source/slug/hash and active brain identity.
- [ ] Search/get remain available during replay.
- [ ] Exactly one holder remains after sync and after owner restart.
- [ ] Messages sync exits nonzero when acknowledgment cannot be obtained.
- [ ] The old collector-only deployed wrapper is retired with a verified rollback copy.

### Rollback

Boot out the owner, wait for zero holders, restore the wrapper/config backup, restore the active store only if canonical persistence damaged it, and leave Messages schedules paused rather than reintroducing a competing writer.

## Phase 4 — Audit the first slice and hand off the reusable contract

### Goal

Prove every Phase 01 epic obligation, close implementation findings, and leave a stable contract for Phase 02 collector migration.

### Work

Run targeted and repo-wide verification, privacy and history checks, a fresh implementation audit, and an independent epic critic; update the plan/worklog and publish only after clean evidence.

### Checklist (must all be done)

- [ ] Map every call-site-audit row to shipped code or a named later epic phase.
- [ ] Run targeted tests, full pytest, type/compile checks, privacy scan, Gitleaks, and diff checks.
- [ ] Run a fresh implementation audit against all phase checklists and exit criteria.
- [ ] Repair every code/spec/quality finding and rerun proof.
- [ ] Run the epic completion/scope critic on GPT-5.6-Sol xhigh.
- [ ] Update the North Star worklog with the measured first-slice result.
- [ ] Commit and push only public, sanitized artifacts after all gates pass.

### Verification (required proof)

The arch-step implementation audit says `Verdict (code): COMPLETE`, the epic critic returns pass, CI passes, and the live Messages acceptance receipt remains valid after restart.

### Docs/comments (propagation; only if needed)

Keep only current operational truth. Delete stale collector-only instructions and point Phase 02 at the shipped owner client contract.

### Exit criteria (all required)

- [ ] No plan item or exit criterion is unmet.
- [ ] No private identifiers, paths, payloads, credentials, receipts, or runtime state entered Git.
- [ ] The Messages slice satisfies the epic gate to Phase 02.
- [ ] The owner client API is reusable without source-specific logic.

### Rollback

Revert the public commit and execute Phase 3 rollback. Preserve the sealed original and all pre-cutover receipts.
<!-- arch_skill:block:phase_plan:end -->

# 8) Verification Strategy (common-sense; evidence planning)

## 8.1 Unit tests (contracts)

Use existing pytest coverage in `gbrain-ops` for deterministic client, receipt, timeout, mismatch, and redaction behavior.

## 8.2 Integration tests (flows)

Use an isolated GBrain home for owner/ingest/readback/one-holder tests, then one live Messages page under the active owner after backup and zero-holder gates.

## 8.3 E2E / device tests (realistic)

Run the Messages collector, submit the changed daily page, verify exact readback, issue concurrent searches, restart the owner, and repeat identity/page checks.

# 9) Rollout / Ops / Telemetry

## 9.1 Rollout plan

Back up private config; stop direct GBrain openers; provision source-bound credentials offline; start the owner; prove the Messages canary; switch the Messages wrapper; leave other collectors paused until Phase 02 migrates them.

## 9.2 Telemetry changes

Write owner-only JSON receipts with source, slug, local content hash, remote content hash, job ID, timing, brain identity, and terminal outcome. Never record payload content or credentials.

## 9.3 Operational runbook

The service must expose `/health`; owner lifecycle uses launchd with a fixed promoted runtime and loopback bind. Failure to acknowledge keeps the collector job failed and does not mutate the receipt.

# 10) Decision Log (append-only)

<!-- arch_skill:block:consistency_pass:start -->
## Consistency Pass — 2026-07-11

- Cold-read 1: TL;DR, North Star, research, target owner path, call-site inventory, phase checklist, verification, and rollback all choose one owner and acknowledged persistence; no competing direct-open path survives.
- Cold-read 2: Epic Phase 01 coverage is complete for the Messages proving slice; Gmail, Calendar, Granola, personal-question retrieval, and final hardening have named later owners and are not dropped.
- Compatibility posture: clean cutover for Messages ingestion; existing deterministic archive format and collector CLI are preserved.
- Fallback posture: forbidden. Owner failure makes sync fail loud and leaves the prior receipt intact.
- Decision-complete: yes
- Unresolved decisions: none
- Unauthorized scope cuts: none
- Decision: proceed to implement? yes
<!-- arch_skill:block:consistency_pass:end -->

## 2026-07-11 - Use existing loopback owner and authenticated ingestion

- **Context:** Isolated PGLite search works; direct concurrent ownership does not. Current GBrain already exposes a loopback HTTP owner, source-bound OAuth clients, `/ingest`, job APIs, and page reads.
- **Options:** migrate immediately to PostgreSQL; build a new local queue/daemon; reuse the current owner and ingestion substrate.
- **Decision:** Reuse the current owner and ingestion substrate. Backend migration is not required unless measured acceptance fails.
- **Consequences:** The ops control plane needs a thin authenticated client and receipt contract, not another storage engine.
- **Follow-ups:** Phase 02 migrates every collector and client once the Messages slice passes.

## 2026-07-11 - Intent-derived: persistence before embedding completion

- **Blocker:** Whether an ingestion receipt should wait for the entire global stale-embedding backlog.
- **Consulted:** Section 0.4, Section 0.5, TL;DR, epic Phase 01 gate, and the existing owner ingestion service.
- **Intent says:** Canonical evidence must be durably persisted and read back; embeddings are a rebuildable derived projection and cannot block raw-evidence RPO.
- **Decision:** Acknowledgment requires persisted page equality. Embedding status is recorded separately and must become healthy before the Phase 03 retrieval gate.
- **Consequences:** Fresh source evidence remains available to lexical reads even if vector backfill is delayed.

## 2026-07-11 - Implementation discovery: queue admission is orphaned under a sole PGLite owner

- **Context:** The existing HTTP `/ingest` route only calls `MinionQueue.add`. `serve --http` starts no Minion worker, and a separate worker process would open PGLite concurrently, violating the measured one-holder invariant.
- **Options:** accept stuck jobs; run a second worker process; add a new storage queue; expose the existing owner-local `IngestionService` dispatch through an explicit persisted-response mode.
- **Decision:** Add an opt-in authenticated `Prefer: wait=persisted` path that submits the validated event to the already-connected `IngestionService`, waits for accepted/duplicate persistence, and returns the canonical content hash. Keep the legacy 202 queue path unchanged for compatibility.
- **Consequences:** The ops reconciler can acknowledge canonical persistence without job polling or a second opener. Embedding remains owner-local and asynchronous. Phase 1 client tests cover both persisted success and fail-loud service-unavailable behavior.

# 11) Implementation Log

## 2026-07-12 - Implemented and verified

- Added owner-client credentials, OAuth token acquisition, persisted ingestion, source/canonical hash verification, MCP reads/search, and atomic private receipts.
- Added the owner-local `Prefer: wait=persisted` runtime path and preserved it as carried patch 0003.
- Promoted and activated runtime `release-v2` at `f93da043cef39b98905279be0a08da576b076720`.
- Installed supervised loopback service `com.gbrain.owner` on port 3131. Live brain identity remains `d3d13cc8-e6db-4e9c-b608-4443b49cb6bf`.
- Reconciled July 3-11 Messages pages with zero failures; July 11 was accepted and read back with matching canonical hash.
- Added filename-date filtering because the collector deliberately rewrites all 2,546 historical pages. The production wrapper now scans only its bounded date window.
- Re-ran production Messages fresh sync: 9 scanned, 0 submitted, 9 unchanged, exit 0.
- Proved authenticated hybrid retrieval returns July 10 for `fig-inspired dinner` and July 11 for `special travel calendar`.
- Verification evidence: 49 full ops tests; 22 targeted runtime owner tests; runtime TypeScript typecheck; focused owner/date-filter tests; live health and idempotent replay.
