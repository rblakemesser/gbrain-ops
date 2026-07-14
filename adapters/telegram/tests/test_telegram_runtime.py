from __future__ import annotations

import json
import inspect
from datetime import UTC, datetime
from pathlib import Path

import pytest

import telegram_runtime as runtime
from telegram_event_model import EventValidationError


class User:
    def __init__(self) -> None:
        self.id = 42
        self.first_name = "Fixture"
        self.last_name = "User"
        self.username = "fixture_user"
        self.access_hash = 999999  # Must never be persisted.
        self.bot = False


class ChatForbidden:
    def __init__(self) -> None:
        self.id = 88
        self.title = "Unavailable fixture"


class ReplyHeader:
    def __init__(self, top: int, reply: int) -> None:
        self.reply_to_top_id = top
        self.reply_to_msg_id = reply


class Message:
    def __init__(self) -> None:
        self.id = 77
        self.date = datetime(2026, 7, 10, 18, 0, tzinfo=UTC)
        self.edit_date = None
        self.raw_text = "A fixture message"
        self.message = self.raw_text
        self.sender_id = 42
        self.sender = User()
        self.reply_to = ReplyHeader(top=505, reply=76)
        self.media = None
        self.reactions = None


def test_default_config_is_full_account_scope(tmp_path: Path):
    config_path = tmp_path / "config.json"
    config_path.write_text((Path(__file__).parents[1] / "config.example.json").read_text(), encoding="utf-8")

    config = runtime.load_config(config_path)

    assert config.mode == "include_all_accessible"
    assert config.include_private_1to1 is True
    assert config.include_groups is True
    assert config.include_channels is True
    assert config.include_bots is True
    assert config.include_saved_messages is True
    assert config.include_archived_dialogs is True
    assert config.include_muted_dialogs is True
    assert config.exclude_peer_ids == ()
    assert config.max_flood_wait_seconds == 900
    assert config.max_flood_wait_retries == 3


def test_safe_peer_strips_telegram_access_hash():
    peer = runtime.safe_peer(User())

    assert peer == {
        "kind": "user",
        "raw_id": "42",
        "title": "Fixture User",
        "username": "fixture_user",
    }
    assert "access_hash" not in json.dumps(peer)


def test_forbidden_chat_has_stable_safe_kind():
    assert runtime.safe_peer(ChatForbidden()) == {
        "kind": "chat",
        "raw_id": "88",
        "title": "Unavailable fixture",
        "username": "",
    }


def test_message_event_uses_topic_root_not_ordinary_reply_and_has_no_access_hash():
    peer = {
        **runtime.safe_peer(User()),
        "dialog_type": "dm",
        "is_forum": False,
        "last_message_id": 999,
        "last_message_date": "2026-07-10T18:01:00Z",
        "topics": [{"id": 505, "title": "Mutable inventory snapshot"}],
    }
    event = runtime.message_to_event(Message(), peer, "account-1", observed_at="2026-07-10T18:00:01Z")

    assert event["topic"]["id"] == 505
    assert event["message"]["reply_to_top_id"] == 505
    assert event["message"]["reply_to_msg_id"] == 76
    assert event["message_key"] == "account-1:user:42:77"
    assert event["peer"] == {
        "kind": "user",
        "raw_id": "42",
        "title": "Fixture User",
        "username": "fixture_user",
        "dialog_type": "dm",
        "is_forum": False,
    }
    assert "access_hash" not in json.dumps(event)


def test_append_events_then_checkpoint_never_serializes_secret_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    raw_dir = tmp_path / "raw"
    checkpoints = tmp_path / "checkpoints"
    monkeypatch.setattr(runtime, "RAW_DIR", raw_dir)
    monkeypatch.setattr(runtime, "CHECKPOINTS_DIR", checkpoints)

    peer = runtime.safe_peer(User())
    event = runtime.message_to_event(Message(), peer, "account-1", observed_at="2026-07-10T18:00:01Z")
    written = runtime.append_events([event])
    checkpoint = runtime.save_checkpoint(peer, [event], mode="recent")

    assert sum(written.values()) == 1
    assert checkpoint["oldest_collected_id"] == 77
    raw_text = next(raw_dir.glob("*/*.jsonl")).read_text(encoding="utf-8")
    assert "access_hash" not in raw_text
    assert "999999" not in raw_text
    assert next(raw_dir.glob("*/*.jsonl")).stat().st_mode & 0o777 == 0o600
    assert next(checkpoints.glob("*.json")).stat().st_mode & 0o777 == 0o600

    completed = runtime.save_checkpoint(peer, [], mode="backfill", backfill_complete=True)
    assert completed["backfill_complete"] is True


def test_overlapping_poll_is_idempotent_but_edit_is_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runtime, "RAW_DIR", tmp_path / "raw")
    peer = runtime.safe_peer(User())
    first = runtime.message_to_event(Message(), peer, "account-1", observed_at="2026-07-10T18:00:01Z")
    overlap = runtime.message_to_event(Message(), peer, "account-1", observed_at="2026-07-10T18:05:01Z")

    assert sum(runtime.append_events([first]).values()) == 1
    assert sum(runtime.append_events([overlap]).values()) == 0

    edited_message = Message()
    edited_message.raw_text = "A corrected fixture message"
    edited_message.message = edited_message.raw_text
    edited_message.edit_date = datetime(2026, 7, 10, 18, 6, tzinfo=UTC)
    edited = runtime.message_to_event(edited_message, peer, "account-1", observed_at="2026-07-10T18:06:01Z")
    assert sum(runtime.append_events([edited]).values()) == 1
    assert len(next((tmp_path / "raw").glob("*/*.jsonl")).read_text(encoding="utf-8").splitlines()) == 2


def test_overlapping_poll_collapses_duplicates_written_by_older_collectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(runtime, "RAW_DIR", tmp_path / "raw")
    peer = runtime.safe_peer(User())
    first = runtime.message_to_event(Message(), peer, "account-1", observed_at="2026-07-10T18:00:01Z")
    duplicate = runtime.message_to_event(Message(), peer, "account-1", observed_at="2026-07-10T18:05:01Z")
    path = tmp_path / "raw" / "user-42" / "2026-07.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        "".join(json.dumps(value, sort_keys=True) + "\n" for value in (first, duplicate)),
        encoding="utf-8",
    )

    overlap = runtime.message_to_event(Message(), peer, "account-1", observed_at="2026-07-10T18:10:01Z")
    assert sum(runtime.append_events([overlap]).values()) == 0
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_recent_delete_detection_is_bounded_to_fetched_id_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runtime, "RAW_DIR", tmp_path / "raw")
    peer = runtime.safe_peer(User())
    previous = []
    for message_id in (76, 77, 78):
        message = Message()
        message.id = message_id
        previous.append(runtime.message_to_event(message, peer, "account-1", observed_at="2026-07-10T18:00:01Z"))
    runtime.append_events(previous)

    fetched = [previous[0], previous[2]]
    deletes = runtime.recent_delete_events(peer, fetched, raw_dir=tmp_path / "raw", observed_at="2026-07-10T18:10:01Z")

    assert [event["message"]["id"] for event in deletes] == [77]
    assert deletes[0]["event_kind"] == "delete"
    assert deletes[0]["availability"] == "deleted"


def test_runtime_env_requires_owner_only_permissions(tmp_path: Path):
    runtime_env = tmp_path / "runtime.env"
    runtime_env.write_text("TELEGRAM_API_ID=123\nTELEGRAM_API_HASH=placeholder\n", encoding="utf-8")
    runtime_env.chmod(0o644)

    with pytest.raises(runtime.RuntimePrerequisiteError, match="owner-only"):
        runtime.load_runtime_credentials(runtime_env)

    runtime_env.chmod(0o600)
    api_id, api_hash = runtime.load_runtime_credentials(runtime_env)
    assert api_id == 123
    assert api_hash == "placeholder"


def test_event_model_rejects_access_hash_in_any_nested_live_shape():
    peer = runtime.safe_peer(User())
    event = runtime.message_to_event(Message(), peer, "account-1", observed_at="2026-07-10T18:00:01Z")
    event["message"]["media"] = {"metadata": {"access_hash": "forbidden"}}

    with pytest.raises(EventValidationError, match="access_hash"):
        runtime.append_events([event])


def test_pinned_telethon_forum_topics_request_uses_peer_parameter():
    pytest.importorskip("telethon")
    _, _, functions, _ = runtime._telethon()
    parameters = inspect.signature(functions.messages.GetForumTopicsRequest).parameters
    assert "peer" in parameters
    assert "channel" not in parameters
