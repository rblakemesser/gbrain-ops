#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

CLIENT_ID = re.compile(r"^\s*Client ID:\s*(\S+)\s*$", re.MULTILINE)
CLIENT_SECRET = re.compile(r"^\s*Client Secret:\s*(\S+)\s*$", re.MULTILINE)


def parse_registration(output: str) -> tuple[str, str]:
    client_id = CLIENT_ID.search(output)
    client_secret = CLIENT_SECRET.search(output)
    if not client_id or not client_secret:
        raise RuntimeError("GBrain client registration did not return credentials")
    return client_id.group(1), client_secret.group(1)


def atomic_private_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision a source-bound GBrain owner client without printing secrets")
    parser.add_argument("--gbrain", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:3131")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output.exists():
        value = json.loads(output.read_text(encoding="utf-8"))
        if value.get("source_id") != args.source_id or value.get("base_url") != args.base_url:
            raise SystemExit("existing owner client config does not match requested source or URL")
        os.chmod(output, 0o600)
        print(json.dumps({"status": "exists", "name": args.name, "source_id": args.source_id}, sort_keys=True))
        return 0

    command = [
        str(args.gbrain.expanduser().resolve()),
        "auth",
        "register-client",
        args.name,
        "--grant-types",
        "client_credentials",
        "--scopes",
        "read write",
        "--source",
        args.source_id,
        "--federated-read",
        args.source_id,
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise SystemExit("GBrain client registration failed")
    client_id, client_secret = parse_registration(result.stdout)
    atomic_private_json(
        output,
        {
            "schema": "gbrain-ops-owner-client/v1",
            "base_url": args.base_url,
            "client_id": client_id,
            "client_secret": client_secret,
            "source_id": args.source_id,
            "timeout_seconds": 60,
        },
    )
    print(json.dumps({"status": "created", "name": args.name, "source_id": args.source_id}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
