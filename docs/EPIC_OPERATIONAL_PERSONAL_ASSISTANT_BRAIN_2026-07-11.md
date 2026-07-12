---
title: Epic — Make the local personal-assistant brain operational end to end
date: 2026-07-11
doc_type: epic
status: complete
raw_goal: |-
  Ok but clearly the point is to get the system working, please execute through arch flow to completion
raw_goal_sha256: 3855e86da13fc9d33f6fb1041397c92bc2e8f9319d63e3d10f3d40291c81d7c4
sub_plans_approved: true
critic_runtime: codex
critic_model: gpt-5.6-sol
critic_effort: xhigh
models_sha256: 816014303c1612749e750c5add7b7842e10fa0c0f0e2ba0487deba7ba2671d8b
auto_execution: null
---

# TL;DR

Make the replacement personal brain actually answer ordinary evidence-backed questions instead of stopping at storage counts. The epic first establishes acknowledged archive-to-canonical ingestion through one owner, then migrates every enabled collector and client onto that owner, adds relationship/time/similarity evidence retrieval with provenance, and finally cuts over schedules and proves the exact recent-conversation and prior-similarity acceptance scenario. The sealed pre-cutover store remains the rollback boundary. Materially different product scope halts for a scope-preserving decision; ordinary implementation defects are repaired in place until the gates pass.

# Decomposition

1. **Ship acknowledged ingestion through the canonical owner**: Make a freshly rendered source page become a source-qualified canonical page with a durable receipt, content-hash equality, and no second PGLite opener, using Messages as the first real slice.
   - DOC_PATH: docs/epic/OPERATIONAL_PERSONAL_ASSISTANT_BRAIN_2026-07-11/PHASE_01_ACKNOWLEDGED_INGESTION_2026-07-11.md
   - Gate to next: A fresh Messages page is persisted through the owner, read back with the same source/slug/content hash, and the holder count remains exactly one during concurrent reads and writes.
   - Status: implemented + verified
   - Epic-critic verdict: —

2. **Migrate all enabled collectors and clients to one owner**: Run Gmail, Calendar, Messages, and Granola collection as archive producers that submit acknowledged writes to one supervised loopback GBrain service, while Hermes and local clients read through the same service.
   - DOC_PATH: docs/epic/OPERATIONAL_PERSONAL_ASSISTANT_BRAIN_2026-07-11/PHASE_02_SINGLE_OWNER_CUTOVER_2026-07-12.md
   - Gate to next: All enabled source jobs refresh successfully without direct database ownership, Hermes reads through the owner, source freshness is within policy, and exactly one PGLite-holder PID survives restart and cross-client acceptance.
   - Status: implemented + verified
   - Epic-critic verdict: —

3. **Ship provenance-first personal-question retrieval**: Resolve relationship aliases, event time, source scope, lexical/vector similarity, and evidence ordering into a compact evidence packet that the assistant can summarize or abstain from.
   - DOC_PATH: docs/epic/OPERATIONAL_PERSONAL_ASSISTANT_BRAIN_2026-07-11/PHASE_03_PERSONAL_QUESTION_RETRIEVAL_2026-07-12.md
   - Gate to next: The system retrieves the last two days of the intended family conversation and the most recent prior similar discussion with source/time provenance, correct temporal ordering, and explicit gaps.
   - Status: implemented + verified
   - Epic-critic verdict: APPROVE

4. **Prove, harden, and promote the operational brain**: Exercise freshness, retrieval, writer ownership, restart, rollback, privacy, and answer-quality gates; promote the verified configuration and retire stale direct-open paths and misleading receipts.
   - DOC_PATH: docs/epic/OPERATIONAL_PERSONAL_ASSISTANT_BRAIN_2026-07-11/PHASE_04_OPERATIONAL_ACCEPTANCE_2026-07-11.md
   - Gate to next: (last sub-plan)
   - Status: implemented + verified
   - Epic-critic verdict: APPROVE

# Orchestration Log

- 2026-07-11 Goal captured from Blake’s directive to execute through completion. Existing approved North Star and its measured Bet 1 evidence were adopted as governing scope.
- 2026-07-11 Decomposition approved by explicit completion directive: the current request rejects stopping at diagnostic boundaries and authorizes the existing North Star’s implementation arc.
- 2026-07-11 Critic policy resolved from Blake’s standing newest-GPT/xhigh preference: `codex` / `gpt-5.6-sol` / `xhigh`; `codex debug models` confirmed the exact runnable model and effort.
- 2026-07-11 Live Bet 2 trace found the first active break: Messages archive refresh succeeded, but `run_fresh_sync.sh` stopped after collection and never imported or acknowledged the canonical page.
- 2026-07-11 One-off repair proved the active page can update to 76 messages; the supported import flag is `--source-id`, not `--source`. A mistaken `--source` diagnostic was invalidated and is not used as product evidence.
- 2026-07-11 Sub-plan 1 auto-plan completed. `arch_stage_gate.py ready` returned `READY next=implement-loop` for the exact Phase 01 DOC_PATH.
- 2026-07-12 Phase 01 passed live: supervised one-owner PGLite, source-bound persisted acknowledgments, fresh July 11 canonical Messages page, idempotent date-bounded replay, and authenticated hybrid retrieval.
- 2026-07-12 Phase 02 passed live: all four enabled collectors use source-bound owner reconciliation, Hermes has a 26-tool read-only MCP registration, all four schedules and the owner watchdog passed manual runs, and restart preserved one holder plus the same brain identity.
- 2026-07-12 Phase 03 passed live through the final Hermes identity: July 10 and July 11 canonical relationship evidence was present, July 8 was the latest earlier qualified match, provenance was stable, and the packet was explicitly answerable with zero gaps.
- 2026-07-12 Promoted and activated release-v5 with read-only legacy bearer scopes and explicit Messages/Gmail/Calendar/Granola source grants; authenticated Messages read passed and write was denied.
- 2026-07-12 Final retrieval specification critic returned `APPROVE`; security/code-quality critic also returned `APPROVE`. The exact Hermes evidence composition passed, all 74 tests and repository-wide lint passed, and hosted Ops/vendor CI passed.

# Decision Log

- 2026-07-11 The epic uses four proof boundaries rather than treating every North Star layer as a separate project. Native PostgreSQL remains a later backend option, not an immediate migration prerequisite, because isolated PGLite retrieval passed and the current product already exposes one loopback HTTP owner plus source-qualified `put_page` operations.
- 2026-07-11 The assistant is the answer composer for the first operational slice. GBrain must return temporally ordered, provenance-linked evidence; a separate embedded LLM answer service is not required for acceptance because current `think` is configured for an unavailable Anthropic model and the user’s standing policy favors the newest GPT agent path.
- 2026-07-11 Telegram remains waived from the prior cutover and is not silently reintroduced. The enabled-source completion set is Gmail, Calendar, Messages, and Granola.
