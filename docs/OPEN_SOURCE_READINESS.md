# Open-Source Readiness

This repository is intended to be publishable without editing tracked history.

## Automated gates

- [x] Tracked-file scanner rejects private home paths, ordinary email addresses, phone numbers, Telegram chat identifiers, private keys, and likely credential assignments.
- [x] Synthetic fixture exceptions are narrow and path-bound.
- [x] Patch files are scanned; only GitHub noreply authorship is exempted.
- [x] CI rejects tracked `data/`, `brain/`, `state/`, `logs/`, and `private/` directories.
- [x] Runtime credentials, API sessions, OAuth tokens, databases, archives, receipts, and local configuration are ignored.
- [x] Test fixtures are synthetic.
- [x] Repository uses an OSI-approved MIT license.

## Publication procedure

1. Run `pytest -q`.
2. Run `python -m gbrain_ops.privacy_scan .`.
3. Run a secret scanner over the complete Git history.
4. Verify the vendor patches apply to the pinned upstream commit and the promoted worktree is clean.
5. Confirm only generic configuration examples are tracked.
6. Create or update the remote as private first; make it public only after the remote history passes the same gates.

## Deliberately external state

Operators provide all account identifiers, source inventories, correction maps, credentials, paths, schedules, and client tokens through owner-only runtime configuration outside the repository. The repository never treats a local runtime directory as source control.
