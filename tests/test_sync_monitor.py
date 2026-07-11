import json
from datetime import datetime, timezone, timedelta

from sync_monitor import evaluate_jobs


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def test_single_transient_failure_is_silent():
    now = datetime(2026, 4, 25, 18, 0, tzinfo=timezone.utc)
    states = {
        "gmail-recent-sync": {
            "job": "gmail-recent-sync",
            "last_attempt_at": iso(now - timedelta(hours=1)),
            "last_success_at": iso(now - timedelta(hours=7)),
            "last_failure_at": iso(now - timedelta(hours=1)),
            "consecutive_failures": 1,
            "last_exit_code": 1,
            "last_error_summary": "temporary network timeout",
        }
    }

    result = evaluate_jobs(states, {}, now=now)

    assert result["alerts"] == []
    assert result["recoveries"] == []


def test_alerts_once_after_three_consecutive_failures_and_dedupes():
    now = datetime(2026, 4, 25, 18, 0, tzinfo=timezone.utc)
    states = {
        "calendar-fresh-sync": {
            "job": "calendar-fresh-sync",
            "last_attempt_at": iso(now - timedelta(minutes=10)),
            "last_success_at": iso(now - timedelta(hours=20)),
            "last_failure_at": iso(now - timedelta(minutes=10)),
            "consecutive_failures": 3,
            "last_exit_code": 1,
            "last_error_fingerprint": "exit-1:permission-denied",
            "last_error_summary": "Permission denied while importing calendar pages",
            "last_log_path": "/tmp/calendar.log",
        }
    }

    first = evaluate_jobs(states, {}, now=now)
    assert len(first["alerts"]) == 1
    assert first["alerts"][0]["job"] == "calendar-fresh-sync"
    assert "3 consecutive failures" in first["alerts"][0]["reason"]
    assert first["incident_state"]["calendar-fresh-sync"]["status"] == "open"

    second = evaluate_jobs(states, first["incident_state"], now=now + timedelta(hours=1))
    assert second["alerts"] == []
    assert second["recoveries"] == []


def test_alerts_when_last_success_is_too_old_even_with_few_failures():
    now = datetime(2026, 4, 25, 18, 0, tzinfo=timezone.utc)
    states = {
        "gmail-recent-sync": {
            "job": "gmail-recent-sync",
            "last_attempt_at": iso(now - timedelta(minutes=10)),
            "last_success_at": iso(now - timedelta(hours=25)),
            "last_failure_at": iso(now - timedelta(minutes=10)),
            "consecutive_failures": 1,
            "last_exit_code": 1,
            "last_error_summary": "one recent failure after a long stale period",
        }
    }

    result = evaluate_jobs(states, {}, now=now)

    assert len(result["alerts"]) == 1
    assert "no successful sync for 25.0h" in result["alerts"][0]["reason"]


def test_immediate_alert_for_auth_failures():
    now = datetime(2026, 4, 25, 18, 0, tzinfo=timezone.utc)
    states = {
        "gmail-recent-sync": {
            "job": "gmail-recent-sync",
            "last_attempt_at": iso(now - timedelta(minutes=10)),
            "last_success_at": iso(now - timedelta(hours=6)),
            "last_failure_at": iso(now - timedelta(minutes=10)),
            "consecutive_failures": 1,
            "last_exit_code": 1,
            "last_error_summary": "google auth failed: invalid_grant token has been expired or revoked",
        }
    }

    result = evaluate_jobs(states, {}, now=now)

    assert len(result["alerts"]) == 1
    assert "systemic error pattern" in result["alerts"][0]["reason"]


def test_recovery_after_open_incident_sends_recovery_once():
    now = datetime(2026, 4, 25, 18, 0, tzinfo=timezone.utc)
    incident_state = {
        "gmail-recent-sync": {
            "status": "open",
            "fingerprint": "exit-1:timeout",
            "last_alert_at": iso(now - timedelta(hours=2)),
            "opened_at": iso(now - timedelta(hours=2)),
        }
    }
    states = {
        "gmail-recent-sync": {
            "job": "gmail-recent-sync",
            "last_attempt_at": iso(now - timedelta(minutes=5)),
            "last_success_at": iso(now - timedelta(minutes=5)),
            "last_failure_at": iso(now - timedelta(hours=6)),
            "consecutive_failures": 0,
            "last_exit_code": 0,
            "last_error_summary": "",
        }
    }

    result = evaluate_jobs(states, incident_state, now=now)

    assert result["alerts"] == []
    assert len(result["recoveries"]) == 1
    assert result["recoveries"][0]["job"] == "gmail-recent-sync"
    assert result["incident_state"]["gmail-recent-sync"]["status"] == "resolved"
