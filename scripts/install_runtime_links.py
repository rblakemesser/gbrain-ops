#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

MAPPING = {
    "email-to-brain": {
        "email_collector.py": "adapters/gmail/email_collector.py",
        "classify_archive.py": "adapters/gmail/classify_archive.py",
        "promote_archive.py": "adapters/gmail/promote_archive.py",
        "run_fresh_sync.sh": "adapters/gmail/run_fresh_sync.sh",
    },
    "calendar-to-brain": {
        "calendar_collector.py": "adapters/calendar/calendar_collector.py",
        "classify_archive.py": "adapters/calendar/classify_archive.py",
        "promote_archive.py": "adapters/calendar/promote_archive.py",
        "run_fresh_sync.sh": "adapters/calendar/run_fresh_sync.sh",
    },
    "messages-to-brain": {
        "messages_collector.py": "adapters/messages/messages_collector.py",
        "run_fresh_sync.sh": "adapters/messages/run_fresh_sync.sh",
    },
    "granola-to-brain": {
        "granola_collector.py": "adapters/granola/granola_collector.py",
        "run_fresh_sync.sh": "adapters/granola/run_fresh_sync.sh",
    },
    "telegram-to-brain": {
        "egress_policy.py": "adapters/telegram/egress_policy.py",
        "render_markdown.py": "adapters/telegram/render_markdown.py",
        "telegram_collector.py": "adapters/telegram/telegram_collector.py",
        "telegram_desktop_import.py": "adapters/telegram/telegram_desktop_import.py",
        "telegram_event_model.py": "adapters/telegram/telegram_event_model.py",
        "telegram_runtime.py": "adapters/telegram/telegram_runtime.py",
        "config.example.json": "adapters/telegram/config.example.json",
    },
    "sync-monitor": {
        "sync_runner.py": "src/gbrain_ops/sync_runner.py",
        "sync_monitor.py": "src/gbrain_ops/sync_monitor.py",
    },
}


def install(repo: Path, integrations: Path, backup_root: Path, dry_run: bool = False) -> dict[str, object]:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_root / timestamp
    installed: list[dict[str, str]] = []
    for integration, files in MAPPING.items():
        target_dir = integrations / integration
        target_dir.mkdir(parents=True, exist_ok=True)
        for target_name, source_rel in files.items():
            source = (repo / source_rel).resolve()
            target = target_dir / target_name
            if not source.is_file():
                raise RuntimeError(f"tracked source missing: {source}")
            if target.is_symlink() and target.resolve() == source:
                installed.append({"target": str(target), "source": str(source), "status": "already-linked"})
                continue
            if target.exists() or target.is_symlink():
                destination = backup / integration / target_name
                if not dry_run:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if target.is_symlink():
                        destination.write_text(os.readlink(target) + "\n", encoding="utf-8")
                    else:
                        shutil.copy2(target, destination)
            if not dry_run:
                target.unlink(missing_ok=True)
                target.symlink_to(source)
            installed.append({"target": str(target), "source": str(source), "status": "linked"})
    receipt = {
        "schema": "gbrain-ops-runtime-links/v1",
        "repo": str(repo),
        "integrations": str(integrations),
        "backup": str(backup),
        "dry_run": dry_run,
        "installed": installed,
    }
    if not dry_run:
        backup.mkdir(parents=True, exist_ok=True)
        (backup / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--integrations", type=Path, default=Path.home() / ".gbrain" / "integrations")
    parser.add_argument("--backup-root", type=Path, default=Path.home() / ".local/share/gbrain-ops/migration-backups")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    receipt = install(args.repo.resolve(), args.integrations.expanduser(), args.backup_root.expanduser(), args.dry_run)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
