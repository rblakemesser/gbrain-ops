# Contributing

1. Use synthetic fixtures and reserved example domains only.
2. Keep runtime state and personal configuration outside Git.
3. Represent commands as argv arrays, never shell strings.
4. Run `pytest -q`, `python -m gbrain_ops.privacy_scan .`, and Gitleaks before committing.
5. When changing vendor patches, rebuild from the pinned upstream commit and run vendor compatibility tests.
6. Keep patch rationale, dependency order, and verification commands documented.
