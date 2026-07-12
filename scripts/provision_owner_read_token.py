#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

TOKEN = re.compile(r'^Token created[^\n]*:\n(?:\s*\n)?\s{2}(\S+)\s*$', re.MULTILINE)


def atomic_private_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        Path(name).unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision a private static read token without printing it")
    parser.add_argument("--gbrain", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--federated-read", default="messages,gmail,calendar,granola")
    args = parser.parse_args()
    output = args.output.expanduser()
    if output.exists():
        if output.stat().st_mode & 0o077:
            raise SystemExit("existing read token file is not private")
        print(json.dumps({"name": args.name, "status": "exists"}, sort_keys=True))
        return 0
    completed = subprocess.run(
        [
            str(args.gbrain.expanduser()),
            "auth",
            "create",
            args.name,
            "--scopes",
            "read",
            "--federated-read",
            args.federated_read,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit("read token provisioning failed")
    match = TOKEN.search(completed.stdout)
    if not match:
        raise SystemExit("read token provisioning output was invalid")
    atomic_private_write(output, match.group(1) + "\n")
    print(json.dumps({"name": args.name, "status": "created"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
