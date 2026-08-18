from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

import pytest

ADAPTER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADAPTER))

import email_collector as email_module  # noqa: E402
from email_collector import merge_raw_records, render_day_page, write_digest  # noqa: E402

DOMAIN = "example.invalid"
FIXTURE_EMAIL = f"fixture@{DOMAIN}"
OWNER_EMAIL = f"owner@{DOMAIN}"
RECIPIENT_EMAIL = f"recipient@{DOMAIN}"


def fixture_record() -> dict:
    return {
        "id": "fixture-message-1",
        "threadId": "fixture-thread-1",
        "historyId": "fixture-history-1",
        "internalDate": "0",
        "timestamp_utc": "2026-08-18T12:34:00+00:00",
        "day": "2026-08-18",
        "from": f"Fixture\u2028Sender <{FIXTURE_EMAIL}>",
        "to": RECIPIENT_EMAIL,
        "cc": "",
        "bcc": "",
        "subject": "Subject with\u2028a line separator",
        "date_header": "",
        "snippet": "Snippet with\u2029a paragraph separator",
        "labelIds": ["INBOX"],
        "is_sent": False,
        "is_draft": False,
        "is_important": False,
        "is_starred": False,
        "is_unread": False,
        "is_noise": False,
        "is_signature": False,
        "gmail_link": "https://mail.google.com/mail/u/0/#inbox/fixture-message-1",
        "account_email": OWNER_EMAIL,
    }


def test_rendered_daily_page_normalizes_unicode_line_separators_without_mutating_raw_record() -> None:
    record = fixture_record()
    original = deepcopy(record)

    page = render_day_page("2026-08-18", OWNER_EMAIL, [record])

    assert "\u2028" not in page
    assert "\u2029" not in page
    assert f"From: Fixture Sender <{FIXTURE_EMAIL}>" in page
    assert "Subject: Subject with a line separator" in page
    assert "Snippet: Snippet with a paragraph separator" in page
    assert record == original


def test_raw_json_preserves_source_separators_while_all_derived_markdown_normalizes_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = fixture_record()
    raw_path = tmp_path / "messages.json"
    monkeypatch.setattr(email_module, "DIGEST_DIR", tmp_path / "digests")

    merge_raw_records(raw_path, [record])
    digest_path = write_digest(OWNER_EMAIL, [record], "fixture")
    daily_page = render_day_page("2026-08-18", OWNER_EMAIL, [record])

    raw_text = raw_path.read_text(encoding="utf-8")
    assert "\u2028" in raw_text
    assert "\u2029" in raw_text
    assert "\u2028" not in digest_path.read_text(encoding="utf-8")
    assert "\u2029" not in digest_path.read_text(encoding="utf-8")
    assert "\u2028" not in daily_page
    assert "\u2029" not in daily_page


def test_recent_wrapper_avoids_full_archive_classification_and_promotion() -> None:
    wrapper = (ADAPTER / "run_fresh_sync.sh").read_text(encoding="utf-8")

    assert "classify_archive.py" not in wrapper
    assert "promote_archive.py" not in wrapper
    assert wrapper.count("reconcile_archive.py") == 1
    assert "--glob 'email/**/*.md'" in wrapper
    assert "--dated-within-days" in wrapper
