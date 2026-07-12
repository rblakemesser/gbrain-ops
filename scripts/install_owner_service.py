#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from gbrain_ops.service import install_launchd_service, render_launchd_service, render_owner_launcher


def atomic_text(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(mode)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Install a launchd service for the sole local GBrain owner")
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--bun", type=Path, required=True)
    parser.add_argument("--gbrain-home", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--port", type=int, default=3131)
    parser.add_argument("--label", default="com.gbrain.owner")
    parser.add_argument("--launcher", type=Path, required=True)
    parser.add_argument("--plist", type=Path, required=True)
    parser.add_argument("--stdout", type=Path, required=True)
    parser.add_argument("--stderr", type=Path, required=True)
    args = parser.parse_args()

    for path in (args.runtime, args.bun, args.gbrain_home, args.env_file, args.launcher, args.plist):
        if not path.expanduser().resolve().is_absolute():
            raise SystemExit("all owner service paths must be absolute")
    runtime = args.runtime.expanduser().resolve()
    bun = args.bun.expanduser().resolve()
    home = args.gbrain_home.expanduser().resolve()
    env_file = args.env_file.expanduser().resolve()
    launcher = args.launcher.expanduser().resolve()
    plist = args.plist.expanduser().resolve()
    stdout = args.stdout.expanduser().resolve()
    stderr = args.stderr.expanduser().resolve()
    if not (runtime / "src" / "cli.ts").is_file() or not bun.is_file() or not env_file.is_file():
        raise SystemExit("owner runtime, bun, or environment file is missing")

    stdout.parent.mkdir(parents=True, exist_ok=True)
    stderr.parent.mkdir(parents=True, exist_ok=True)
    atomic_text(
        launcher,
        render_owner_launcher(bun=bun, runtime=runtime, gbrain_home=home, env_file=env_file, port=args.port),
        0o700,
    )
    content = render_launchd_service(
        label=args.label,
        argv=[str(launcher)],
        stdout_path=str(stdout),
        stderr_path=str(stderr),
        keep_alive=True,
        throttle_interval=15,
        working_directory=str(runtime),
    )
    install_launchd_service(plist, content)
    print(json.dumps({"status": "installed", "label": args.label, "port": args.port}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
