from __future__ import annotations

import argparse
import json
from pathlib import Path

from .activation import activate_vendor_runtime
from .config import load_config
from .privacy_scan import scan_repository
from .recovery import build_source_inventory, recovery_readiness
from .vendor import apply_patch_series


def main() -> int:
    parser = argparse.ArgumentParser(prog="gbrain-ops")
    sub = parser.add_subparsers(dest="command", required=True)
    privacy = sub.add_parser("privacy-check", help="fail if tracked/open-source files contain private material")
    privacy.add_argument("path", nargs="?", default=".")
    promote = sub.add_parser("vendor-promote", help="apply the pinned carried-patch series to a disposable worktree")
    promote.add_argument("--vendor", required=True)
    promote.add_argument("--manifest", default="patches/gbrain/manifest.json")
    promote.add_argument("--output", required=True)
    config = sub.add_parser("config-check", help="expand and validate an operator configuration")
    config.add_argument("path", type=Path)
    inventory = sub.add_parser("inventory", help="build a deterministic private source inventory")
    inventory.add_argument("--source-id", required=True)
    inventory.add_argument("--root", required=True, type=Path)
    inventory.add_argument("--output", type=Path)
    readiness = sub.add_parser("recovery-readiness", help="evaluate inventory completeness and replay equality")
    readiness.add_argument("--inventory", action="append", type=Path, required=True)
    readiness.add_argument("--required-source", action="append", required=True)
    readiness.add_argument("--imported", type=int, required=True)
    readiness.add_argument("--replayed", type=int, required=True)
    readiness.add_argument("--output", type=Path)
    activate = sub.add_parser("activate-runtime", help="atomically point a local gbrain wrapper at a promoted runtime")
    activate.add_argument("--runtime", type=Path, required=True)
    activate.add_argument("--bun", type=Path, required=True)
    activate.add_argument("--wrapper", type=Path, required=True)
    activate.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "privacy-check":
        findings = scan_repository(Path(args.path))
        for finding in findings:
            print(f"{finding.path}:{finding.line}: {finding.rule}")
        return 1 if findings else 0
    if args.command == "vendor-promote":
        receipt = apply_patch_series(Path(args.vendor), Path(args.manifest), Path(args.output))
        print(f"promoted {receipt['base_commit']} -> {receipt['head_commit']}")
        return 0
    if args.command == "config-check":
        config_value = load_config(args.path)
        print(json.dumps({"status": "valid", "sources": [item["id"] for item in config_value["sources"]]}, indent=2))
        return 0
    if args.command == "inventory":
        value = build_source_inventory(args.source_id, args.root)
        rendered = json.dumps(value, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered)
        else:
            print(rendered, end="")
        return 0
    if args.command == "recovery-readiness":
        inventories = [json.loads(path.read_text()) for path in args.inventory]
        value = recovery_readiness(
            inventories,
            required_sources=args.required_source,
            imported=args.imported,
            replayed=args.replayed,
        )
        rendered = json.dumps(value, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered)
        else:
            print(rendered, end="")
        return 0 if value["status"] == "ready" else 3
    if args.command == "activate-runtime":
        receipt = activate_vendor_runtime(
            runtime=args.runtime,
            bun=args.bun,
            wrapper=args.wrapper,
            receipt_path=args.receipt,
        )
        print(json.dumps({"status": "activated", "runtime_head": receipt["runtime_head"]}, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
