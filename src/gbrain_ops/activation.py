from __future__ import annotations

import hashlib
import json
import os
import shlex
from pathlib import Path
from typing import Any


def activate_vendor_runtime(*, runtime: Path, bun: Path, wrapper: Path, receipt_path: Path) -> dict[str, Any]:
    runtime = runtime.resolve(strict=True)
    bun = bun.resolve(strict=True)
    cli = runtime / "src" / "cli.ts"
    if not cli.is_file():
        raise RuntimeError(f"vendor runtime is missing src/cli.ts: {runtime}")
    if not os.access(bun, os.X_OK):
        raise RuntimeError(f"Bun runtime is not executable: {bun}")
    content = f"#!/bin/sh\nexec {shlex.quote(str(bun))} {shlex.quote(str(cli))} \"$@\"\n"
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    temporary = wrapper.with_suffix(wrapper.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(0o755)
    temporary.replace(wrapper)
    receipt = {
        "schema": "gbrain-ops-runtime-activation/v1",
        "runtime": str(runtime),
        "runtime_head": _git_head(runtime),
        "bun": str(bun),
        "wrapper": str(wrapper),
        "wrapper_sha256": hashlib.sha256(content.encode()).hexdigest(),
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt_path.chmod(0o600)
    return receipt


def _git_head(runtime: Path) -> str:
    import subprocess

    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=runtime, check=True, text=True, stdout=subprocess.PIPE)
    return result.stdout.strip()
