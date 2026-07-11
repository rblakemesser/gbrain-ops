from __future__ import annotations

import json
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


def test_message_event_uses_topic_root_not_ordinary_reply_and_has_no_access_hash():
    peer = runtime.safe_peer(User())
    event = runtime.message_to_event(Message(), peer, "account-1", observed_at="2026-07-10T18:00:01Z")

    assert event["topic"]["id"] == 505
    assert event["message"]["reply_to_top_id"] == 505
    assert event["message"]["reply_to_msg_id"] == 76
    assert event["message_key"] == "account-1:user:42:77"
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
