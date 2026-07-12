#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from gbrain_ops.service import install_launchd_service, render_launchd_service


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the dedicated local GBrain PostgreSQL launchd service")
    parser.add_argument("--postgres", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--plist", type=Path, required=True)
    parser.add_argument("--stdout", type=Path, required=True)
    parser.add_argument("--stderr", type=Path, required=True)
    parser.add_argument("--label", default="com.gbrain.postgres")
    parser.add_argument("--locale", default="en_US.UTF-8")
    args = parser.parse_args()

    postgres = args.postgres.expanduser().resolve()
    data_dir = args.data_dir.expanduser().resolve()
    plist = args.plist.expanduser().resolve()
    stdout = args.stdout.expanduser().resolve()
    stderr = args.stderr.expanduser().resolve()
    if not postgres.is_file():
        raise SystemExit("postgres executable is missing")
    if not (data_dir / "PG_VERSION").is_file():
        raise SystemExit("data directory is not an initialized PostgreSQL cluster")
    if data_dir.stat().st_mode & 0o077:
        raise SystemExit("data directory must not be accessible to group or other users")

    stdout.parent.mkdir(parents=True, exist_ok=True)
    stderr.parent.mkdir(parents=True, exist_ok=True)
    content = render_launchd_service(
        label=args.label,
        argv=[str(postgres), "-D", str(data_dir)],
        stdout_path=str(stdout),
        stderr_path=str(stderr),
        keep_alive=True,
        throttle_interval=15,
        working_directory=str(data_dir),
        environment={"LC_ALL": args.locale},
    )
    install_launchd_service(plist, content)
    print(json.dumps({"status": "installed", "label": args.label, "data_dir": str(data_dir)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
