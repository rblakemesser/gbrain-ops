# Privacy Model

This repository contains source code, schemas, synthetic fixtures, and deployment templates only.

Never commit credentials, OAuth tokens, API sessions, databases, archives, inventories, manifests, receipts, logs, recovery bundles, source content, account identifiers, or machine-specific configuration. These may contain sensitive metadata even when message bodies are omitted.

Operator configuration must refer to secrets through owner-only files or environment variables. Generated receipts belong under the configured private state root and must use paths relative to a declared root.

Before publishing or releasing, run the tracked-file privacy scanner, the test suite, and Gitleaks over full history. A scanner exception must be narrow, documented, and limited to an explicitly synthetic fixture or GitHub noreply authorship.
