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

Run **Bet 1 — Evidence-to-answer retrieval trace**. Do not begin the storage migration or owner-service build until the test identifies the first failing retrieval layer. After Bet 1, run the native PostgreSQL spike and the private personal-question evaluation harness before freezing a delivery architecture.
