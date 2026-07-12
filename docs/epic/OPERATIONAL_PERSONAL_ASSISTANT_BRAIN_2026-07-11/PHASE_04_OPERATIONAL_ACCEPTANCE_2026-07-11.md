---
title: "GBrain - Operational Acceptance and Promotion"
date: 2026-07-12
status: active
implementation_status: complete
verification_status: pending
fallback_policy: forbidden
owners: [Blake Messer]
reviewers: [Arch Epic critic]
---

# Outcome

Promote the acknowledged-ingestion, one-owner, provenance-first brain only after runtime, retrieval, privacy, recovery, least-privilege, and publication gates pass.

# Acceptance gates

- [x] Exactly one PGLite holder and one loopback owner listener.
- [x] Active immutable promoted runtime is reproducible from the public patch manifest.
- [x] Messages, Gmail, Calendar, and Granola use acknowledged owner reconciliation.
- [x] Hermes credential is read-only, has an explicit approved source allow-list, can read Messages, and is denied writes.
- [x] Exact July 10–11 relationship retrieval is answerable with canonical provenance.
- [x] July 8 is selected as the latest earlier relationship-and-topic-qualified conversation.
- [x] Missing evidence yields explicit abstention; owner failures propagate as operational failures.
- [x] Owner restart preserves brain identity and evidence provenance.
- [x] Python tests, Ruff, shell syntax, compilation, privacy scan, Gitleaks, runtime typecheck/check gates, and focused owner/auth tests pass.
- [ ] Repair critics approve the final retrieval implementation.
- [ ] Hosted CI passes the committed sanitized tree.
- [ ] Sanitized commit is pushed to the public repository.

# Rollback

The sealed pre-cutover database remains untouched. Runtime rollback changes only the activated immutable runtime and launchd launcher, then requires health, single-holder, authenticated read, and authenticated write-ack verification. Private credentials, relationship mappings, evidence packets, receipts, and logs stay outside Git.
