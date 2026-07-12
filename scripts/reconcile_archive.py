#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from gbrain_ops.archive_reconcile import run_reconcile
from gbrain_ops.owner_client import OwnerClientError


def main() -> int:
    parser = argparse.ArgumentParser(description="Submit deterministic archive pages to the single GBrain owner")
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--receipt-root", type=Path, required=True)
    parser.add_argument("--glob", default="**/*.md")
    parser.add_argument("--modified-within-days", type=float)
    parser.add_argument("--dated-within-days", type=int)
    parser.add_argument("--force-verify", action="store_true")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    try:
        summary = run_reconcile(
            credentials_path=args.credentials.expanduser(),
            root=args.root.expanduser(),
            receipt_root=args.receipt_root.expanduser(),
            pattern=args.glob,
            modified_within_days=args.modified_within_days,
            dated_within_days=args.dated_within_days,
            force_verify=args.force_verify,
        )
    except (OwnerClientError, OSError, ValueError):
        print(json.dumps({"status": "error", "error": "archive reconciliation failed"}, sort_keys=True))
        return 1
    payload = summary.as_dict()
    if args.summary_only:
        payload.pop("receipts", None)
    print(json.dumps({"status": "ok", **payload}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
