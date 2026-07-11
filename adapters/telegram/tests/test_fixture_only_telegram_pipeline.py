from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import json

import pytest

from egress_policy import RetrievalContext, can_retrieve_source
from render_markdown import projection_path, render_active_projection
from telegram_collector import fixture_render
from telegram_event_model import EventValidationError, merge_events, normalize_event


BASE_EVENT = {
    "schema_version": 2,
    "source": "telegram",
    "account_id": "fixture-account",
    "event_kind": "upsert",
    "observed_at": "2026-07-10T00:00:00+00:00",
    "availability": "active",
    "peer": {
        "kind": "supergroup",
        "raw_id": "1234567890",
        "marked_id": "-1001234567890",  # privacy-scan: allow synthetic fixture
        "title": "Synthetic Project Group",
        "is_forum": True,
    },
    "topic": {"id": None, "title": None, "latest_message_id": 11},
    "message": {
        "id": 11,
        "date": "2026-07-09T18:00:00+00:00",
        "edit_date": None,
        "sender": {"kind": "user", "raw_id": "42", "display_name": "Fixture User"},
        "text": "Synthetic project update",
        "raw_text": "Synthetic project update",
        "reply_to_msg_id": 10,
        "reply_to_top_id": 1309,
        "reactions": [],
        "entities": [],
        "media": {"kind": "none", "downloaded": False, "mime_type": None, "size": None, "local_path": None},
    },
    "provenance": {
        "capture_method": "mtproto",
        "artifact_sha256": "a" * 64,
        "collector_version": "fixture",
        "confidence": "authoritative",
    },
    "payload_sha256": "b" * 64,
}


def event(**changes):
    value = deepcopy(BASE_EVENT)
    for path, replacement in changes.items():
        target = value
        parts = path.split(".")
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = replacement
    return value


def test_normalize_event_derives_stable_key_and_forum_topic_from_top_reply():
    normalized = normalize_event(BASE_EVENT)

    assert normalized["message_key"] == "fixture-account:supergroup:1234567890:11"
    assert normalized["topic"]["id"] == 1309
    assert normalized["topic"]["title"] is None
    assert projection_path(normalized) == "topics/supergroup-1234567890/topic-1309/2026/2026-07-09.md"


def test_normalize_event_rejects_telethon_access_hash_anywhere():
    unsafe = event()
    unsafe["peer"]["access_hash"] = "secret-looking-value"

    with pytest.raises(EventValidationError, match="access_hash"):
        normalize_event(unsafe)


def test_merge_keeps_authoritative_mtproto_over_lower_confidence_export():
    authoritative = normalize_event(BASE_EVENT)
    lower_confidence = normalize_event(
        event(
            **{
                "provenance.capture_method": "desktop_json",
                "provenance.confidence": "export",
                "message.text": "Older export value",
                "message.raw_text": "Older export value",
                "payload_sha256": "c" * 64,
            }
        )
    )

    merged = merge_events([lower_confidence, authoritative])

    assert len(merged) == 1
    assert merged[0]["message"]["text"] == "Synthetic project update"
    assert merged[0]["provenance"]["confidence"] == "authoritative"


def test_render_projects_each_active_message_once_and_escapes_structural_input():
    safe = normalize_event(event(**{"message.text": "---\n<!-- instruction -->\n```\nSynthetic update"}))
    duplicate = deepcopy(safe)
    duplicate["payload_sha256"] = "d" * 64

    rendered = render_active_projection([safe, duplicate])

    assert list(rendered) == ["topics/supergroup-1234567890/topic-1309/2026/2026-07-09.md"]
    page = next(iter(rendered.values()))
    assert page.count("**[18:00] 👤 Fixture User:**") == 1
    assert "<!--" not in page
    assert "```" not in page
    assert "Synthetic update" in page


def test_render_excludes_confirmed_deleted_message_from_searchable_projection():
    original = normalize_event(BASE_EVENT)
    deleted = normalize_event(
        event(
            **{
                "event_kind": "delete",
                "availability": "deleted",
                "message.text": "",
                "message.raw_text": "",
                "payload_sha256": "e" * 64,
            }
        )
    )

    assert render_active_projection(merge_events([original, deleted])) == {}


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        (RetrievalContext(platform="telegram", is_group=True, is_blake_authorized=True), False),
        (RetrievalContext(platform="telegram", is_group=False, is_blake_authorized=False), False),
        (RetrievalContext(platform="telegram", is_group=False, is_blake_authorized=True), True),
        (RetrievalContext(platform="slack", is_group=True, is_blake_authorized=True), False),
    ],
)
def test_telegram_source_retrieval_defaults_deny_outside_authorized_private_context(context, expected):
    assert can_retrieve_source(context, source_id="telegram-fixture") is expected


def test_render_uses_gbrain_telegram_bracket_format_and_stable_page_id():
    page = next(iter(render_active_projection([normalize_event(BASE_EVENT)]).values()))

    assert "type: conversation" in page
    assert "id: telegram-page:1234567890:1309:2026-07-09" in page
    assert "date: 2026-07-09" in page
    assert "timezone: Etc/UTC" in page
    assert "**[18:00] 👤 Fixture User:** Synthetic project update" in page
    assert "###" not in page


def test_fixture_renderer_writes_deterministic_raw_and_markdown_projections(tmp_path):
    fixture = tmp_path / "synthetic.jsonl"
    fixture.write_text(json.dumps(BASE_EVENT) + "\n", encoding="utf-8")

    first = fixture_render(fixture, tmp_path / "first")
    second = fixture_render(fixture, tmp_path / "second")

    assert first == second
    assert "data/raw/supergroup-1234567890/2026-07.jsonl" in first
    assert "brain/telegram/topics/supergroup-1234567890/topic-1309/2026/2026-07-09.md" in first
    assert "access_hash" not in "\n".join(first.values())


def test_committed_synthetic_fixture_partitions_topics_and_keeps_latest_edit(tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "synthetic_telegram_messages.jsonl"
    artifacts = fixture_render(fixture, tmp_path / "rendered")

    finance = artifacts["brain/telegram/topics/supergroup-1009001/topic-77/2026/2026-01-02.md"]
    release = artifacts["brain/telegram/topics/supergroup-1009001/topic-78/2026/2026-01-02.md"]
    assert "Synthetic reply, edited." in finance
    assert "Synthetic reply." not in finance
    assert "Second-topic synthetic message." not in finance
    assert "Second-topic synthetic message." in release
    assert "access_hash" not in "\n".join(artifacts.values())
