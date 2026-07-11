#!/usr/bin/env python3
"""Monitor GBrain sync heartbeats and emit deduped incident alerts as JSON."""
from __future__ import annotations

import argparse
import json
import os
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_STATE_DIR = Path(os.environ.get("GBRAIN_OPS_MONITOR_STATE_DIR", Path.home() / ".local/share/gbrain-ops/monitor/state")).expanduser()
DEFAULT_JOBS = [value for value in os.environ.get("GBRAIN_OPS_MONITOR_JOBS", "gmail,calendar,messages,granola,telegram").split(",") if value]
CONSECUTIVE_FAILURE_THRESHOLD = 3
STALE_SUCCESS_HOURS = 24
RE_ALERT_HOURS = 24
SYSTEMIC_PATTERNS = re.compile(
    r"(invalid_grant|expired or revoked|unauthorized|permission denied|forbidden|"
    r"missing token|no such file|database.*(corrupt|locked|cannot open)|"
    r"auth.*fail|oauth|quota exceeded)",
    re.IGNORECASE,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def hours_since(dt: datetime | None, now: datetime) -> float | None:
    if dt is None:
        return None
    return max(0.0, (now - dt.astimezone(timezone.utc)).total_seconds() / 3600)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def load_job_states(state_dir: Path, jobs: list[str]) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    for job in jobs:
        state = load_json(state_dir / f"{job}.json", None)
        if isinstance(state, dict):
            states[job] = state
        else:
            states[job] = {
                "job": job,
                "consecutive_failures": 0,
                "last_error_summary": f"No heartbeat state file at {state_dir / f'{job}.json'}",
            }
    return states


def fingerprint_for(state: dict[str, Any], reasons: list[str]) -> str:
    explicit = state.get("last_error_fingerprint")
    if explicit:
        return str(explicit)
    summary = str(state.get("last_error_summary") or "").strip().lower()
    normalized = re.sub(r"\s+", " ", summary)[:160]
    return f"{state.get('job', 'unknown')}|{'/'.join(reasons)}|{state.get('last_exit_code')}|{normalized}"


def build_message(kind: str, job: str, reason: str, state: dict[str, Any]) -> str:
    if kind == "recovery":
        return (
            f"GBrain sync recovered: {job}\n"
            f"Last success: {state.get('last_success_at', 'unknown')}"
        )
    lines = [
        f"GBrain sync incident: {job}",
        f"Reason: {reason}",
        f"Consecutive failures: {state.get('consecutive_failures', 0)}",
        f"Last success: {state.get('last_success_at') or 'never/unknown'}",
        f"Last attempt: {state.get('last_attempt_at') or 'unknown'}",
    ]
    if state.get("last_log_path"):
        lines.append(f"Log: {state['last_log_path']}")
    summary = str(state.get("last_error_summary") or "").strip()
    if summary:
        lines.append(f"Last error: {summary[-1200:]}")
    return "\n".join(lines)


def evaluate_jobs(
    states: dict[str, dict[str, Any]],
    incident_state: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    updated = deepcopy(incident_state)
    alerts: list[dict[str, Any]] = []
    recoveries: list[dict[str, Any]] = []

    for job, state in states.items():
        consecutive = int(state.get("consecutive_failures") or 0)
        last_success = parse_time(state.get("last_success_at"))
        since_success = hours_since(last_success, now)
        summary = str(state.get("last_error_summary") or "")
        reasons: list[str] = []

        if consecutive >= CONSECUTIVE_FAILURE_THRESHOLD:
            reasons.append(f"{consecutive} consecutive failures")
        if since_success is None:
            if state.get("last_attempt_at"):
                reasons.append("no recorded successful sync")
        elif since_success > STALE_SUCCESS_HOURS:
            reasons.append(f"no successful sync for {since_success:.1f}h")
        if consecutive > 0 and SYSTEMIC_PATTERNS.search(summary):
            reasons.append("systemic error pattern")

        existing = updated.get(job, {}) if isinstance(updated.get(job), dict) else {}

        if not reasons:
            if existing.get("status") == "open" and consecutive == 0 and last_success:
                recovery = {
                    "job": job,
                    "kind": "recovery",
                    "message": build_message("recovery", job, "", state),
                }
                recoveries.append(recovery)
                updated[job] = {
                    **existing,
                    "status": "resolved",
                    "resolved_at": iso(now),
                    "last_recovery_at": iso(now),
                }
            continue

        reason = "; ".join(reasons)
        fp = fingerprint_for(state, reasons)
        last_alert_at = parse_time(existing.get("last_alert_at"))
        hours_from_alert = hours_since(last_alert_at, now)
        already_open_same = existing.get("status") == "open" and existing.get("fingerprint") == fp
        should_alert = not already_open_same or hours_from_alert is None or hours_from_alert >= RE_ALERT_HOURS

        if should_alert:
            alert = {
                "job": job,
                "kind": "alert",
                "reason": reason,
                "fingerprint": fp,
                "message": build_message("alert", job, reason, state),
            }
            alerts.append(alert)
            updated[job] = {
                "status": "open",
                "fingerprint": fp,
                "reason": reason,
                "opened_at": existing.get("opened_at") if already_open_same else iso(now),
                "last_alert_at": iso(now),
            }
        elif existing.get("status") == "open":
            updated[job] = {**existing, "reason": reason}

    return {"alerts": alerts, "recoveries": recoveries, "incident_state": updated}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--jobs", nargs="*", default=DEFAULT_JOBS)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    states = load_job_states(args.state_dir, args.jobs)
    incident_path = args.state_dir / "incidents.json"
    incidents = load_json(incident_path, {})
    result = evaluate_jobs(states, incidents)
    if not args.no_write:
        write_json(incident_path, result["incident_state"])
    print(json.dumps({"alerts": result["alerts"], "recoveries": result["recoveries"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
