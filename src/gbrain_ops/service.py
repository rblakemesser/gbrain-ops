from __future__ import annotations

import plistlib
from pathlib import Path
from typing import Sequence


def render_launchd_service(
    *,
    label: str,
    argv: Sequence[str],
    stdout_path: str,
    stderr_path: str,
    interval_seconds: int | None = None,
    run_at_load: bool = True,
) -> bytes:
    if not label or not argv:
        raise ValueError("label and argv are required")
    if interval_seconds is not None and interval_seconds < 60:
        raise ValueError("interval_seconds must be at least 60")
    payload: dict[str, object] = {
        "Label": label,
        "ProgramArguments": list(argv),
        "RunAtLoad": run_at_load,
        "StandardOutPath": stdout_path,
        "StandardErrorPath": stderr_path,
        "ProcessType": "Background",
    }
    if interval_seconds is not None:
        payload["StartInterval"] = interval_seconds
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)


def install_launchd_service(destination: Path, content: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.chmod(0o600)
    temporary.replace(destination)
