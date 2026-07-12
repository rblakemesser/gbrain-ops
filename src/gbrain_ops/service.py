from __future__ import annotations

import plistlib
import shlex
from pathlib import Path
from typing import Mapping, Sequence


def render_launchd_service(
    *,
    label: str,
    argv: Sequence[str],
    stdout_path: str,
    stderr_path: str,
    interval_seconds: int | None = None,
    run_at_load: bool = True,
    keep_alive: bool = False,
    throttle_interval: int = 10,
    working_directory: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> bytes:
    if not label or not argv:
        raise ValueError("label and argv are required")
    if interval_seconds is not None and interval_seconds < 60:
        raise ValueError("interval_seconds must be at least 60")
    if throttle_interval < 1:
        raise ValueError("throttle_interval must be positive")
    payload: dict[str, object] = {
        "Label": label,
        "ProgramArguments": list(argv),
        "RunAtLoad": run_at_load,
        "StandardOutPath": stdout_path,
        "StandardErrorPath": stderr_path,
        "ProcessType": "Background",
        "ThrottleInterval": throttle_interval,
    }
    if keep_alive:
        payload["KeepAlive"] = True
    if working_directory:
        payload["WorkingDirectory"] = working_directory
    if environment:
        payload["EnvironmentVariables"] = dict(environment)
    if interval_seconds is not None:
        payload["StartInterval"] = interval_seconds
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)


def render_owner_launcher(
    *,
    bun: Path,
    runtime: Path,
    gbrain_home: Path,
    env_file: Path,
    port: int,
) -> str:
    if port < 1 or port > 65535:
        raise ValueError("port must be between 1 and 65535")
    cli = runtime / "src" / "cli.ts"
    values = [bun, cli, gbrain_home, env_file]
    if not all(path.is_absolute() for path in values):
        raise ValueError("owner launcher paths must be absolute")
    return "\n".join(
        [
            "#!/usr/bin/env zsh",
            "set -euo pipefail",
            f"set -a; source {shlex.quote(str(env_file))}; set +a",
            f"export HOME={shlex.quote(str(gbrain_home.parent))}",
            f"export GBRAIN_HOME={shlex.quote(str(gbrain_home.parent))}",
            "exec "
            + " ".join(
                shlex.quote(str(value))
                for value in [
                    bun,
                    cli,
                    "serve",
                    "--http",
                    "--with-ingestion",
                    "--port",
                    str(port),
                    "--bind",
                    "127.0.0.1",
                    "--suppress-bootstrap-token",
                ]
            ),
            "",
        ]
    )


def install_launchd_service(destination: Path, content: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.chmod(0o600)
    temporary.replace(destination)
