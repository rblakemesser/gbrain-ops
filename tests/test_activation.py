from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from gbrain_ops.activation import activate_vendor_runtime


def test_activate_vendor_runtime_writes_atomic_wrapper_and_private_receipt(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    (runtime / "src").mkdir(parents=True)
    (runtime / "src" / "cli.ts").write_text("console.log('fixture')\n")
    subprocess.run(["git", "init", "-q"], cwd=runtime, check=True)
    subprocess.run(["git", "add", "."], cwd=runtime, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@users.noreply.github.com", "commit", "-qm", "fixture"],
        cwd=runtime,
        check=True,
    )
    bun = tmp_path / "bun"
    bun.write_text("#!/bin/sh\nexit 0\n")
    bun.chmod(0o755)
    wrapper = tmp_path / "bin" / "gbrain"
    receipt_path = tmp_path / "private" / "activation.json"

    receipt = activate_vendor_runtime(runtime=runtime, bun=bun, wrapper=wrapper, receipt_path=receipt_path)

    assert wrapper.stat().st_mode & 0o111
    assert '"$@"' in wrapper.read_text()
    assert str(runtime.resolve()) in wrapper.read_text()
    assert receipt["runtime_head"] == subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=runtime, text=True).strip()
    assert json.loads(receipt_path.read_text())["wrapper_sha256"] == receipt["wrapper_sha256"]
    assert receipt_path.stat().st_mode & 0o077 == 0
