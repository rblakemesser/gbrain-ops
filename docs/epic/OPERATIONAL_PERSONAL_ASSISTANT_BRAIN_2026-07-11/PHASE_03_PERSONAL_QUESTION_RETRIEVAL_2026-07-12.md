---
title: "GBrain - Provenance-First Personal Question Retrieval - Architecture Plan"
date: 2026-07-12
status: active
implementation_status: complete
verification_status: passed
fallback_policy: forbidden
owners: [Blake Messer]
reviewers: [Arch Epic critic]
doc_path: docs/epic/OPERATIONAL_PERSONAL_ASSISTANT_BRAIN_2026-07-11/PHASE_03_PERSONAL_QUESTION_RETRIEVAL_2026-07-12.md
approved_by: Blake Messer
approved_at: 2026-07-12
approval_note: Auto-approved by the standing directive to execute the approved North Star through operational completion.
---

<!-- arch_skill:block:auto_plan_receipts:start -->
{
  "version": 1,
  "digest": "sha256:dacbb2a53a7813a3d0313c3b5f90e3fd55dbe61bd27fb34494386397da42aa90",
  "receipts": [
    {
      "stage": "research",
      "command": "research",
      "status": "complete",
      "started_at": "2026-07-12T02:30:32Z",
      "command_ref_hash": "sha256:5ad5dc9efcb3c7d0d42e1d9014e3ee66fd24b8d2f1c85eef2c5ee96543e05c96",
      "doc_hash_before": "sha256:241a58e79ae8733a22316735bce1ed661921be3364f4015e069e21e96aaf17ae",
      "completed_at": "2026-07-12T02:30:32Z",
      "doc_hash_after": "sha256:f38909cb5a14f205eb9213a8373152e33aedbb5c32fbe572bc2224562c83945e"
    },
    {
      "stage": "deep-dive-pass-1",
      "command": "deep-dive",
      "status": "complete",
      "started_at": "2026-07-12T02:30:33Z",
      "command_ref_hash": "sha256:a70fc06739f4a02466585892607a7dccc5476123c3c4af9c01fad587284ae649",
      "doc_hash_before": "sha256:f38909cb5a14f205eb9213a8373152e33aedbb5c32fbe572bc2224562c83945e",
      "completed_at": "2026-07-12T02:31:46Z",
      "doc_hash_after": "sha256:5e28f3cc1663c175a13799b11e28869463f35fbb5c6d23b0b9bf6ac0c04fedf9"
    },
    {
      "stage": "deep-dive-pass-2",
      "command": "deep-dive",
      "status": "complete",
      "started_at": "2026-07-12T02:31:47Z",
      "command_ref_hash": "sha256:a70fc06739f4a02466585892607a7dccc5476123c3c4af9c01fad587284ae649",
      "doc_hash_before": "sha256:5e28f3cc1663c175a13799b11e28869463f35fbb5c6d23b0b9bf6ac0c04fedf9",
      "completed_at": "2026-07-12T02:32:43Z",
      "doc_hash_after": "sha256:1577626a247182a4d6697f2ea93aeebe8eab199171686d26af6591173220242d"
    },
    {
      "stage": "phase-plan",
      "command": "phase-plan",
      "status": "complete",
      "started_at": "2026-07-12T02:32:43Z",
      "command_ref_hash": "sha256:1ce4687beab44819933a8a404a02b8e1345823a7a996f7d651f3dd25a0c54aa3",
      "doc_hash_before": "sha256:1577626a247182a4d6697f2ea93aeebe8eab199171686d26af6591173220242d",
      "completed_at": "2026-07-12T02:32:44Z",
      "doc_hash_after": "sha256:45767e938c6647685e9afba4961000d9071136c31eb5f9d542e1333aa361308f"
    },
    {
      "stage": "consistency-pass",
      "command": "consistency-pass",
      "status": "complete",
      "started_at": "2026-07-12T02:32:44Z",
      "command_ref_hash": "sha256:7489e588432f0d742aeb3fc2f19649224b462dc9cd95ad442675037b1c616137",
      "doc_hash_before": "sha256:45767e938c6647685e9afba4961000d9071136c31eb5f9d542e1333aa361308f",
      "completed_at": "2026-07-12T02:32:44Z",
      "doc_hash_after": "sha256:46f3e03163df3f3a58c4d4352cb318b72212ecbb61317f986700c00072ce4324"
    }
  ]
}
<!-- arch_skill:block:auto_plan_receipts:end -->


# TL;DR

- **Outcome:** Relationship-aware, time-bounded, provenance-first evidence packets for personal conversation questions through the sole GBrain owner.
- **Problem:** Broad semantic ranking does not enforce relationship, date window, latest-prior ordering, or abstention.
- **Approach:** Resolve a private alias, read exact canonical dates, compose hybrid candidates, validate relationship/topic concepts, and choose the newest qualifying prior page.
- **Non-negotiables:** no direct PGLite/archive fallback; no private identifiers in Git; every answer has source/date provenance or abstains.

<!-- arch_skill:block:planning_passes:start -->
<!--
arch_skill:planning_passes
deep_dive_pass_1: done 2026-07-12
external_research_grounding: not required — live product behavior and canonical private evidence settle the design
deep_dive_pass_2: done 2026-07-12
recommended_flow: deep dive -> deep dive again -> phase plan -> implement
-->
<!-- arch_skill:block:planning_passes:end -->

# 1) Executive Summary

Build a deterministic owner-only evidence retriever. It resolves a private relationship alias, fetches source-qualified date pages, extracts only relationship-relevant sections, composes multiple hybrid queries, then chooses the chronologically latest earlier candidate that contains both relationship evidence and the requested topic concepts. It returns evidence plus source/date provenance and explicit gaps; Hermes remains the answer composer.

# 2) Scope, Goals, and Non-Goals

## Goals

- Resolve `my mom` without exposing her source identifier publicly.
- Retrieve July 10–11 Messages evidence directly from the one owner.
- Find the latest earlier similar childcare/travel-coverage conversation by semantic candidates followed by deterministic relation/topic/date validation.
- Return source, slug, date, scores, and uncertainty/gaps.
- Abstain when relationship, date evidence, or prior-similarity evidence is absent.

## Non-Goals

- No general-purpose entity graph migration.
- No embedded answer LLM.
- No raw PGLite access or archive fallback.
- No public relationship identifiers or conversation contents.

<!-- arch_skill:block:research_grounding:start -->
# 3) Research Grounding

Live owner search and query are source-scoped and restart-stable. Direct `get_page` returns complete canonical Markdown. Hybrid probes show July 8 in the candidate set for multiple childcare/travel formulations, while broad single-query ranking alone is not chronologically correct. The archive proves the private participant marker is stable and redacted, so it is safe to store only in a private `0600` relationship file.
<!-- arch_skill:block:research_grounding:end -->

<!-- arch_skill:block:current_architecture:start -->
# 4) Current Architecture

Hermes can call the owner MCP, but it must manually combine relationship resolution, direct date reads, semantic candidates, temporal ordering, and evidence composition. Broad `query` ranks by similarity, not latest-prior semantics, and `think` lacks the required source/date scope.
<!-- arch_skill:block:current_architecture:end -->

<!-- arch_skill:block:target_architecture:start -->
# 5) Target Architecture

`question -> private alias resolution -> exact date slugs -> canonical get_page -> relationship section extraction -> multi-query hybrid candidates -> date-before-window filter -> relation + concept validation -> newest valid prior -> evidence packet -> Hermes summary/abstention`

The relationship file stores display labels and redacted participant markers outside Git. Candidate concepts use explicit travel, care, and coverage vocabularies. A prior page qualifies only when its relationship section has travel evidence and care/coverage evidence. Chronology, not vector rank, chooses among qualifying pages.
<!-- arch_skill:block:target_architecture:end -->

# 6) System-Wide Impact

- Adds a public retrieval library/CLI but no private data.
- Adds one private relationship mapping.
- Keeps all reads on the owner MCP.
- Establishes a provenance packet contract Hermes can summarize.

<!-- arch_skill:block:call_site_audit:start -->
## 6.1 Call-Site Audit

| Call site | Change | Proof |
|---|---|---|
| Hermes personal question | use owner evidence packet | exact acceptance query |
| Relationship resolution | private alias file | no identifier in Git/output summary |
| Recent evidence | direct source/date pages | canonical hashes and slugs |
| Prior similarity | multi-query candidates + newest valid relation/topic page | July 8 selected and explained |
| Missing evidence | explicit gaps/abstention | negative tests |
<!-- arch_skill:block:call_site_audit:end -->

<!-- arch_skill:block:phase_plan:start -->
# 7) Phased Implementation Plan

## Phase 1 — Evidence contract and private relationship map

- [x] Add typed evidence packet structures and alias validation.
- [x] Install the private `my mom` marker map with mode `0600`.
- [x] Add section extraction and canonical source checks.

## Phase 2 — Temporal and similarity retrieval

- [x] Fetch inclusive date pages by canonical slug.
- [x] Run multiple owner hybrid queries.
- [x] Filter candidates before the window, validate relationship/topic concepts, and pick the newest.
- [x] Report gaps instead of inventing evidence.

## Phase 3 — Acceptance and Hermes operating path

- [x] Run the July 10–11 / prior-similarity acceptance query.
- [x] Verify July 8 is selected as the latest earlier similar discussion.
- [x] Verify packet provenance survives owner restart.
- [x] Document the owner-only operating path for Hermes.
<!-- arch_skill:block:phase_plan:end -->

# 8) Verification Strategy

- Unit tests for alias resolution, section extraction, date parsing, concept validation, ordering, and abstention.
- Live owner packet generation without private identifiers in public files.
- Manual evidence review against July 10, July 11, and July 8 canonical pages.
- Restart and rerun the same packet; compare provenance slugs and dates.

# 9) Deployment and Rollback

Deploy the library/CLI first, then install the private mapping. Rollback removes only the private mapping and CLI call path; it does not modify the owner, database, archives, or receipts. If evidence is incomplete, the assistant abstains and leaves current canonical data untouched.

# 10) Decision Log

## Planning Receipts

- Research reviewed: live owner, canonical Messages pages, source scoping, and broad hybrid ranking behavior establish the retrieval seam.
- Deep-dive pass 1 reviewed: private alias, direct-date reads, section extraction, and chronology constraints are decision-complete.
- Deep-dive pass 2 reviewed: multi-query candidate composition, concept validation, provenance, and abstention are decision-complete.
- Phase plan reviewed: evidence contract, temporal retrieval, acceptance, restart proof, and Hermes operating path are ordered.
- Consistency pass reviewed: North Star, architecture, call sites, phases, verification, privacy, and rollback agree.
<!-- arch_skill:block:consistency_pass:start -->
## Consistency Pass — 2026-07-12

- North Star, one-owner contract, relationship privacy, chronology, provenance, and abstention align.
- Decision-complete: yes
- Unresolved decisions: none
- Unauthorized scope cuts: none
- Decision: proceed to implement? yes
<!-- arch_skill:block:consistency_pass:end -->

## 2026-07-12 - Use deterministic evidence composition

- **Decision:** Let the owner retrieve evidence and Hermes compose the answer; do not add another LLM service.
- **Consequences:** Acceptance remains inspectable, current-model compatible, and able to abstain explicitly.

## Implementation and verification log

- Implemented private alias resolution, canonical source/slug/hash validation, header-only relationship section selection, boundary-aware topic qualification, deterministic latest-prior ordering, explicit `answerable`/`abstain` disposition, and privacy-safe summary output.
- Added the owner-only Hermes consumer contract in `docs/PERSONAL_EVIDENCE_RETRIEVAL.md`.
- Live retrieval through the final Hermes read-only token returned both requested dates, selected July 8 as the latest qualifying prior date, reported zero gaps, and stored its private evidence packet mode `0600`.
- Verified the Hermes token has only `read`, can read the Messages source through its explicit source allow-list, and is denied a write operation.
- Focused adversarial retrieval tests, the full Python suite, Ruff, privacy scan, Gitleaks, runtime typecheck/check gates, and focused runtime owner/auth tests passed.
