#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import yaml

TOKEN = re.compile(r"^[A-Za-z0-9_-]{20,}$")


def atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        os.chmod(path, mode)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        Path(name).unlink(missing_ok=True)
        raise


def set_env_value(content: str, key: str, value: str) -> str:
    lines = content.splitlines()
    replacement = f"{key}={value}"
    found = False
    result: list[str] = []
    for line in lines:
        if line.startswith(f"{key}="):
            if not found:
                result.append(replacement)
                found = True
            continue
        result.append(line)
    if not found:
        result.append(replacement)
    return "\n".join(result).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the private GBrain owner MCP endpoint in Hermes")
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--url", default="http://127.0.0.1:3131/mcp")
    args = parser.parse_args()

    token_file = args.token_file.expanduser()
    env_file = args.env_file.expanduser()
    config_file = args.config.expanduser()
    token = token_file.read_text(encoding="utf-8").strip()
    if not TOKEN.fullmatch(token):
        raise SystemExit("read token file is invalid")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_root = config_file.parent / "backups" / "gbrain-owner-mcp" / stamp
    backup_root.mkdir(parents=True, exist_ok=True)
    for path in (env_file, config_file):
        if path.exists():
            shutil.copy2(path, backup_root / path.name)

    current_env = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    atomic_write(env_file, set_env_value(current_env, "GBRAIN_OWNER_READ_TOKEN", token))

    config = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
    servers = config.setdefault("mcp_servers", {})
    servers["gbrain-owner"] = {
        "url": args.url,
        "headers": {"Authorization": "Bearer ${GBRAIN_OWNER_READ_TOKEN}"},
        "enabled": True,
        "connect_timeout": 30,
        "timeout": 120,
        "tools": {
            "include": [
                "get_page",
                "list_pages",
                "search",
                "query",
                "get_chunks",
                "get_stats",
                "get_health",
                "get_brain_identity",
                "get_timeline",
                "get_links",
                "get_backlinks",
                "traverse_graph",
                "sources_list",
                "sources_status",
                "get_recent_salience",
                "chronicle_day",
                "chronicle_since",
                "chronicle_last_seen",
                "ontology_get",
                "recall",
                "volunteer_context",
                "takes_list",
                "takes_search",
                "find_experts",
                "find_trajectory",
                "whoami",
            ],
            "resources": False,
            "prompts": False,
        },
    }
    atomic_write(config_file, yaml.safe_dump(config, sort_keys=False, default_flow_style=False))
    print(f"status=installed server=gbrain-owner backup={backup_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
