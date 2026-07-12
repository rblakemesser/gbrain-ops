# Personal evidence retrieval contract

Hermes personal-question retrieval is owner-only. It must not open PGLite, read source archives as final evidence, or treat collector receipts as answer evidence.

## Required path

1. Resolve the requested relationship from the private mode-`0600` relationship file outside Git.
2. Use a source-scoped owner credential and the authenticated loopback owner MCP endpoint.
3. Run `scripts/retrieve_personal_evidence.py` with an exact inclusive date range and one or more candidate queries.
4. Consume the evidence packet only when both conditions hold:
   - `answerable` is `true`;
   - `disposition` is `answerable`.
5. If the CLI exits `3`, or the packet says `disposition: abstain`, do not synthesize a factual answer. Report the packet's privacy-safe gaps.
6. Treat any other nonzero exit or `EvidenceRetrievalError` as an operational retrieval failure, not as absent evidence.

Example shape (private paths and query values intentionally omitted):

```bash
PYTHONPATH=src python3 scripts/retrieve_personal_evidence.py \
  --credentials /private/source-owner-credential.json \
  --relationships /private/relationships.json \
  --relationship 'relationship alias' \
  --start YYYY-MM-DD \
  --end YYYY-MM-DD \
  --candidate-query 'topic query' \
  --summary-only
```

## Evidence and privacy rules

- Canonical pages must match the configured source and requested slug and carry a valid SHA-256 content hash.
- Relationship identity comes only from the canonical section heading, never from marker text quoted in a message body.
- The latest prior result must be earlier than the requested window, match every required topic group, and be selected by date with a stable slug tie-break.
- Full evidence packets are private operational artifacts. `--summary-only` removes sections and candidate-query text and returns only a query count.
- Hermes answers may summarize supported themes and cite source plus dates, but must not expose relationship markers, participant identifiers, raw conversations, credentials, or private paths.
