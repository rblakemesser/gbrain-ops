#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any

REQUIRED_CONTEXTS = ("pre-merge", "signoff")


def protection_payload() -> dict[str, Any]:
    return {
        "required_status_checks": {
            "strict": True,
            "contexts": list(REQUIRED_CONTEXTS),
        },
        "enforce_admins": True,
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": False,
            "require_code_owner_reviews": False,
            "required_approving_review_count": 0,
            "require_last_push_approval": False,
        },
        "restrictions": None,
        "required_linear_history": False,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "block_creations": False,
        "required_conversation_resolution": False,
        "lock_branch": False,
        "allow_fork_syncing": False,
    }


def protection_drift(protection: dict[str, Any]) -> list[str]:
    drift: list[str] = []
    status_checks = protection.get("required_status_checks") or {}
    actual_contexts = set(status_checks.get("contexts") or [])
    expected_contexts = set(REQUIRED_CONTEXTS)
    if actual_contexts != expected_contexts:
        drift.append(
            f"required contexts are {sorted(actual_contexts)!r}; expected {sorted(expected_contexts)!r}"
        )
    if status_checks.get("strict") is not True:
        drift.append("required status checks are not strict")

    enforce_admins = protection.get("enforce_admins") or {}
    if enforce_admins.get("enabled") is not True:
        drift.append("repository admins can bypass the gate")

    reviews = protection.get("required_pull_request_reviews")
    if reviews is None:
        drift.append("pull requests are not required")
    elif reviews.get("required_approving_review_count") != 0:
        drift.append("required approving review count is not zero")

    for field, label in (
        ("allow_force_pushes", "force pushes"),
        ("allow_deletions", "branch deletion"),
    ):
        value = protection.get(field) or {}
        if value.get("enabled") is not False:
            drift.append(f"{label} is not disabled")

    return drift


def run_gh(args: list[str], *, input_data: dict[str, Any] | None = None) -> str:
    result = subprocess.run(
        ["gh", *args],
        check=False,
        text=True,
        input=json.dumps(input_data) if input_data is not None else None,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "gh returned no diagnostic").strip()
        raise RuntimeError(detail)
    return result.stdout


def resolve_repo(explicit_repo: str | None) -> str:
    if explicit_repo:
        return explicit_repo
    return run_gh(["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"]).strip()


def read_protection(repo: str, branch: str, *, missing_ok: bool = False) -> dict[str, Any]:
    try:
        raw = run_gh(["api", f"repos/{repo}/branches/{branch}/protection"])
    except RuntimeError as exc:
        if missing_ok and "HTTP 404" in str(exc):
            return {}
        raise
    return json.loads(raw)


def apply_protection(repo: str, branch: str) -> None:
    run_gh(
        [
            "api",
            "--method",
            "PUT",
            f"repos/{repo}/branches/{branch}/protection",
            "-H",
            "Accept: application/vnd.github+json",
            "-H",
            "X-GitHub-Api-Version: 2022-11-28",
            "--input",
            "-",
        ],
        input_data=protection_payload(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check or apply the gbrain-ops GitHub merge gate")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="verify protection without changing it (default)")
    action.add_argument("--apply", action="store_true", help="apply the expected protection before verifying")
    parser.add_argument("--repo", help="GitHub owner/repository; defaults to the current checkout")
    parser.add_argument("--branch", default="main")
    args = parser.parse_args(argv)

    try:
        repo = resolve_repo(args.repo)
        current = read_protection(repo, args.branch, missing_ok=args.apply)
        drift = protection_drift(current)
        if drift and args.apply:
            for item in drift:
                print(f"repairing: {item}")
            apply_protection(repo, args.branch)
            current = read_protection(repo, args.branch)
            drift = protection_drift(current)
    except (FileNotFoundError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"merge-gate error: {exc}", file=sys.stderr)
        return 1

    if drift:
        for item in drift:
            print(f"drift: {item}", file=sys.stderr)
        return 1

    print(f"merge gate verified: {repo}:{args.branch} requires {', '.join(REQUIRED_CONTEXTS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())