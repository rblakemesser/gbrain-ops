# North Star Investigation Worklog

Investigation doc: `NORTH_STAR_PERSONAL_ASSISTANT_BRAIN_2026-07-11.md`

## 2026-07-11 — Bootstrap

### What changed

- Established a first-principles North Star for a local-first personal assistant brain.
- Reframed the product from “a populated vector database” to “an evidence-backed memory and retrieval system.”
- Defined a status matrix, quantitative scoreboard, ranked hypotheses, and three brutal tests.
- Identified the evidence-to-answer retrieval trace as the first and highest-information bet.

### Evidence inspected

- Current control-plane architecture and repository contracts.
- Replacement generation counts and post-cutover acceptance receipt.
- Current embedding implementation, including stale counting, progress reporting, per-page error handling, and cancellation.
- Current retrieval probes for lexical and hybrid query paths.
- Source schedule ownership and direct-writer topology.

### Commands and probes

```text
gbrain stats
gbrain embed --stale --dry-run
gbrain search <representative-term>
gbrain query <representative-question>
process and database-owner inspection
source schedule inventory
embedding implementation source inspection
```

### Raw results

- Replacement generation reports 10,285 pages, 28,165 chunks, and 28,165 embeddings.
- Stale dry-run reports zero stale chunks.
- Plain lexical retrieval returned no results for representative common terms.
- A hybrid query returned evidence during acceptance but was not consistently repeatable afterward.
- Embedding progress exceeded 100% because its callback mixes a stale-chunk estimate with cumulative processed pages and a batch-dependent key count.
- One long PGLite/WASM maintenance process became CPU-bound without database/WAL modification progress, did not stop cooperatively, and required external termination.
- A bounded completion pass succeeded but logged one transient provider failure; later stale verification reported zero remaining chunks.
- Source jobs are enabled with staggering, but each remains an independent database opener.
- Historical and current status receipts can disagree because they are snapshots rather than one canonical operational state machine.

### Conclusion

The immediate failure is not missing embeddings. The first failing layer is likely between canonical/chunk persistence and reliable retrieval. The broader architectural risk is the use of a portable single-process WASM database as both the maintenance runtime and the multi-client production boundary.

The correct next move is not a broad rewrite. It is a 100-item evidence-to-answer trace (20 known items per enabled source plus controls) with a pre-committed layer-by-layer decision rule. That test will identify whether the first implementation phase belongs in replay/index repair, retrieval routing, or the answer layer.

### Current ranked belief

1. Search invariants were not fully rebuilt or validated during recovery.
2. PGLite/WASM is unsuitable as the long-lived production owner boundary.
3. Independent direct writers make lock contention structural.
4. Existing acceptance optimizes counts instead of evidence-backed personal utility.
5. Operational state lacks one canonical truth source.

### Next-step recommendation

Run **Bet 1 — Isolated unique-nonce keyword oracle** before the broader evidence-to-answer trace. Do not begin the storage migration or owner-service build until the isolated oracle distinguishes index/query failure from ownership or current-corpus effects. After that, run the real-item trace, then the combined backend and writer-ownership bake-off before freezing a delivery architecture.

## 2026-07-11 — Independent North Star reconciliation

### What changed

Three independent analyses reviewed the first-principles architecture, quantitative scoreboard, and current failure map. Their non-duplicative findings were incorporated into the controller:

- memory is explicitly governed forgetting, correction, and deletion;
- scoreboard values are labeled measured, derived, assumed, or unknown;
- hard gates cannot be averaged away by a quality score;
- personal utility now includes opportunity completion, time saved, correction burden, usefulness, and proactive precision;
- the first bet is now a 60-minute isolated unique-nonce keyword oracle;
- the backend spike now compares both storage engines and direct-writer versus single-owner topology.

### Raw result

The independent analyses agreed on the central model: durable evidence and provenance are the product foundation; indexes are disposable generations; retrieval is the acceptance contract; and direct writers plus a PGLite/WASM production boundary are hypotheses to test rather than assumptions to preserve.

### Conclusion

The North Star remains stable. The next move became narrower and higher-information: prove whether keyword failure exists in an isolated deterministic fixture before tracing the full personal corpus or committing to PostgreSQL.

## 2026-07-11 — Bet 1: isolated unique-nonce keyword oracle

### What changed

- Added reusable `scripts/run_keyword_oracle.py` and deterministic unit coverage.
- Froze 30 synthetic pages across three source-qualified fixtures.
- Reused the same ten slugs across all three sources to test source isolation while assigning one unique nonce to every page.
- Forced `search.mcp_keyword_only=true` so the CLI could not silently substitute vector or hybrid retrieval.
- Added raw SQL FTS, CLI positive/negative, cold/warm reopen, controlled-writer, and post-writer recovery probes.
- Kept all fixture content and PGLite state in an isolated `GBRAIN_HOME`; the active personal generation was not opened by the oracle.

### Invalid pilot

The first pilot receipt is retained as invalid evidence. Its raw SQL query referenced a nonexistent `sources.status` column, so all raw probes failed before testing retrieval. The harness was corrected to use the actual `sources.archived_at` visibility field and rerun in a fresh isolated generation. No conclusion uses the invalid pilot.

### Valid artifact

```text
$HOME/.gbrain/recovery/oracles/20260711T225811Z/receipt.json
fixture manifest SHA-256: 67f0404de014a2387678b5eb4a8bbedf08ed8238e404c16f338d3fc19133d7db
```

### Raw measured results

| Phase | Probes | Failures/timeouts | p50 | p95 |
| --- | ---: | ---: | ---: | ---: |
| Raw FTS cold, positive + wrong-source negative | 60 | 0 | 118 ms | 129 ms |
| Raw FTS warm reopen, positive + wrong-source negative | 60 | 0 | 118 ms | 132 ms |
| CLI keyword-only cold, positive + wrong-source negative | 60 | 0 | 526.5 ms | 549 ms |
| CLI keyword-only warm reopen, positive + wrong-source negative | 60 | 0 | 525 ms | 541 ms |
| CLI while one controlled writer owned PGLite | 30 | 30 bounded timeouts | 4,016 ms | 4,036 ms |
| CLI after the controlled writer exited cleanly | 3 | 0 | 530 ms | 530 ms |

The isolated generation contained exactly 30 pages and 30 chunks. The controlled owner acquired the GBrain lock, performed one harmless config mutation, and exited `0`. Every bounded external CLI reader failed while ownership was held; sampled readers immediately succeeded after owner release.

The active personal generation was independently rechecked afterward and retained its prior identity and counts: 10,285 pages, 28,165 chunks, and 28,165 embeddings.

### Decision

```text
classification = ownership_or_isolation_defect
```

Bet 1 falsifies a generic fresh-schema, FTS-trigger, and keyword-only CLI defect. It confirms that the current direct multi-process topology cannot provide concurrent reads while a GBrain writer owns PGLite. Schedule staggering reduces collision probability but cannot satisfy the North Star read-availability contract.

Bet 1 does **not** explain why the active replacement corpus returns no expected results when no writer is present. That failure is now more likely current-generation replay/index/filter state than a universal keyword implementation defect.

### Updated beliefs

1. Direct-writer ownership is a measured structural defect, not merely a suspected lock risk.
2. Fresh PGLite FTS and keyword-only CLI retrieval are deterministic for the synthetic corpus.
3. The active replacement requires an evidence-to-answer trace before choosing repair versus rebuild.
4. A single-owner service is required regardless of whether PGLite or PostgreSQL ultimately wins the backend bake-off.

### Next decision rule

Run **Bet 2 — Evidence-to-answer retrieval trace** against known active-corpus items. Stop at the first missing layer. Do not migrate storage yet: if canonical chunks lack lexical vectors, repair/rebuild the generation; if lexical rows exist but source-scoped retrieval misses, repair the query/filter path; if retrieval succeeds but answers fail, repair fusion and evidence composition.
