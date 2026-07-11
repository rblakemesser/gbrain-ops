"""MTProto runtime primitives for Telegram → GBrain.

This module deliberately has no hard-coded account identifiers and never reads
``~/.hermes/.env``. Runtime credentials come only from an owner-only integration
``runtime.env`` supplied by the operator. Persisted inventory/events exclude
Telegram access hashes and session material.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, AsyncIterator, Iterable

from telegram_event_model import normalize_event

ROOT = Path(os.environ.get("GBRAIN_OPS_TELEGRAM_ROOT", Path.home() / ".local/share/gbrain-ops/telegram")).expanduser()
DATA_DIR = ROOT / "data"
SESSION_PATH = DATA_DIR / "telegram.session"
RUNTIME_ENV_PATH = ROOT / "runtime.env"
DEFAULT_CONFIG_PATH = ROOT / "config.json"
DIALOGS_PATH = DATA_DIR / "dialogs.json"
STATE_PATH = DATA_DIR / "state.json"
RAW_DIR = DATA_DIR / "raw"
CHECKPOINTS_DIR = DATA_DIR / "checkpoints"

SECRET_FIELD_NAMES = {"access_hash", "api_hash", "phone_code_hash", "session", "session_string"}
PEER_KINDS = {"user", "chat", "supergroup", "channel"}


class RuntimePrerequisiteError(RuntimeError):
    """Raised when a live collector prerequisite is absent or unsafe."""


@dataclass(frozen=True)
class CollectorConfig:
    mode: str = "include_all_accessible"
    exclude_peer_ids: tuple[str, ...] = ()
    exclude_peer_usernames: tuple[str, ...] = ()
    exclude_title_regexes: tuple[str, ...] = ()
    include_peer_ids: tuple[str, ...] = ()
    include_archived_dialogs: bool = True
    include_muted_dialogs: bool = True
    include_private_1to1: bool = True
    include_groups: bool = True
    include_channels: bool = True
    include_bots: bool = True
    include_saved_messages: bool = True
    download_media: bool = False
    recent_lookback_days: int = 3
    recent_limit_per_peer: int = 500
    backfill_batch_size: int = 5000
    fresh_sync_overlap_messages: int = 200
    max_flood_wait_seconds: int = 900
    max_flood_wait_retries: int = 3


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as temp:
        temp.write(content)
        temp_path = Path(temp.name)
    os.replace(temp_path, path)


def atomic_write_json(path: Path, value: Any) -> None:
    _atomic_write(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_bool(value: Any, default: bool) -> bool:
    return bool(value) if value is not None else default


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> CollectorConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise RuntimePrerequisiteError(f"collector config missing: {config_path}")
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimePrerequisiteError("collector config is not valid JSON") from exc
    if raw.get("mode", "include_all_accessible") != "include_all_accessible":
        raise RuntimePrerequisiteError("only mode=include_all_accessible is supported")
    return CollectorConfig(
        mode="include_all_accessible",
        exclude_peer_ids=_string_tuple(raw.get("exclude_peer_ids")),
        exclude_peer_usernames=_string_tuple(raw.get("exclude_peer_usernames")),
        exclude_title_regexes=_string_tuple(raw.get("exclude_title_regexes")),
        include_peer_ids=_string_tuple(raw.get("include_peer_ids")),
        include_archived_dialogs=_parse_bool(raw.get("include_archived_dialogs"), True),
        include_muted_dialogs=_parse_bool(raw.get("include_muted_dialogs"), True),
        include_private_1to1=_parse_bool(raw.get("include_private_1to1"), True),
        include_groups=_parse_bool(raw.get("include_groups"), True),
        include_channels=_parse_bool(raw.get("include_channels"), True),
        include_bots=_parse_bool(raw.get("include_bots"), True),
        include_saved_messages=_parse_bool(raw.get("include_saved_messages"), True),
        download_media=_parse_bool(raw.get("download_media"), False),
        recent_lookback_days=max(1, int(raw.get("recent_lookback_days", 3))),
        recent_limit_per_peer=max(1, int(raw.get("recent_limit_per_peer", 500))),
        backfill_batch_size=max(1, int(raw.get("backfill_batch_size", 5000))),
        fresh_sync_overlap_messages=max(0, int(raw.get("fresh_sync_overlap_messages", 200))),
        max_flood_wait_seconds=max(1, int(raw.get("max_flood_wait_seconds", 900))),
        max_flood_wait_retries=max(0, int(raw.get("max_flood_wait_retries", 3))),
    )


def _dotenv_value(raw_value: str) -> str:
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_runtime_credentials(path: Path | str = RUNTIME_ENV_PATH) -> tuple[int, str]:
    """Read only required Telegram app credentials from owner-only runtime.env.

    Values are never returned to logging/CLI callers. The caller uses them only
    to initialize Telethon in memory.
    """

    env_path = Path(path)
    if not env_path.exists():
        raise RuntimePrerequisiteError(f"runtime env missing: {env_path}")
    mode = stat.S_IMODE(env_path.stat().st_mode)
    if mode & 0o077:
        raise RuntimePrerequisiteError("runtime.env must be owner-only (0600)")
    values: dict[str, str] = {}
    pattern = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = pattern.match(line)
        if not match:
            continue
        key, value = match.groups()
        if key in {"TELEGRAM_API_ID", "TELEGRAM_API_HASH"}:
            values[key] = _dotenv_value(value)
    missing = [key for key in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH") if not values.get(key)]
    if missing:
        raise RuntimePrerequisiteError("runtime.env is missing required Telegram app credential names")
    try:
        return int(values["TELEGRAM_API_ID"]), values["TELEGRAM_API_HASH"]
    except ValueError as exc:
        raise RuntimePrerequisiteError("TELEGRAM_API_ID must be numeric") from exc


def ensure_runtime_dirs() -> None:
    for path in (DATA_DIR, RAW_DIR, CHECKPOINTS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def _telethon():  # type: ignore[no-untyped-def]
    try:
        from telethon import TelegramClient, errors, functions, types
    except ImportError as exc:  # pragma: no cover - environment prerequisite
        raise RuntimePrerequisiteError("Telethon is not installed in the collector runtime") from exc
    return TelegramClient, errors, functions, types


def build_client():  # type: ignore[no-untyped-def]
    ensure_runtime_dirs()
    api_id, api_hash = load_runtime_credentials()
    TelegramClient, _, _, _ = _telethon()
    return TelegramClient(str(SESSION_PATH), api_id, api_hash)


def require_owner_only_session() -> None:
    if not SESSION_PATH.exists():
        raise RuntimePrerequisiteError("Telegram session is missing; run auth interactively")
    if stat.S_IMODE(SESSION_PATH.stat().st_mode) & 0o077:
        raise RuntimePrerequisiteError("Telegram session must be owner-only (0600)")


def peer_kind(entity: Any) -> str:
    """Normalize a Telethon entity to a stable non-secret peer kind."""
    name = type(entity).__name__
    if name == "User":
        return "user"
    if name == "Chat":
        return "chat"
    if name == "Channel":
        return "supergroup" if bool(getattr(entity, "megagroup", False)) else "channel"
    raise RuntimePrerequisiteError(f"unsupported Telegram entity type: {name}")


def entity_title(entity: Any) -> str:
    if peer_kind(entity) == "user":
        return " ".join(part for part in (getattr(entity, "first_name", None), getattr(entity, "last_name", None)) if part) or getattr(entity, "username", None) or "Telegram user"
    return str(getattr(entity, "title", None) or "Telegram chat")


def safe_peer(entity: Any) -> dict[str, str]:
    """Serialize only identity/display fields; never serialize access_hash."""
    kind = peer_kind(entity)
    raw_id = getattr(entity, "id", None)
    if raw_id is None:
        raise RuntimePrerequisiteError("Telegram entity did not expose an ID")
    return {
        "kind": kind,
        "raw_id": str(raw_id),
        "title": entity_title(entity),
        "username": str(getattr(entity, "username", None) or ""),
    }


def _excluded(peer: dict[str, str], config: CollectorConfig) -> str | None:
    if peer["raw_id"] in config.exclude_peer_ids:
        return "excluded_peer_id"
    if peer.get("username") and peer["username"] in config.exclude_peer_usernames:
        return "excluded_username"
    for expression in config.exclude_title_regexes:
        if re.search(expression, peer.get("title", ""), re.IGNORECASE):
            return "excluded_title_regex"
    if config.include_peer_ids and peer["raw_id"] not in config.include_peer_ids:
        return "not_in_explicit_include"
    kind = peer["kind"]
    if kind == "user" and not config.include_private_1to1:
        return "private_disabled"
    if kind in {"chat", "supergroup"} and not config.include_groups:
        return "groups_disabled"
    if kind == "channel" and not config.include_channels:
        return "channels_disabled"
    return None


def _dialog_row(dialog: Any, config: CollectorConfig) -> dict[str, Any]:
    peer = safe_peer(dialog.entity)
    excluded = _excluded(peer, config)
    is_archived = bool(getattr(dialog, "folder_id", None))
    is_muted = bool(getattr(getattr(dialog, "dialog", None), "notify_settings", None) and getattr(dialog.dialog.notify_settings, "mute_until", None))
    if is_archived and not config.include_archived_dialogs:
        excluded = excluded or "archived_disabled"
    if is_muted and not config.include_muted_dialogs:
        excluded = excluded or "muted_disabled"
    return {
        **peer,
        "is_forum": bool(getattr(dialog.entity, "forum", False)),
        "is_archived": is_archived,
        "is_muted": is_muted,
        "last_message_id": int(getattr(getattr(dialog, "message", None), "id", 0) or 0),
        "last_message_date": _iso_or_none(getattr(getattr(dialog, "message", None), "date", None)),
        "included": excluded is None,
        "outcome": "included" if excluded is None else excluded,
        "topics": [],
    }


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


async def iter_forum_topics(client: Any, entity: Any) -> AsyncIterator[dict[str, Any]]:
    """Yield safe forum topic metadata, paginating the raw MTProto API."""
    _, errors, functions, _ = _telethon()
    offset_date = None
    offset_id = 0
    offset_topic = 0
    while True:
        try:
            result = await client(functions.messages.GetForumTopicsRequest(
                channel=entity,
                offset_date=offset_date,
                offset_id=offset_id,
                offset_topic=offset_topic,
                limit=100,
            ))
        except (errors.ChannelPrivateError, errors.ChannelInvalidError):
            return
        topics = list(getattr(result, "topics", []) or [])
        if not topics:
            return
        for topic in topics:
            topic_id = getattr(topic, "id", None)
            if topic_id is None:
                continue
            yield {
                "id": str(topic_id),
                "title": str(getattr(topic, "title", None) or f"Topic {topic_id}"),
                "closed": bool(getattr(topic, "closed", False)),
                "hidden": bool(getattr(topic, "hidden", False)),
            }
        if len(topics) < 100:
            return
        last = topics[-1]
        offset_date = getattr(last, "date", None)
        offset_id = int(getattr(last, "top_message", 0) or 0)
        offset_topic = int(getattr(last, "id", 0) or 0)


async def inventory(client: Any, config: CollectorConfig) -> dict[str, Any]:
    """Enumerate all reachable dialogs and topic roots without collecting history."""
    rows: list[dict[str, Any]] = []
    async for dialog in client.iter_dialogs():
        row = _dialog_row(dialog, config)
        if row["included"] and row["is_forum"]:
            row["topics"] = [topic async for topic in iter_forum_topics(client, dialog.entity)]
        rows.append(row)
    rows.sort(key=lambda row: (row["kind"], row["raw_id"]))
    result = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "dialogs": rows,
        "summary": {
            "total": len(rows),
            "included": sum(1 for row in rows if row["included"]),
            "excluded_or_unavailable": sum(1 for row in rows if not row["included"]),
            "forum_topics": sum(len(row["topics"]) for row in rows),
        },
    }
    atomic_write_json(DIALOGS_PATH, result)
    return result


def _message_topic_id(message: Any) -> int | None:
    direct = getattr(message, "reply_to_top_id", None)
    if direct is not None:
        return int(direct)
    reply_to = getattr(message, "reply_to", None)
    top = getattr(reply_to, "reply_to_top_id", None)
    return int(top) if top is not None else None


def _message_reply_id(message: Any) -> int | None:
    direct = getattr(message, "reply_to_msg_id", None)
    if direct is not None:
        return int(direct)
    reply_to = getattr(message, "reply_to", None)
    reply = getattr(reply_to, "reply_to_msg_id", None)
    return int(reply) if reply is not None else None


def _sender_display(message: Any) -> str:
    sender = getattr(message, "sender", None)
    if sender is None:
        return str(getattr(message, "post_author", None) or "Unknown sender")
    if type(sender).__name__ == "User":
        return " ".join(part for part in (getattr(sender, "first_name", None), getattr(sender, "last_name", None)) if part) or str(getattr(sender, "username", None) or "Telegram user")
    return str(getattr(sender, "title", None) or getattr(sender, "username", None) or "Telegram sender")


def _media_metadata(message: Any) -> dict[str, Any] | None:
    media = getattr(message, "media", None)
    if media is None:
        return None
    document = getattr(media, "document", None)
    photo = getattr(media, "photo", None)
    return {
        "kind": type(media).__name__,
        "document_mime_type": str(getattr(document, "mime_type", None) or "") if document else "",
        "document_size": int(getattr(document, "size", 0) or 0) if document else 0,
        "has_photo": photo is not None,
    }


def message_to_event(message: Any, peer: dict[str, str], account_id: str, *, observed_at: str | None = None) -> dict[str, Any]:
    """Normalize one Telethon message to the append-only safe event contract."""
    message_id = getattr(message, "id", None)
    message_date = _iso_or_none(getattr(message, "date", None))
    if message_id is None or message_date is None:
        raise RuntimePrerequisiteError("message is missing stable ID or timestamp")
    topic_id = _message_topic_id(message)
    text = str(getattr(message, "raw_text", None) or getattr(message, "message", None) or "")
    event = {
        "schema_version": 2,
        "source": "telegram",
        "event_kind": "edit" if bool(getattr(message, "edit_date", None)) else "upsert",
        "availability": "active",
        "account_id": str(account_id),
        "observed_at": observed_at or utc_now(),
        "peer": peer,
        "topic": {"id": topic_id} if topic_id is not None else {},
        "message": {
            "id": int(message_id),
            "date": message_date,
            "text": text,
            "sender": {
                "id": str(getattr(message, "sender_id", None) or ""),
                "display_name": _sender_display(message),
            },
            "reply_to_msg_id": _message_reply_id(message),
            "reply_to_top_id": topic_id,
            "edited_at": _iso_or_none(getattr(message, "edit_date", None)),
            "media": _media_metadata(message),
        },
        "provenance": {"capture_method": "mtproto", "confidence": "authoritative"},
    }
    normalized = normalize_event(event)
    normalized["payload_sha256"] = hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return normalized


def _peer_key(peer: dict[str, str]) -> str:
    return f"{peer['kind']}-{peer['raw_id']}"


def _month(event: dict[str, Any]) -> str:
    return str(event["message"]["date"])[:7]


def append_events(events: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Append normalized lifecycle events into deterministic monthly JSONL segments."""
    grouped: dict[Path, list[dict[str, Any]]] = {}
    for raw in events:
        event = normalize_event(raw)
        path = RAW_DIR / _peer_key(event["peer"]) / f"{_month(event)}.jsonl"
        grouped.setdefault(path, []).append(event)
    counts: dict[str, int] = {}
    for path, values in grouped.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "".join(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for value in values)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(content)
        counts[str(path)] = len(values)
    return counts


def checkpoint_path(peer: dict[str, str]) -> Path:
    return CHECKPOINTS_DIR / f"{_peer_key(peer)}.json"


def load_checkpoint(peer: dict[str, str]) -> dict[str, Any]:
    path = checkpoint_path(peer)
    if not path.exists():
        return {"peer": peer, "oldest_collected_id": None, "newest_collected_id": None}
    return json.loads(path.read_text(encoding="utf-8"))


def save_checkpoint(peer: dict[str, str], events: list[dict[str, Any]], *, mode: str) -> dict[str, Any]:
    current = load_checkpoint(peer)
    ids = [int(event["message"]["id"]) for event in events]
    if ids:
        current["oldest_collected_id"] = min(ids) if current.get("oldest_collected_id") is None else min(int(current["oldest_collected_id"]), min(ids))
        current["newest_collected_id"] = max(ids) if current.get("newest_collected_id") is None else max(int(current["newest_collected_id"]), max(ids))
    current.update({"peer": peer, "last_mode": mode, "updated_at": utc_now()})
    atomic_write_json(checkpoint_path(peer), current)
    return current


async def collect_messages(client: Any, entity: Any, peer: dict[str, str], account_id: str, *, limit: int, max_id: int | None = None) -> list[dict[str, Any]]:
    """Fetch one bounded message segment with FloodWait propagated to the caller."""
    events: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"limit": limit}
    if max_id is not None:
        kwargs["max_id"] = max_id
    async for message in client.iter_messages(entity, **kwargs):
        events.append(message_to_event(message, peer, account_id))
    events.sort(key=lambda event: (event["message"]["date"], event["message"]["id"]))
    return events


async def authorized_account_id(client: Any) -> str:
    me = await client.get_me()
    account_id = getattr(me, "id", None)
    if account_id is None:
        raise RuntimePrerequisiteError("Telegram session has no authenticated user")
    return str(account_id)


async def authenticate_interactively() -> None:
    """Create/reuse the local session. Must be run from an operator-controlled TTY."""
    client = build_client()
    try:
        await client.start()
        if not await client.is_user_authorized():
            raise RuntimePrerequisiteError("Telegram authentication did not complete")
    finally:
        await client.disconnect()
    if SESSION_PATH.exists():
        SESSION_PATH.chmod(0o600)


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)
