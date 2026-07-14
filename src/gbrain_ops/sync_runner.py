#!/usr/bin/env python3
"""Run a GBrain sync command and persist heartbeat/status for the monitor."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_STATE_DIR = Path(os.environ.get("GBRAIN_OPS_MONITOR_STATE_DIR", Path.home() / ".local/share/gbrain-ops/monitor/state")).expanduser()
DEFAULT_LOG_DIR = Path(os.environ.get("GBRAIN_OPS_MONITOR_LOG_DIR", Path.home() / ".local/share/gbrain-ops/monitor/logs")).expanduser()


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    tmp.chmod(0o600)
    tmp.replace(path)
    path.chmod(0o600)


def summarize_output(output: str, max_lines: int = 30) -> str:
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    return "\n".join(lines[-max_lines:])[-4000:]


def fingerprint(exit_code: int | None, summary: str) -> str:
    normalized = re.sub(r"\s+", " ", summary.lower()).strip()[:1000]
    digest = hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"exit-{exit_code}:{digest}"


def parse_command(command: str) -> list[str]:
    argv = shlex.split(command)
    if not argv:
        raise ValueError("command must contain at least one argv element")
    return argv


def run_command(argv: list[str], timeout: int) -> tuple[int, str]:
    env = os.environ.copy()
    proc = subprocess.Popen(
        argv,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            output, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            output, _ = proc.communicate()
        raise subprocess.TimeoutExpired(argv, timeout, output=output)
    return int(proc.returncode or 0), output or ""


def update_state(
    *,
    job: str,
    command: str,
    exit_code: int | None,
    output: str,
    log_path: Path,
    state_dir: Path,
    timed_out: bool = False,
) -> dict[str, Any]:
    now = iso_now()
    state_path = state_dir / f"{job}.json"
    prev = load_json(state_path, {})
    summary = summarize_output(output)
    success = exit_code == 0 and not timed_out
    previous_failures = int(prev.get("consecutive_failures") or 0)
    state = {
        **prev,
        "job": job,
        "command": command,
        "last_attempt_at": now,
        "last_exit_code": exit_code,
        "last_log_path": str(log_path),
        "last_output_tail": summary,
    }
    if success:
        state.update(
            {
                "last_success_at": now,
                "consecutive_failures": 0,
                "last_error_summary": "",
                "last_error_fingerprint": "",
            }
        )
    else:
        error_summary = "Command timed out" if timed_out else summary or f"Command failed with exit code {exit_code}"
        state.update(
            {
                "last_failure_at": now,
                "consecutive_failures": previous_failures + 1,
                "last_error_summary": error_summary,
                "last_error_fingerprint": fingerprint(exit_code, error_summary),
            }
        )
    write_json(state_path, state)
    return state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    parser.add_argument("--command", required=True, help="shell-style quoting is parsed into argv; shell operators are never evaluated")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    args = parser.parse_args()

    started = datetime.now(timezone.utc)
    args.log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.log_dir.chmod(0o700)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    log_path = args.log_dir / f"{args.job}-{stamp}.log"

    exit_code: int | None
    output: str
    timed_out = False
    argv: list[str] = []
    try:
        argv = parse_command(args.command)
        exit_code, output = run_command(argv, args.timeout)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = 124
        partial = exc.stdout or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        output = f"{partial}\nCommand timed out after {args.timeout}s"
    except Exception as exc:  # defensive: heartbeat should record unexpected runner failures
        exit_code = 125
        output = f"sync_runner exception: {type(exc).__name__}: {exc}"

    log_path.write_text(output)
    log_path.chmod(0o600)
    state = update_state(
        job=args.job,
        command=shlex.join(argv) if argv else args.command,
        exit_code=exit_code,
        output=output,
        log_path=log_path,
        state_dir=args.state_dir,
        timed_out=timed_out,
    )
    print(json.dumps({"job": args.job, "exit_code": exit_code, "consecutive_failures": state.get("consecutive_failures"), "log": str(log_path)}, indent=2))
    return int(exit_code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
