from __future__ import annotations

import json
import sys
from copy import deepcopy
from datetime import date
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


class _FakeList:
    def __init__(self, calls: list[dict]) -> None:
        self.calls = calls

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return self

    def execute(self) -> dict:
        return {"messages": [{"id": "fixture-message-1"}, {"id": "fixture-spam-1"}]}


class _FakeService:
    def __init__(self, calls: list[dict]) -> None:
        self._messages = _FakeList(calls)

    def users(self):
        return self

    def messages(self):
        return self._messages


def spam_record() -> dict:
    record = fixture_record()
    record.update(id="fixture-spam-1", labelIds=["SPAM", "UNREAD"])
    return record


def test_gmail_listing_excludes_spam_and_trash() -> None:
    calls: list[dict] = []

    email_module.list_message_ids_for_range(_FakeService(calls), date(2026, 8, 18), date(2026, 8, 19))

    assert calls[0]["includeSpamTrash"] is False
    assert "-in:spam" in calls[0]["q"]
    assert "-in:trash" in calls[0]["q"]
    assert "in:anywhere" not in calls[0]["q"]


def test_spam_or_trash_records_never_reach_raw_pages_or_digests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(email_module, "gmail_profile", lambda: {"emailAddress": OWNER_EMAIL})
    monkeypatch.setattr(email_module, "gmail_service", lambda: _FakeService([]))
    monkeypatch.setattr(
        email_module,
        "extract_message",
        lambda _service, msg_id, _account: spam_record() if msg_id == "fixture-spam-1" else fixture_record(),
    )

    result = email_module.fetch_range(date(2026, 8, 18), date(2026, 8, 19))
    assert [r["id"] for r in result.records] == ["fixture-message-1"]

    trashed = fixture_record()
    trashed.update(id="fixture-trash-1", labelIds=["TRASH"])
    raw_path = tmp_path / "messages.json"
    raw_path.write_text(json.dumps([spam_record(), trashed]), encoding="utf-8")
    merged = merge_raw_records(raw_path, [fixture_record()])
    assert [r["id"] for r in merged] == ["fixture-message-1"]
    assert [r["id"] for r in json.loads(raw_path.read_text(encoding="utf-8"))] == ["fixture-message-1"]

    monkeypatch.setattr(email_module, "BRAIN_DIR", tmp_path / "brain")
    pages = email_module.write_pages(OWNER_EMAIL, [fixture_record(), spam_record(), trashed])
    page = pages[0].read_text(encoding="utf-8")
    assert "Total messages: 1" in page
    assert "fixture-spam-1" not in page
    assert "fixture-trash-1" not in page
