from __future__ import annotations

import re
from pathlib import Path

import pytest

import scripts.configure_merge_gate as merge_gate
from scripts.configure_merge_gate import REQUIRED_CONTEXTS, protection_drift, protection_payload


def protected_branch(**overrides):
    protection = {
        "required_status_checks": {
            "strict": True,
            "contexts": ["pre-merge", "signoff"],
        },
        "enforce_admins": {"enabled": True},
        "required_pull_request_reviews": {"required_approving_review_count": 0},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
    }
    protection.update(overrides)
    return protection


def test_protection_payload_requires_pr_signoff_and_ci_for_admins():
    payload = protection_payload()

    assert payload["required_status_checks"] == {
        "strict": True,
        "contexts": list(REQUIRED_CONTEXTS),
    }
    assert payload["enforce_admins"] is True
    assert payload["required_pull_request_reviews"]["required_approving_review_count"] == 0
    assert payload["allow_force_pushes"] is False
    assert payload["allow_deletions"] is False


def test_protection_drift_accepts_expected_gate():
    assert protection_drift(protected_branch()) == []


def test_unprotected_branch_can_be_treated_as_repairable_drift(monkeypatch):
    def missing_protection(_args):
        raise RuntimeError("gh: Branch not protected (HTTP 404)")

    monkeypatch.setattr(merge_gate, "run_gh", missing_protection)

    assert merge_gate.read_protection("owner/repo", "main", missing_ok=True) == {}
    with pytest.raises(RuntimeError, match="HTTP 404"):
        merge_gate.read_protection("owner/repo", "main")


def test_protection_drift_reports_bypass_and_missing_checks():
    drift = protection_drift(
        protected_branch(
            required_status_checks={"strict": False, "contexts": ["signoff"]},
            enforce_admins={"enabled": False},
            required_pull_request_reviews=None,
        )
    )

    assert "required status checks are not strict" in drift
    assert "repository admins can bypass the gate" in drift
    assert "pull requests are not required" in drift
    assert any("required contexts" in item for item in drift)


def test_pre_merge_job_aggregates_every_other_ci_job():
    workflow = Path(".github/workflows/ci.yml").read_text()
    jobs_section = workflow.split("\njobs:\n", maxsplit=1)[1]
    job_ids = set(re.findall(r"^  ([A-Za-z0-9_-]+):\s*$", jobs_section, flags=re.MULTILINE))
    pre_merge = re.search(
        r"^  pre-merge:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\s*$|\Z)",
        workflow,
        flags=re.MULTILINE | re.DOTALL,
    )

    assert pre_merge is not None
    needs = re.search(r"^    needs: \[([^]]+)]\s*$", pre_merge.group("body"), flags=re.MULTILINE)
    assert needs is not None
    aggregated_jobs = {item.strip() for item in needs.group(1).split(",")}

    assert aggregated_jobs == job_ids - {"pre-merge"}
    assert aggregated_jobs == {"ops", "vendor-compat"}
    assert "if: ${{ always() }}" in pre_merge.group("body")
