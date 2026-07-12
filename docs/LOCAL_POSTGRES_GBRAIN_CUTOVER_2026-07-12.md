---
title: "GBrain - Local PostgreSQL Cutover - Architecture Plan"
date: 2026-07-12
status: completed
fallback_policy: forbidden
owners: [Blake Messer]
reviewers: [Hermes]
doc_type: architectural_change
related:
  - docs/NORTH_STAR_PERSONAL_ASSISTANT_BRAIN_2026-07-11.md
  - docs/EPIC_OPERATIONAL_PERSONAL_ASSISTANT_BRAIN_2026-07-11.md
---

# TL;DR

- **Outcome:** The active GBrain serving store moved from local PGLite/WASM to a dedicated local native PostgreSQL instance with pgvector while preserving the authenticated single-owner API and accepted personal retrieval behavior.
- **Problem:** PGLite was healthy only behind strict one-holder supervision and had exhibited lock, checkpoint, and uninterruptible WASM maintenance failure modes. The broader off-laptop architecture also requires a Postgres-compatible serving boundary.
- **Approach:** An isolated PostgreSQL cluster was provisioned without touching Blake's existing PostgreSQL 15 databases; source-qualified pages and embeddings were migrated side by side, exact parity and recovery were proven, and the owner was atomically restarted against PostgreSQL.
- **Plan:** Completed: provision → migrate → parity/recovery tests → owner cutover → collector/Hermes acceptance → sanitized operations publication.
- **Non-negotiables:** No destructive PGLite operation; no direct collector DB access; no secrets in Git/logs; no cutover without content, retrieval, ebook, least-privilege, restart, backup, and rollback evidence.

## Implementation status — 2026-07-12

- Active owner: PostgreSQL 17.10 + pgvector 0.8.5 on loopback port 55432; logical brain identity preserved.
- Migration: 10,806 source-qualified pages, 30,330 baseline chunks/vectors, 35,281 tags, 2,295 page versions, six sources, auth state, provenance metadata, and durable operational state migrated.
- Independent audit follow-through: the built-in behavioral migrator's source-registry, supplemental-table, page-metadata, chunk-metadata, and identity gaps were repaired. Exhaustive natural-key column parity is zero for every unchanged page/chunk; one page was intentionally excluded because a real collector updated it after cutover.
- Explicit reset policy: 22 PGLite `query_cache` rows were intentionally invalidated because they are backend-specific derived state keyed to pre-migration generations/IDs. Empty locks, leases, reservations, jobs, facts, takes, ontology, timeline, raw-data, link, alias, file, code-edge, and multimodal auxiliary tables required no row transfer.
- Recovery: private custom-format PostgreSQL backup restored successfully to an isolated database; PGLite rollback and return-to-PostgreSQL completed in six seconds.
- Clients: Hermes authorization/personal evidence passed; Gmail, Calendar, Messages, and Granola post-cutover sync jobs all completed successfully; watchdog exit is zero.
- Publication: implementation commit `eadcb3f251cbf23729830f6cf052a942676e070e` is on `origin/main`; hosted CI and vendor compatibility passed in [run 29202912279](https://github.com/rblakemesser/gbrain-ops/actions/runs/29202912279), and GitHub signoff was recorded for that commit.
- Review: manual specification/security/code review and CRG change-impact review found no blocker; CRG reported low risk (`0.30`) and no test gaps. A supplemental local Codex audit was attempted after upgrading the CLI, but the newest available CLI still rejected the configured `gpt-5.6-sol` model and therefore produced no verdict.

<!-- arch_skill:block:planning_passes:start -->
<!--
arch_skill:planning_passes
deep_dive_pass_1: completed 2026-07-12
external_research_grounding: completed 2026-07-12
external_research_note: GBrain's own Postgres engine, migration command, Homebrew PostgreSQL/pgvector packaging, and live local service inventory supplied the required external/runtime grounding.
deep_dive_pass_2: completed 2026-07-12
recommended_flow: implement -> audit implementation
note: Blake explicitly approved local PostgreSQL on 2026-07-12; this plan records and executes that decision.
-->
<!-- arch_skill:block:planning_passes:end -->

# 0) Holistic North Star

## 0.1 The claim (falsifiable)

After cutover, the same authenticated GBrain owner and Hermes read identity operate against native PostgreSQL with all accepted source-qualified pages, chunks, vectors, provenance, and personal retrieval results preserved; PostgreSQL restart and concurrent owner reads/writes complete without PGLite locking or loss, and rollback to the preserved PGLite generation remains possible within ten minutes.

## 0.2 In scope

- Dedicated local PostgreSQL and pgvector provisioning.
- Least-privilege runtime and migration roles stored only in owner-private configuration.
- Source-qualified page/chunk/embedding migration.
- Protected store identity and explicit cutover receipt.
- Owner, wrappers, Hermes MCP, watchdog, backup, restore, and rollback adaptation.
- Ebook corpus verification.

## 0.3 Out of scope

- Supabase/cloud deployment, remote replica, or public database exposure.
- Direct collector, Hermes, or model database access.
- Rewriting GBrain's retrieval product or changing accepted answer behavior.
- Deleting either the active or sealed PGLite stores.
- Reconfiguring the unrelated Homebrew PostgreSQL 15 instance or its databases.

## 0.4 Definition of done (acceptance evidence)

- Exact source/page membership and canonical content-hash parity.
- Chunk and non-null embedding parity with vector width 1280.
- Source inventory and protected target brain identity recorded privately.
- Lexical, hybrid, exact-date, relationship, chronology, and ebook probes pass.
- Hermes scope remains `read`, allowed-source reads pass, and writes remain denied.
- Owner restart, PostgreSQL restart, concurrent read/write, backup, isolated restore, and PGLite rollback drills pass.
- All collectors complete acknowledged idempotent runs after cutover.
- Public ops tests, Ruff, privacy scan, Gitleaks, diff check, CI, and gh-signoff pass.

## 0.5 Key invariants (fix immediately if violated)

- PGLite remains immutable during target construction and retained after cutover.
- Exactly one logical GBrain owner; no client receives a DB credential.
- The PostgreSQL listener remains local-only.
- Every migrated page retains source identity and valid content hash.
- Embedding model and vector width remain `openai:text-embedding-3-large` / 1280.
- A failed pre-cutover gate leaves the owner on PGLite; a post-cutover failure invokes the tested PGLite rollback.
- No runtime fallback or silent dual-write path.

# 1) Key Design Considerations (what matters most)

## 1.1 Priorities (ranked)

1. Preserve private data and exact accepted behavior.
2. Preserve reversible service availability.
3. Remove the PGLite/WASM production failure boundary.
4. Establish a Postgres-compatible path toward later off-laptop hosting.
5. Avoid disturbing unrelated local databases.

## 1.2 Constraints

- Current corpus is 10,806 pages and approximately 30,330 chunks.
- Existing PostgreSQL 15 serves unrelated local databases and must not be changed.
- Current Homebrew pgvector packaging targets PostgreSQL 17/18.
- The owner and collectors already depend on private environment indirection and acknowledged writes.

## 1.3 Architectural principles (rules we will enforce)

- Dedicated cluster, port, data directory, roles, database, logs, and backups.
- Owner API remains the canonical access path.
- Configuration changes are atomic and secret-safe.
- Migration receipts use exact source-qualified membership, not aggregate counts alone.
- Promotion is a hard cutover after proof, not indefinite dual operation.

## 1.4 Known tradeoffs (explicit)

Native PostgreSQL adds a supervised daemon and backup responsibility. In exchange it provides mature WAL, crash recovery, concurrency, introspection, and a direct future path to Supabase. Keeping a logical owner leaves some serialization by design, but preserves authorization and provenance boundaries.

# 2) Problem Statement (existing architecture + why change)

## 2.1 What exists today

One authenticated loopback GBrain owner holds `~/.gbrain/brain.pglite`; collectors reconcile archives through owner APIs and Hermes has a read-only, source-allowed MCP credential.

## 2.2 What’s broken / missing (concrete)

PGLite direct concurrency failed 30/30 controlled probes, large staging previously wedged at a WASM checkpoint boundary, and the serving store cannot be moved to an always-on host without changing the storage boundary. Native Postgres support exists but has not been exercised against Blake's accepted corpus.

## 2.3 Constraints implied by the problem

The migration must preserve owner semantics, source-qualified identity, private receipts, embeddings, retrieval, and rollback; replacing PGLite cannot reintroduce direct database clients or conflate local migration with cloud exposure.

# 3) Research Grounding (external + internal “ground truth”)

## 3.1 External anchors (papers, systems, prior art)

- PostgreSQL native WAL and crash recovery are the boring serving boundary selected by the existing North Star.
- pgvector is the current vector implementation required by GBrain; Homebrew 0.8.5 supports the dedicated PostgreSQL 17 target.
- A separate cluster avoids in-place major-version upgrade risk to unrelated databases.

## 3.2 Internal ground truth (code as spec)

- `src/core/postgres-engine.ts` is a full `BrainEngine` implementation.
- `src/commands/migrate-engine.ts` implements resumable multi-source PGLite→Postgres transfer and preserves chunks/embeddings, tags, timeline, raw data, and links.
- `src/core/config.ts` chooses Postgres from `GBRAIN_DATABASE_URL` or file config.
- The ops owner launcher already sources an owner-only environment file, the canonical place for the runtime URL.
- Existing `archive_reconcile`, evidence retrieval, owner authorization, and watchdog paths are engine-independent.

## 3.3 Decision gaps that must be resolved before implementation

None. Blake approved local PostgreSQL; repository and runtime evidence select a dedicated PostgreSQL 17 cluster, local-only networking, hard owner cutover, and preserved PGLite rollback.

<!-- arch_skill:block:research_grounding:start -->
Grounding completed from the active promoted runtime, the current ops control plane, and the live PostgreSQL/Homebrew inventory on 2026-07-12.
<!-- arch_skill:block:research_grounding:end -->

# 4) Current Architecture (as-is)

## 4.1 On-disk structure

- `~/.gbrain/brain.pglite` active store.
- `~/.gbrain/brain.pglite.sealed-pre-cutover-20260711T051447Z` sealed recovery generation.
- `~/.gbrain/owner/` private tokens, receipts, launcher, and logs.
- `gbrain-ops` public control plane and carried runtime patches.

## 4.2 Control paths (runtime)

Collectors write archives and call the authenticated owner. Hermes reads over owner MCP. The owner alone opens PGLite.

## 4.3 Object model + key abstractions

`BrainEngine`, `PGLiteEngine`, `PostgresEngine`, protected `brain_identity`, source-qualified pages, chunks, and owner operations.

## 4.4 Observability + failure behavior today

Owner health, one-holder checks, collector cron states, persistence receipts, private acceptance packets, and sealed rollback exist. PGLite failures surface as lock/checkpoint/WASM issues.

## 4.5 UI surfaces (ASCII mockups, if UI work)

No UI change.

# 5) Target Architecture (to-be)

## 5.1 On-disk structure (future)

- `~/.gbrain/postgres/17/data` dedicated cluster.
- `~/.gbrain/postgres/17/backups` encrypted/private logical and physical recovery artifacts.
- `~/.gbrain/owner/runtime.env` private `GBRAIN_DATABASE_URL` and unchanged API credentials.
- Existing PGLite directories retained read-only after acceptance.

## 5.2 Control paths (future)

Collectors → owner API → PostgresEngine → dedicated PostgreSQL. Hermes → owner MCP → PostgresEngine. No direct client DB connections.

## 5.3 Object model + abstractions (future)

Existing `BrainEngine` abstraction owns parity. Ops adds dedicated cluster lifecycle, secret-safe provisioning, parity receipt generation, backup/restore, and engine-aware watchdog checks.

## 5.4 Invariants and boundaries

Local-only listener, SCRAM authentication, separate owner/migrator roles, fixed vector width, no service credentials in public files, one logical owner, hard cutover, explicit rollback.

## 5.5 UI surfaces (ASCII mockups, if UI work)

No UI change.

<!-- arch_skill:block:current_architecture:start -->
Current PGLite owner path and live environment measured 2026-07-12.
<!-- arch_skill:block:current_architecture:end -->

<!-- arch_skill:block:target_architecture:start -->
Dedicated PostgreSQL 17/pgvector cluster behind the unchanged authenticated owner boundary.
<!-- arch_skill:block:target_architecture:end -->

# 6) Call-Site Audit (exhaustive change inventory)

## 6.1 Change map (table)

| Area | File | Symbol / Call site | Current behavior | Required change | Why | New API / contract | Tests impacted |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Provisioning | new ops module/script | cluster lifecycle | none | provision dedicated cluster/roles/db/extension | isolate existing PG15 | idempotent private receipt | new unit/integration |
| Migration | new ops script around runtime migrate | PGLite→Postgres | manual product command mutates config | supervised side-by-side transfer plus exact parity | safe promotion | migration receipt | new parity tests |
| Owner service | `src/gbrain_ops/service.py`, installer | runtime env | PGLite file config | inject private DB URL | target Postgres | no URL in plist/Git | contract tests |
| Watchdog | local deployed watchdog + ops source | health | PGLite holder count | PostgreSQL reachability, owner identity, cluster service | correct health | engine-aware checks | watchdog tests |
| Backup/restore | new ops script | none | sealed PGLite only | PostgreSQL dump/restore drill | DR | private backup receipt | integration test |
| Retrieval | existing evidence CLI | owner MCP | engine-independent | run unchanged against target | parity | no API change | live acceptance |
| Collectors | four wrappers | owner API | engine-independent | rerun after cutover | persistence proof | no API change | live acceptance |
| Hermes | current MCP | owner URL/token | engine-independent | verify same token semantics | least privilege | no API change | read/write probes |
| Docs | this plan + runbook | PGLite production truth | stale after cutover | update to PostgreSQL truth | avoid drift | operational runbook | review |

## 6.2 Migration notes

The product migration command is reused, but the ops control plane must prevent its automatic config mutation from becoming an unverified cutover. The target URL remains private. Existing PGLite direct-open and holder checks are retired from active health only after cutover; rollback instructions retain them.

<!-- arch_skill:block:call_site_audit:start -->
Adjacent surfaces classified: owner launcher, watchdog, collectors, Hermes, backups, acceptance, docs, runtime promotion, and rollback are included now; Supabase/cloud hosting is explicitly out of scope.
<!-- arch_skill:block:call_site_audit:end -->

# 7) Depth-First Phased Implementation Plan (authoritative)

> Rule: depth-first implementation protects the full destination while proving the path early. No fallbacks or silent dual writers. Every phase must satisfy every checklist item and exit criterion.

## Phase 1 — Isolated Postgres target and migration proof

**Goal:** Build a dedicated local target and prove a complete source-qualified transfer without changing the active owner.

**Work:** Install PostgreSQL 17/pgvector, initialize a private local-only cluster, create least-privilege roles/database, run schema initialization and resumable migration against a controlled target config.

**Checklist (must all be done):**
- [x] Preserve active and sealed PGLite stores and record fingerprints.
- [x] Provision PostgreSQL 17 cluster on a non-conflicting port.
- [x] Enforce local-only listener, SCRAM, mode-0700 data, private credentials.
- [x] Create pgvector extension and 1280-dimensional GBrain schema.
- [x] Migrate every active source-qualified page and embedding.
- [x] Produce private exact-member/hash/chunk/vector parity receipt.

**Verification (required proof):** Schema introspection, exact source/page/hash diff, chunk/embedding counts, vector width, target brain identity, and no active-owner change.

**Docs/comments (propagation; only if needed):** Document cluster ownership and secret boundary in the public runbook without values.

**Exit criteria (all required):** Empty exact-member diff, zero invalid hashes, exact chunk/embedding parity, target passes owner-safe read probes, PGLite owner remains healthy.

**Rollback:** Drop the isolated target database/cluster; active owner is unchanged.

## Phase 2 — Retrieval, failure, backup, and rollback acceptance

**Goal:** Prove PostgreSQL meets the North Star's serving and recovery gates before promotion.

**Work:** Run lexical/hybrid/personal/ebook probes, concurrent owner-style reads/writes, PostgreSQL restart, backup/isolated restore, and rollback timing.

**Checklist (must all be done):**
- [x] Run source/exact/lexical/hybrid probes.
- [x] Run July 10–11 relationship/chronology acceptance.
- [x] Run Hunt, Gather, Parent ebook retrieval.
- [x] Exercise concurrent reads and acknowledged writes.
- [x] Restart PostgreSQL and verify identity/parity.
- [x] Create private backup and restore it to an isolated verification database.
- [x] Prove rollback launcher/config can restore the PGLite owner within ten minutes.

**Verification (required proof):** Private acceptance packets, deterministic concurrency result, stable identity, backup checksum/restore parity, timed rollback receipt.

**Docs/comments (propagation; only if needed):** Add backup/restore and rollback runbook.

**Exit criteria (all required):** Every retrieval result matches accepted behavior, no corruption or identity rotation, restored DB matches source-qualified membership, rollback drill passes.

**Rollback:** Leave owner on PGLite and discard/rebuild target.

## Phase 3 — Atomic owner cutover and client acceptance

**Goal:** Make PostgreSQL the active backend without changing client contracts.

**Work:** Stop owner, atomically install private DB config, start owner, verify identity/engine, rerun collectors and Hermes probes, update watchdog.

**Checklist (must all be done):**
- [x] Capture final PGLite source fingerprint and freeze cutover manifest.
- [x] Reconcile target to the final cutoff.
- [x] Atomically switch private runtime configuration.
- [x] Start owner and verify `engine=postgres` and expected identity.
- [x] Verify Hermes read/source grants and denied write.
- [x] Run all four acknowledged collectors.
- [x] Update and run engine-aware watchdog.
- [x] Retain PGLite stores read-only and document rollback expiry criteria.

**Verification (required proof):** Owner health, identity/stats, collectors, Hermes read/write, personal evidence, ebook, PostgreSQL listener/role checks, watchdog exit 0.

**Docs/comments (propagation; only if needed):** Update active architecture and health semantics.

**Exit criteria (all required):** PostgreSQL owner serves all clients, collectors persist idempotently, no secret leakage, PGLite remains intact, rollback command remains tested.

**Rollback:** Stop owner, restore prior private runtime config, start PGLite owner, verify original identity and clients.

## Phase 4 — Audit and publication

**Goal:** Publish a reproducible, sanitized, reviewed local-Postgres operations layer.

**Work:** Run full local gates, critic reviews, hosted CI, gh-signoff, and commit/push only public-safe files.

**Checklist (must all be done):**
- [x] Run ops pytest, Ruff, syntax checks, privacy scan, Gitleaks, diff check.
- [x] Run relevant GBrain Postgres tests/typecheck.
- [x] Obtain specification and security/code-quality approval.
- [x] Update this plan/worklog to implementation truth.
- [x] Commit and push sanitized changes.
- [x] Verify hosted CI and gh-signoff.

**Verification (required proof):** Exact command results, critic verdicts, clean worktree, synchronized remote revision, successful CI/signoff.

**Docs/comments (propagation; only if needed):** Retire stale PGLite-active statements while preserving rollback docs.

**Exit criteria (all required):** All gates green and published revision verifiably matches the active control plane.

**Rollback:** Revert public control-plane commit independently; runtime rollback remains Phase 3's PGLite path.

<!-- arch_skill:block:phase_plan:start -->
Authoritative four-phase migration and promotion frontier approved by Blake's explicit 2026-07-12 directive.
<!-- arch_skill:block:phase_plan:end -->

# 8) Verification Strategy (common-sense; evidence planning)

## 8.1 Unit tests (contracts)

Provisioning idempotency, secret-safe rendering, role/URL redaction, parity comparison, engine-aware watchdog, and rollback config generation.

## 8.2 Integration tests (flows)

Real PostgreSQL schema/migration/parity, pgvector round-trip, concurrent owner operations, restart, backup/restore, and isolated target deletion.

## 8.3 E2E / device tests (realistic)

Live owner, four collectors, Hermes read/write authorization, July personal evidence, ebook search, watchdog, and timed rollback.

# 9) Rollout / Ops / Telemetry

## 9.1 Rollout plan

Side-by-side target, final delta/freeze, hard owner cutover, immediate acceptance, retained PGLite rollback. No dual writer.

## 9.2 Telemetry changes

Watchdog reports owner engine/identity, PostgreSQL readiness, expected local listener, backup age, collector freshness, and PGLite rollback presence without credentials or private data.

## 9.3 Operational runbook

Start/stop/status, backup, restore, credential rotation, migration replay, cutover, rollback, and later Supabase URL substitution through the same private owner boundary.

# 10) Decision Log (append-only)

## 2026-07-12 - User-approved native PostgreSQL migration

**Context:** PGLite is operational behind one-owner supervision, but Blake wants to proceed toward a conventional Postgres boundary and future off-laptop persistence.

**Options:** Retain PGLite indefinitely; mutate the existing PostgreSQL 15 cluster; provision a dedicated local PostgreSQL target; move directly to Supabase.

**Decision:** Provision and promote a dedicated local PostgreSQL 17/pgvector target now. Preserve owner semantics and make later Supabase adoption a separate remote-hosting phase.

**Consequences:** Adds one dedicated supervised database service and backup runbook; removes PGLite/WASM from the active serving boundary after acceptance.

**Follow-ups:** Evaluate Supabase/cloud compute only after local Postgres parity and cutover are stable.

## 2026-07-12 - Intent-derived: isolate existing local databases

**Blocker:** The machine already runs PostgreSQL 15 with unrelated databases.

**Consulted:** TL;DR, Sections 0, 1, and 7 plus live PostgreSQL inventory.

**Intent says:** Migration must be reversible and must not put unrelated data at risk.

**Decision:** Use a separate PostgreSQL 17 cluster, data directory, and port rather than upgrading or repurposing PostgreSQL 15.

**Consequences:** Slightly more local service management, materially smaller blast radius.
