# North Star Investigation: A World-Class Personal Assistant Brain

Status: bootstrap investigation
Date: 2026-07-11
Authoritative worklog: `NORTH_STAR_PERSONAL_ASSISTANT_BRAIN_2026-07-11.worklog.md`

## Commander's Intent

Build a personal brain that is useful every day, remains honest under partial data and partial failure, and can always show why it believes something. Optimize for trustworthy assistance, not for maximum ingestion, maximum embeddings, or an impressive database.

## North Star

For any personally relevant question or action, the assistant returns the smallest useful answer supported by temporally correct, provenance-linked evidence—or clearly says what it does not know—while keeping private data controlled, the read path continuously available, and every derived index safely rebuildable.

A brain is not a vector database. It is a local-first evidence and memory system with explicit truth boundaries, deterministic lifecycle rules, and measured retrieval quality.

## Product promise

The system should reliably answer five classes of personal-assistant questions:

1. **What happened?** Events, conversations, meetings, messages, and transactions.
2. **What is true now?** Current people, preferences, plans, commitments, and state.
3. **Why do we believe it?** Exact source evidence, time, provenance, and conflicting evidence.
4. **What should happen next?** Open commitments, deadlines, follow-ups, and likely actions.
5. **What changed?** New, superseded, contradicted, or expired information since the last known state.

## First principles

1. **Evidence precedes memory.** Raw source events are immutable evidence. Canonical memories and facts are derived, versioned projections.
2. **Unknown is a valid answer.** Missing, stale, or conflicting evidence must reduce confidence or trigger abstention—not synthesis by optimism.
3. **The read path must survive maintenance.** Ingestion, embedding, compaction, and reindexing never take the last-known-good read generation offline.
4. **One logical writer owns mutable state.** Collectors never open the serving database directly. They submit immutable envelopes to an owner-controlled queue.
5. **Every claim is source-qualified and time-qualified.** Identity is `(source, source_item_id, observed_at, version)` before any cross-source reconciliation.
6. **Derived state is disposable.** Search indexes, vectors, graphs, summaries, facts, and caches must be reproducible from evidence plus versioned transforms.
7. **Retrieval is an evaluated product.** Row counts and embedding counts are implementation details. The acceptance unit is a supported answer on a representative personal question.
8. **Graceful degradation is designed, not improvised.** If vector search fails, lexical and source browsing still work. If enrichment fails, raw evidence remains queryable.
9. **Operations are state machines.** “Live” is never one boolean. Generation, evidence coverage, index health, freshness, and client reachability are independently measured.
10. **Privacy is an egress policy.** Local storage is encrypted and access-controlled; every external model payload follows explicit minimization, provider, retention, and audit rules.

## Scope

### In scope

- Evidence ingestion and source watermarks
- Canonical item identity, versioning, deduplication, and conflict preservation
- Personal memory types and lifecycle
- Lexical, vector, entity, temporal, and source-aware retrieval
- Answer evidence, confidence, contradiction, and abstention
- Single-owner serving and multi-client access
- Online generation rebuilds and atomic promotion
- Local-first privacy and model egress controls
- Evaluation, observability, backup, restore, and disaster recovery

### Out of scope for this investigation

- Autonomous high-impact actions without separate policy and confirmation
- A global social graph or enterprise multi-tenant product
- Perfect historical completeness from inaccessible providers
- Treating model-generated summaries as evidence
- Optimizing for benchmark scale beyond one person's corpus before personal utility is proven

## Non-negotiables

- No silent data loss.
- No claim without retrievable evidence identifiers.
- No destructive migration without a tested rollback generation.
- No collector directly opening the serving database.
- No vector-only retrieval path.
- No maintenance operation that blocks all reads.
- No success report based only on rows, chunks, vectors, or process exit code.
- No private source content in public repositories, logs, metrics, or test fixtures.
- No irreversible conflation of raw evidence, canonical memory, and generated interpretation.

## Target architecture

```text
Source APIs / exports
        |
        v
Collectors (read-only, resumable, source-specific)
        |
        v
Immutable evidence envelopes + content-addressed raw archive
        |
        v
Durable ingestion queue / append ledger
        |
        v
Single writer + versioned deterministic transforms
        |
        +--------------------------+
        |                          |
        v                          v
Canonical evidence store      Derived memory store
(items, versions, provenance) (facts, preferences, commitments,
                               entities, contradictions, expiry)
        |                          |
        +------------+-------------+
                     v
          Versioned retrieval generation
          - native lexical/FTS index
          - vector index
          - temporal/entity/source indexes
          - ACL/egress metadata
                     |
                     v
         Retrieval + evidence composition service
                     |
        +------------+-------------+
        |                          |
        v                          v
Local CLI / MCP clients       Assistant answer/action layer
```

### 1. Evidence plane: the durable truth

Each source observation becomes an immutable envelope:

```text
source_id
source_item_id
source_version
observed_at
source_event_at
content_digest
raw_archive_pointer
normalized_payload
collector_version
visibility / sensitivity class
```

Properties:

- Idempotency is defined at the source-qualified version boundary.
- Corrections create new versions; they do not erase prior evidence.
- A source watermark means “complete through this cursor under this collector version,” not “we have everything forever.”
- Raw bytes are content-addressed, encrypted at rest, and independent of the serving database.

### 2. Canonical memory plane: derived and reversible

The brain maintains distinct memory types rather than one undifferentiated page corpus:

| Memory type | Examples | Lifecycle |
| --- | --- | --- |
| Episodic | meetings, messages, events | immutable evidence with updates/corrections |
| Semantic | person/company/project facts | versioned claims with support and contradiction edges |
| Preferences | travel, products, communication style | confidence-weighted; reinforced, superseded, or expired |
| Commitments | promises, tasks, deadlines, follow-ups | explicit state machine: proposed/open/done/cancelled/expired |
| Procedural | repeatable workflows and tool knowledge | versioned artifact with tests and ownership |
| Working | current-session context | bounded TTL; promoted only through explicit memory rules |

Every derived memory includes:

- supporting evidence IDs
- transform/model/version
- valid-time and observed-time
- confidence and contradiction state
- supersession/expiry policy

### 3. Storage and ownership

Use a boring native transactional database as the serving system of record for canonical and derived state. At the current scale, local PostgreSQL with native WAL, full-text search, and pgvector is the default reference implementation. PGLite/WASM may remain useful for disposable tests or portable snapshots, but it should not be the production owner for long-running, interruptible, multi-client operations.

The owner service:

- is the sole mutable database owner
- accepts collector envelopes through a durable queue/API
- serializes writes and transform jobs
- exposes read APIs/MCP concurrently
- publishes health and generation state
- supports bounded, externally supervised jobs
- never requires clients to open storage directly

### 4. Retrieval generation model

Serving reads use an immutable, accepted generation. Rebuild work occurs in a new generation:

1. Replay evidence.
2. Build canonical projections.
3. Build lexical indexes.
4. Build vectors and other derived indexes.
5. Run deterministic invariant checks.
6. Run the private retrieval evaluation suite.
7. Atomically promote the generation.
8. Preserve the prior accepted generation until rollback expiry.

A failed embedding provider, migration, or index build cannot remove lexical access to the last accepted generation.

### 5. Retrieval and answer contract

Retrieval uses multiple independent channels:

- exact source/item lookup
- lexical/FTS retrieval
- semantic/vector retrieval
- entity and relationship lookup
- temporal and recency filtering
- commitment/state queries

Candidates are fused, reranked, and returned with evidence. The answer layer must either:

- answer with citations to supporting evidence;
- present conflicting evidence explicitly; or
- abstain and name the missing source or freshness gap.

The system must distinguish **retrieval confidence** from **answer confidence**. A plausible model answer cannot repair missing evidence.

### 6. Privacy and egress

- Raw evidence and durable memory are local-first and encrypted.
- Source credentials remain isolated from model execution.
- External model calls receive the minimum necessary snippets, not broad archives.
- Sensitivity classes can prohibit remote egress entirely.
- Every remote call records provider, model, purpose, content identifiers, token volume, and retention policy without logging private text.
- Local-only operation remains a supported degraded mode.

## Operational state model

Expose a status matrix rather than `live: true`:

| Dimension | Example states |
| --- | --- |
| Evidence generation | collecting / complete-through-watermark / blocked |
| Canonical projection | building / accepted / failed |
| Lexical index | ready / stale / rebuilding / failed |
| Vector index | ready / partial / unavailable |
| Retrieval evaluation | pass / degraded / fail |
| Freshness | within-SLO / stale-by-source |
| Owner service | healthy / degraded / unavailable |
| Client acceptance | CLI / MCP / assistant individually pass or fail |
| Rollback | available / tested / expired |

A generation is promotable only when required dimensions pass. Optional sources or enrichment channels may be explicitly waived without falsifying the rest of the matrix.

## Scoreboard

Initial targets are product hypotheses until measured against real use.

### Trust and retrieval

| Metric | Initial target | Measurement |
| --- | ---: | --- |
| Evidence precision@5 | ≥ 95% | Private stratified question set |
| Evidence recall@10 | ≥ 95% for answerable questions | Gold evidence IDs |
| Supported-answer rate | ≥ 99% | Every material assertion maps to evidence |
| Confidently wrong answer rate | < 0.5% | Human adjudication on private evaluation traffic |
| Correct abstention rate | ≥ 99% | Unanswerable/conflicting question set |
| Lexical fallback availability | 100% when vector provider/index is unavailable | Fault-injection test |
| Cross-run retrieval determinism | ≥ 99% top-5 overlap for unchanged generation/query | Repeated-query test |

### Data quality and freshness

| Metric | Initial target | Measurement |
| --- | ---: | --- |
| Provenance coverage | 100% | Canonical/derived rows with evidence lineage |
| Replay equality | 100% | Archived envelopes vs accepted projection receipts |
| Duplicate canonical item rate | < 0.1% | Source-qualified identity audit |
| Enabled-source completeness | ≥ 99.9% through declared watermark | Provider inventory vs archive receipt |
| Freshness p95 | < 15 min for interactive sources; < 2 h for batch sources | Source event time to accepted evidence time |
| Contradictions silently overwritten | 0 | Version/conflict audit |

### Availability and operations

| Metric | Initial target | Measurement |
| --- | ---: | --- |
| Read availability | ≥ 99.9% monthly | Synthetic local query probe |
| Retrieval latency | p50 < 300 ms; p95 < 1.5 s | Retrieval-only, warm owner |
| Evidence-backed answer latency | p95 < 5 s | End-to-end assistant probe |
| Raw-evidence RPO | 0 after archive acknowledgement | Kill-after-ack chaos test |
| Serving-generation RTO | < 10 min by rollback; < 30 min by replay at current scale | Quarterly restore drill |
| Writer overlap | 0 | Owner lock/lease telemetry |
| False healthy status | 0 | Status matrix vs independent probes |
| Unbounded maintenance jobs | 0 | Supervisor/job receipt audit |

### Privacy and cost

| Metric | Initial target | Measurement |
| --- | ---: | --- |
| Unauthorized private egress | 0 | Egress policy audit |
| External calls with provenance receipt | 100% | Provider-call ledger |
| Secrets or private text in public/log artifacts | 0 | Automated scanners + review |
| Assumed monthly model budget | ≤ $30 until utility is proven | Cost ledger; product decision, not a hard invariant |

## Quant model and sanity checks

### Measured now

- Corpus: 10,285 pages and 28,165 chunks.
- All chunks currently report embeddings and zero stale chunks.
- Plain lexical search is not reliably returning expected results.
- One hybrid query passed during acceptance but later repetition was inconsistent.
- A long PGLite/WASM maintenance process became CPU-bound without durable file progress and required external termination.
- Current collectors are independent database writers; schedule staggering reduces but does not eliminate overlap risk.

### Assumptions to test

- Approximately 30 meaningful assistant queries per day (900/month).
- Native PostgreSQL removes the uninterruptible PGLite/WASM maintenance failure mode at this scale.
- A representative private evaluation set can predict real personal utility.
- A local owner service can satisfy latency targets on existing hardware.

### Statistical bounds

- A 200-query suite has about an 86.6% chance of observing at least one defect with a true 1% incidence.
- About 299 independent trials are needed for a 95% chance of observing a 1% defect at least once.
- Therefore bootstrap with a stratified diagnostic set, then grow to at least 300 adjudicated questions before claiming a sub-1% failure rate.
- At 99.9% monthly read availability, the downtime budget is about 43.2 minutes per 30-day month. Maintenance must normally consume none of it because readers stay on the prior generation.

## Ground-truth anchors

1. Row/chunk/vector counts can all be correct while retrieval is unusable.
2. Cooperative cancellation cannot interrupt a synchronous runtime that blocks its event loop.
3. A progress denominator mixing chunks, pages, and dynamic batches is not an acceptance oracle.
4. A successful process exit can hide per-item failures.
5. Time-staggered direct writers are not equivalent to one logical owner.
6. Model/schema compatibility is part of generation identity and must be validated before promotion.
7. A receipt that disagrees with current process/database state is historical evidence, not operational truth.

## Ranked hypotheses

| Rank | Hypothesis | Current confidence | Fastest falsifier |
| ---: | --- | ---: | --- |
| 1 | Recovery replay did not rebuild all lexical/search invariants, so stored and embedded content is not reliably retrievable. | 0.90 | Trace 20 known evidence items through envelope → canonical row → chunk → lexical vector → lexical/vector/hybrid result. |
| 2 | PGLite/WASM is the wrong production ownership boundary for interruptible long-running jobs and multi-client availability. | 0.85 | Replay the same fixed sample into local native PostgreSQL and compare kill behavior, indexing, and retrieval under identical workload. |
| 3 | Direct-writer collectors are the structural cause of lock fragility; time staggering only masks it. | 0.95 | Run concurrent collector/query chaos against a single-owner queue and against direct writers; compare lock errors and read availability. |
| 4 | Current acceptance gates optimize implementation counts rather than personal-question utility. | 0.95 | Evaluate both current and candidate generations on a private evidence-labeled question set. |
| 5 | Status and receipts lack a canonical state machine, causing stale or contradictory operational claims. | 0.90 | Compare every current status artifact with independent process, database, index, and query probes. |
| 6 | A small local-first evidence model plus evaluated retrieval will deliver more utility than additional graph/agent complexity. | 0.70 | Disable optional enrichment and compare answer quality/cost on the same evaluation set. |

## First iteration plan

Run one highest-information bet at a time.

### Bet 1 — Evidence-to-answer retrieval trace

**Question:** Is current retrieval failure caused by missing index material, filtering/routing, or answer-layer behavior?

**Brutal test:** Select 20 known items per enabled source plus 20 known-absent controls. For each known item, trace:

```text
archive envelope
→ canonical item/version
→ chunk text
→ lexical index row
→ vector row/signature
→ exact lookup
→ lexical top-10
→ vector top-10
→ hybrid top-10
→ evidence-backed answer
```

**Pre-committed decision rule:**

- Missing before canonicalization: collector/replay defect.
- Canonical/chunk exists but lexical row is absent: index-build defect.
- Index row exists but lexical lookup misses: query/filter defect.
- Lexical/vector retrieval works but answer fails: fusion/answer-contract defect.
- More than 5% known-item miss rate blocks all further architecture promotion work.

**Time budget:** 4 hours to build the oracle and identify the first failing layer.

### Bet 2 — Native PostgreSQL production-boundary spike

**Question:** Does moving the owner boundary to native PostgreSQL remove the maintenance and concurrency failure class without sacrificing local-first operation?

**Brutal test:** Replay a fixed, non-private synthetic corpus plus a local hashed sample into PGLite and native PostgreSQL. Run identical ingestion, lexical/vector build, concurrent reads, SIGTERM/SIGKILL, restart, and rollback tests.

**Pre-committed decision rule:** Adopt native PostgreSQL as the target owner if it:

- preserves replay equality;
- passes lexical/vector retrieval invariants;
- keeps reads available during rebuild;
- terminates bounded jobs within 10 seconds or survives external kill without corruption;
- restores service within 10 minutes;
- stays within 2× the current warm-query latency at this scale.

**Time budget:** 1 day after Bet 1 localizes retrieval.

### Bet 3 — Private personal-question evaluation harness

**Question:** Does the architecture answer the questions that matter rather than merely indexing content?

**Brutal test:** Create a private, evidence-labeled, source-stratified diagnostic set of 60 questions: 10 each for events, people/facts, commitments, preferences, conflicts/updates, and intentionally unanswerable questions. Expand to at least 300 after the harness proves useful.

**Pre-committed decision rule:** No generation is promoted unless the diagnostic set reaches:

- ≥ 95% evidence recall@10;
- ≥ 95% evidence precision@5;
- 100% citation presence for material assertions;
- ≥ 95% correct abstention in the bootstrap set;
- no regression greater than 2 percentage points versus the accepted generation.

**Time budget:** 1 day for the first 60-question harness after Bet 1.

## Decisions deferred until evidence

- Native PostgreSQL versus another native transactional engine.
- Local-only embeddings versus approved remote embedding providers.
- Which derived graph/fact/enrichment features survive utility evaluation.
- Whether a remote encrypted replica is worth its additional threat surface.
- Final freshness and cost targets after one month of measured personal usage.

## Exit criteria for the investigation

The investigation can become a fixed architecture delivery plan when:

1. Bet 1 identifies and proves the current first failing retrieval layer.
2. The storage/owner spike settles the PGLite-versus-native boundary.
3. The private evaluation harness can distinguish a good generation from a broken one.
4. The target state can be decomposed into independently provable delivery phases without unresolved ownership or truth-model questions.

At that point, route to an architecture epic with ordered sub-plans for evidence ledger, single-owner service, retrieval generation/evaluation, client migration, and recovery/operations.
