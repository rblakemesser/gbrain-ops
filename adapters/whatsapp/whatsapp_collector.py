#!/usr/bin/env python3
"""Collect the local macOS WhatsApp cache into a deterministic private archive.

The native WhatsApp database is a local-cache source, not proof of complete
server history. This collector takes a consistent in-memory SQLite backup,
validates the Core Data shape it depends on, retains append-only lifecycle
observations, and renders a source-qualified searchable projection. It never
persists media keys, receipt blobs, remote media URLs, or full local paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

APPLE_EPOCH_OFFSET = 978_307_200
COLLECTOR_VERSION = "gbrain-ops-whatsapp/v1"
MAX_PAGE_BYTES = 72_000
PAGE_HEADER_RESERVE_BYTES = 3_000
DEFAULT_ROOT = Path(
    os.environ.get(
        "GBRAIN_OPS_WHATSAPP_ROOT",
        Path.home() / ".local" / "share" / "gbrain-ops" / "whatsapp",
    )
).expanduser()
DEFAULT_DATABASE = Path(
    os.environ.get(
        "GBRAIN_OPS_WHATSAPP_DB",
        Path.home()
        / "Library"
        / "Group Containers"
        / "group.net.whatsapp.WhatsApp.shared"
        / "ChatStorage.sqlite",
    )
).expanduser()

OTP_HINT_RE = re.compile(
    r"(?i)\b(code|verification|verify|two[- ]factor|2fa|otp|passcode|security code)\b"
)
MOSTLY_CODE_RE = re.compile(r"^[\s\-]*(?:\d[\s\-]*){4,8}$")
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

REQUIRED_COLUMNS: dict[str, set[str]] = {
    "ZWAMESSAGE": {
        "Z_PK",
        "Z_OPT",
        "ZCHATSESSION",
        "ZGROUPMEMBER",
        "ZMEDIAITEM",
        "ZPARENTMESSAGE",
        "ZMESSAGEDATE",
        "ZSENTDATE",
        "ZFROMJID",
        "ZPUSHNAME",
        "ZSTANZAID",
        "ZTEXT",
        "ZTOJID",
        "ZISFROMME",
        "ZMESSAGESTATUS",
        "ZMESSAGETYPE",
        "ZGROUPEVENTTYPE",
    },
    "ZWACHATSESSION": {
        "Z_PK",
        "ZARCHIVED",
        "ZHIDDEN",
        "ZREMOVED",
        "ZSESSIONTYPE",
        "ZCONTACTJID",
        "ZCONTACTIDENTIFIER",
        "ZPARTNERNAME",
        "ZLASTMESSAGEDATE",
    },
    "ZWAGROUPMEMBER": {
        "Z_PK",
        "ZCHATSESSION",
        "ZMEMBERJID",
        "ZCONTACTNAME",
        "ZFIRSTNAME",
        "ZISACTIVE",
        "ZISADMIN",
    },
    "ZWAMEDIAITEM": {
        "Z_PK",
        "ZFILESIZE",
        "ZMOVIEDURATION",
        "ZASPECTRATIO",
        "ZAUTHORNAME",
        "ZCOLLECTIONNAME",
        "ZMEDIALOCALPATH",
        "ZMEDIAURL",
        "ZTHUMBNAILLOCALPATH",
        "ZTITLE",
        "ZVCARDNAME",
        "ZMEDIAKEY",
        "ZMETADATA",
    },
    "ZWAMESSAGEDATAITEM": {
        "Z_PK",
        "ZINDEX",
        "ZTYPE",
        "ZMESSAGE",
        "ZCONTENT1",
        "ZCONTENT2",
        "ZMATCHEDTEXT",
        "ZSUMMARY",
        "ZTITLE",
        "ZTHUMBNAILPATH",
    },
}


class WhatsAppCollectorError(RuntimeError):
    """Base class for privacy-safe collector failures."""


class SourceSchemaError(WhatsAppCollectorError):
    """Raised when the local WhatsApp schema no longer satisfies the contract."""


class SourceInvariantError(WhatsAppCollectorError):
    """Raised when source/account identities are unsafe to merge."""


@dataclass(frozen=True)
class CollectorPaths:
    root: Path
    data_dir: Path
    raw_dir: Path
    brain_root: Path
    manifest_dir: Path
    state_path: Path
    summary_path: Path
    inventory_path: Path

    @classmethod
    def from_root(cls, root: Path | str) -> "CollectorPaths":
        root_path = Path(root).expanduser().resolve()
        data_dir = root_path / "data"
        return cls(
            root=root_path,
            data_dir=data_dir,
            raw_dir=data_dir / "raw",
            brain_root=root_path / "brain" / "daily" / "whatsapp",
            manifest_dir=data_dir / "manifests",
            state_path=data_dir / "state.json",
            summary_path=data_dir / "sync-summary.json",
            inventory_path=data_dir / "chats.json",
        )


def _private_dir(path: Path) -> None:
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    for created in missing:
        created.chmod(0o700)
    path.chmod(0o700)


def ensure_runtime_dirs(paths: CollectorPaths) -> None:
    for path in (
        paths.root,
        paths.data_dir,
        paths.raw_dir,
        paths.brain_root,
        paths.manifest_dir,
    ):
        _private_dir(path)


def _atomic_write(path: Path, content: str, *, skip_unchanged: bool = False) -> bool:
    _private_dir(path.parent)
    encoded = content.encode("utf-8")
    if skip_unchanged and path.exists() and path.read_bytes() == encoded:
        path.chmod(0o600)
        return False
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return True


def _atomic_json(path: Path, value: Any, *, skip_unchanged: bool = False) -> bool:
    return _atomic_write(
        path,
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        skip_unchanged=skip_unchanged,
    )


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SourceInvariantError(f"invalid private state file: {path.name}") from exc


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _fingerprint(value: str, length: int = 24) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _source_timestamp(value: Any) -> str:
    if value is None:
        return ""
    try:
        unix_timestamp = float(value) + APPLE_EPOCH_OFFSET
        return datetime.fromtimestamp(unix_timestamp, tz=UTC).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _apple_timestamp(value: datetime) -> float:
    return value.astimezone(UTC).timestamp() - APPLE_EPOCH_OFFSET


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    text = text.encode("utf-8", errors="replace").decode("utf-8")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return CONTROL_RE.sub(" ", text).strip()


def _safe_line(value: Any) -> str:
    text = _clean_text(value)
    text = text.replace("<!--", "< !--").replace("-->", "-- >")
    text = text.replace("```", "ˋˋˋ").replace("---", "—")
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    text = text.replace("\n", " ↩ ")
    return " ".join(text.split()).strip()


def _basename(value: Any) -> str:
    raw = _clean_text(value)
    return Path(raw).name if raw else ""


def _chat_kind(jid: str, session_type: int) -> str:
    if jid.endswith("@g.us"):
        return "group"
    if jid.endswith("@status"):
        return "status"
    if jid.endswith("@broadcast"):
        return "broadcast"
    if jid.endswith("@lid"):
        return "direct-lid"
    if jid.endswith("@s.whatsapp.net"):
        return "direct"
    return f"session-{session_type}"


def _classification(text: str, *, has_media: bool) -> dict[str, Any]:
    labels: set[str] = set()
    redacted = False
    stripped = text.strip()
    if (OTP_HINT_RE.search(stripped) and len(stripped) <= 240) or (
        len(stripped) <= 24 and MOSTLY_CODE_RE.fullmatch(stripped)
    ):
        labels.add("sensitive-otp-or-code")
        redacted = True
    if has_media:
        labels.add("has-attachment")
    if URL_RE.search(stripped):
        labels.add("has-link")
    if len(stripped) > 120:
        labels.add("substantive")
    if any(
        keyword in stripped.lower()
        for keyword in (
            "address",
            "reservation",
            "appointment",
            "flight",
            "hotel",
            "order",
            "receipt",
            "meeting",
            "dinner",
            "lunch",
            "birthday",
            "wedding",
        )
    ):
        labels.add("planning-or-admin")
    if len(stripped) <= 12 and stripped.lower() in {
        "ok",
        "okay",
        "yes",
        "no",
        "lol",
        "thanks",
        "thank you",
        "👍",
        "❤️",
    }:
        labels.add("low-signal")
    return {"labels": sorted(labels), "redacted_in_brain": redacted}


def open_consistent_snapshot(database: Path) -> sqlite3.Connection:
    database = database.expanduser().resolve()
    if not database.is_file():
        raise SourceSchemaError("WhatsApp ChatStorage database is unavailable")
    source = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=30)
    snapshot = sqlite3.connect(":memory:")
    try:
        source.backup(snapshot)
    except Exception:
        snapshot.close()
        raise
    finally:
        source.close()
    snapshot.row_factory = sqlite3.Row
    snapshot.execute("PRAGMA query_only=ON")
    return snapshot


def validate_schema(connection: sqlite3.Connection) -> str:
    check = connection.execute("PRAGMA quick_check").fetchone()
    if not check or check[0] != "ok":
        raise SourceSchemaError("required WhatsApp schema failed SQLite integrity validation")
    schema_rows: list[tuple[str, str]] = []
    for table, required in REQUIRED_COLUMNS.items():
        table_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not table_row:
            raise SourceSchemaError(f"required WhatsApp schema table is missing: {table}")
        columns = {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}
        missing = sorted(required - columns)
        if missing:
            raise SourceSchemaError(
                f"required WhatsApp schema columns are missing from {table}: {', '.join(missing)}"
            )
        schema_rows.append((table, str(table_row[0] or "")))
    return _sha256_json(schema_rows)


def account_fingerprint(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        """
        SELECT ZFROMJID, COUNT(*) AS message_count
        FROM ZWAMESSAGE
        WHERE ZISFROMME=1 AND ZFROMJID IS NOT NULL AND ZFROMJID<>''
        GROUP BY ZFROMJID
        ORDER BY message_count DESC, ZFROMJID ASC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        row = connection.execute(
            """
            SELECT ZTOJID, COUNT(*) AS message_count
            FROM ZWAMESSAGE
            WHERE ZISFROMME=0 AND ZTOJID IS NOT NULL AND ZTOJID<>''
            GROUP BY ZTOJID
            ORDER BY message_count DESC, ZTOJID ASC
            LIMIT 1
            """
        ).fetchone()
    if not row or not str(row[0] or ""):
        raise SourceInvariantError("unable to bind the WhatsApp archive to a local account")
    return _fingerprint(str(row[0]), 24)


def build_inventory(connection: sqlite3.Connection, account_id: str) -> dict[str, Any]:
    chats: dict[str, dict[str, Any]] = {}
    chat_rows = connection.execute(
        """
        SELECT Z_PK,ZARCHIVED,ZHIDDEN,ZREMOVED,ZSESSIONTYPE,ZCONTACTJID,
               ZCONTACTIDENTIFIER,ZPARTNERNAME,ZLASTMESSAGEDATE
        FROM ZWACHATSESSION
        ORDER BY Z_PK
        """
    )
    for row in chat_rows:
        jid = _clean_text(row[5])
        if not jid:
            raise SourceInvariantError("WhatsApp chat is missing a stable JID")
        key = _fingerprint(jid)
        chats[key] = {
            "fingerprint": key,
            "jid": jid,
            "display_name": _clean_text(row[7]) or _clean_text(row[6]),
            "kind": _chat_kind(jid, int(row[4] or 0)),
            "session_type": int(row[4] or 0),
            "archived": bool(row[1]),
            "hidden": bool(row[2]),
            "removed": bool(row[3]),
            "last_message_at": _source_timestamp(row[8]),
        }

    participants: dict[str, dict[str, Any]] = {}
    member_rows = connection.execute(
        """
        SELECT ZMEMBERJID,ZCONTACTNAME,ZFIRSTNAME,ZISACTIVE,ZISADMIN
        FROM ZWAGROUPMEMBER
        WHERE ZMEMBERJID IS NOT NULL AND ZMEMBERJID<>''
        ORDER BY Z_PK
        """
    )
    for row in member_rows:
        jid = _clean_text(row[0])
        key = _fingerprint(jid)
        participants[key] = {
            "fingerprint": key,
            "jid": jid,
            "display_name": _clean_text(row[1]) or _clean_text(row[2]),
            "active": bool(row[3]),
            "admin": bool(row[4]),
        }

    push_rows = connection.execute(
        """
        SELECT ZFROMJID,ZPUSHNAME,MAX(ZMESSAGEDATE)
        FROM ZWAMESSAGE
        WHERE ZFROMJID IS NOT NULL AND ZFROMJID<>''
        GROUP BY ZFROMJID
        """
    )
    for row in push_rows:
        jid = _clean_text(row[0])
        key = _fingerprint(jid)
        current = participants.get(key, {})
        display_name = current.get("display_name") or _clean_text(row[1])
        participants[key] = {
            "fingerprint": key,
            "jid": jid,
            "display_name": display_name,
            "active": current.get("active"),
            "admin": current.get("admin"),
        }

    return {
        "schema": "gbrain-ops-whatsapp-inventory/v1",
        "account_fingerprint": account_id,
        "chats": chats,
        "participants": participants,
    }


def _media_value(row: sqlite3.Row) -> dict[str, Any] | None:
    if row["media_pk"] is None:
        return None
    aspect_ratio = float(row["media_aspect_ratio"] or 0)
    if not math.isfinite(aspect_ratio):
        aspect_ratio = 0.0
    return {
        "present": True,
        "file_size": max(0, int(row["media_size"] or 0)),
        "duration_seconds": max(0, int(row["media_duration"] or 0)),
        "aspect_ratio": aspect_ratio,
        "author": _clean_text(row["media_author"]),
        "collection": _clean_text(row["media_collection"]),
        "title": _clean_text(row["media_title"]),
        "vcard_name": _clean_text(row["media_vcard_name"]),
        "local_name": _basename(row["media_local_path"]),
        "thumbnail_name": _basename(row["media_thumbnail_path"]),
        "local_reference_present": bool(_clean_text(row["media_local_path"])),
        "remote_reference_present": bool(_clean_text(row["media_url"])),
    }


def _data_items(connection: sqlite3.Connection, message_pks: list[int]) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for offset in range(0, len(message_pks), 800):
        batch = message_pks[offset : offset + 800]
        if not batch:
            continue
        placeholders = ",".join("?" for _ in batch)
        rows = connection.execute(
            f"""
            SELECT ZMESSAGE,ZINDEX,ZTYPE,ZCONTENT1,ZCONTENT2,ZMATCHEDTEXT,
                   ZSUMMARY,ZTITLE,ZTHUMBNAILPATH
            FROM ZWAMESSAGEDATAITEM
            WHERE ZMESSAGE IN ({placeholders})
            ORDER BY ZMESSAGE,ZINDEX,Z_PK
            """,
            batch,
        )
        for row in rows:
            result[int(row[0])].append(
                {
                    "index": int(row[1] or 0),
                    "source_type": int(row[2] or 0),
                    "content_1": _clean_text(row[3]),
                    "content_2": _clean_text(row[4]),
                    "matched_text": _clean_text(row[5]),
                    "summary": _clean_text(row[6]),
                    "title": _clean_text(row[7]),
                    "thumbnail_name": _basename(row[8]),
                }
            )
    return result


def _message_rows(
    connection: sqlite3.Connection,
    *,
    mode: str,
    last_pk: int,
    recent_days: int,
    now: datetime,
) -> list[sqlite3.Row]:
    if mode == "backfill":
        where = ""
        parameters: list[Any] = []
    elif mode == "recent":
        cutoff = _apple_timestamp(now - timedelta(days=recent_days))
        where = "WHERE (m.Z_PK>? OR m.ZMESSAGEDATE>=?)"
        parameters = [last_pk, cutoff]
    else:
        raise ValueError("mode must be backfill or recent")
    return list(
        connection.execute(
            f"""
            SELECT
              m.Z_PK AS source_pk,
              m.Z_OPT AS source_version,
              m.ZSTANZAID AS stanza_id,
              m.ZMESSAGEDATE AS message_date,
              m.ZSENTDATE AS sent_date,
              m.ZTEXT AS message_text,
              m.ZMESSAGETYPE AS message_type,
              m.ZMESSAGESTATUS AS message_status,
              m.ZGROUPEVENTTYPE AS group_event_type,
              m.ZISFROMME AS is_from_me,
              m.ZFROMJID AS from_jid,
              m.ZTOJID AS to_jid,
              m.ZPUSHNAME AS push_name,
              c.Z_PK AS chat_pk,
              c.ZCONTACTJID AS chat_jid,
              c.ZPARTNERNAME AS chat_name,
              c.ZCONTACTIDENTIFIER AS chat_identifier,
              c.ZSESSIONTYPE AS session_type,
              gm.ZMEMBERJID AS member_jid,
              parent.ZSTANZAID AS parent_stanza_id,
              mi.Z_PK AS media_pk,
              mi.ZFILESIZE AS media_size,
              mi.ZMOVIEDURATION AS media_duration,
              mi.ZASPECTRATIO AS media_aspect_ratio,
              mi.ZAUTHORNAME AS media_author,
              mi.ZCOLLECTIONNAME AS media_collection,
              mi.ZMEDIALOCALPATH AS media_local_path,
              mi.ZMEDIAURL AS media_url,
              mi.ZTHUMBNAILLOCALPATH AS media_thumbnail_path,
              mi.ZTITLE AS media_title,
              mi.ZVCARDNAME AS media_vcard_name
            FROM ZWAMESSAGE m
            JOIN ZWACHATSESSION c ON c.Z_PK=m.ZCHATSESSION
            LEFT JOIN ZWAGROUPMEMBER gm ON gm.Z_PK=m.ZGROUPMEMBER
            LEFT JOIN ZWAMESSAGE parent ON parent.Z_PK=m.ZPARENTMESSAGE
            LEFT JOIN ZWAMEDIAITEM mi ON mi.Z_PK=m.ZMEDIAITEM
            {where}
            ORDER BY m.ZMESSAGEDATE,m.Z_PK
            """,
            parameters,
        )
    )


def _stable_payload(event: dict[str, Any]) -> dict[str, Any]:
    message = event["message"]
    return {
        "source": event["source"],
        "account_fingerprint": event["account_fingerprint"],
        "chat_jid": event["chat"]["jid"],
        "message_id": message["id"],
        "date": message["date"],
        "sent_at": message["sent_at"],
        "text": message["text"],
        "is_from_me": message["is_from_me"],
        "sender_jid": message["sender"]["jid"],
        "reply_to_id": message["reply_to_id"],
        "source_type": message["source_type"],
        "group_event_type": message["group_event_type"],
        "media": message["media"],
        "data_items": message["data_items"],
    }


def normalize_rows(
    connection: sqlite3.Connection,
    rows: list[sqlite3.Row],
    *,
    account_id: str,
    schema_hash: str,
    observed_at: str,
    observation_sequence: int,
    previous_payloads: dict[str, str],
) -> tuple[list[dict[str, Any]], int]:
    message_pks = [int(row["source_pk"]) for row in rows]
    data_items = _data_items(connection, message_pks)
    seen_keys: set[str] = set()
    events: list[dict[str, Any]] = []
    unchanged = 0
    for row in rows:
        stanza_id = _clean_text(row["stanza_id"])
        chat_jid = _clean_text(row["chat_jid"])
        if not stanza_id or not chat_jid:
            raise SourceInvariantError("WhatsApp message is missing a stable chat/stanza identity")
        chat_id = _fingerprint(chat_jid)
        message_key = f"{account_id}:{chat_id}:{stanza_id}"
        if message_key in seen_keys:
            raise SourceInvariantError("duplicate stable message identity in WhatsApp snapshot")
        seen_keys.add(message_key)
        sender_jid = _clean_text(row["member_jid"] or row["from_jid"])
        media = _media_value(row)
        text = _clean_text(row["message_text"])
        event: dict[str, Any] = {
            "schema_version": 1,
            "source": "whatsapp",
            "event_kind": "upsert",
            "availability": "active",
            "account_fingerprint": account_id,
            "observed_at": observed_at,
            "observation_sequence": observation_sequence,
            "message_key": message_key,
            "chat": {
                "jid": chat_jid,
                "fingerprint": chat_id,
                "kind": _chat_kind(chat_jid, int(row["session_type"] or 0)),
            },
            "message": {
                "id": stanza_id,
                "source_pk": int(row["source_pk"]),
                "source_version": int(row["source_version"] or 0),
                "date": _source_timestamp(row["message_date"]),
                "sent_at": _source_timestamp(row["sent_date"]),
                "text": text,
                "is_from_me": bool(row["is_from_me"]),
                "sender": {
                    "jid": sender_jid,
                    "fingerprint": _fingerprint(sender_jid) if sender_jid else "",
                },
                "reply_to_id": _clean_text(row["parent_stanza_id"]) or None,
                "source_type": int(row["message_type"] or 0),
                "source_status": int(row["message_status"] or 0),
                "group_event_type": int(row["group_event_type"] or 0),
                "media": media,
                "data_items": data_items.get(int(row["source_pk"]), []),
                "classification": _classification(text, has_media=media is not None),
            },
            "provenance": {
                "capture_method": "whatsapp_macos_coredata_snapshot",
                "confidence": "authoritative_local_cache",
                "collector_version": COLLECTOR_VERSION,
                "schema_sha256": schema_hash,
            },
        }
        payload_hash = _sha256_json(_stable_payload(event))
        event["payload_sha256"] = payload_hash
        previous = previous_payloads.get(message_key)
        if previous == payload_hash:
            unchanged += 1
            continue
        if previous is not None:
            event["event_kind"] = "edit"
        events.append(event)
    return events, unchanged


def load_all_events(raw_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not raw_dir.exists():
        return events
    for path in sorted(raw_dir.glob("*.jsonl")):
        # Iterate on physical newlines only. str.splitlines() also splits on
        # U+2028/U+2029, both of which are valid inside a JSON string and occur
        # in real WhatsApp message text.
        with path.open("r", encoding="utf-8", newline="") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise SourceInvariantError(f"invalid raw event at {path.name}:{line_number}") from exc
                if not isinstance(value, dict) or not value.get("message_key") or not value.get("payload_sha256"):
                    raise SourceInvariantError(f"invalid raw event contract at {path.name}:{line_number}")
                events.append(value)
    return events


def _latest_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    selection_by_key: dict[str, tuple[int, str, int]] = {}
    for index, event in enumerate(events):
        key = str(event["message_key"])
        selection = (
            int(event.get("observation_sequence") or 0),
            str(event.get("observed_at") or ""),
            index,
        )
        if key not in selection_by_key or selection > selection_by_key[key]:
            latest[key] = event
            selection_by_key[key] = selection
    return [latest[key] for key in sorted(latest)]


def write_raw_events(paths: CollectorPaths, events: list[dict[str, Any]]) -> tuple[int, list[str]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        date = str(event["message"]["date"])
        if len(date) < 7:
            raise SourceInvariantError("WhatsApp event has no usable canonical timestamp")
        grouped[date[:7]].append(event)
    written = 0
    changed: list[str] = []
    for month, additions in sorted(grouped.items()):
        path = paths.raw_dir / f"{month}.jsonl"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        content = existing
        if content and not content.endswith("\n"):
            raise SourceInvariantError(f"raw event file is not newline-terminated: {path.name}")
        for event in sorted(
            additions,
            key=lambda item: (
                item["message"]["date"],
                item["message_key"],
                item["payload_sha256"],
            ),
        ):
            content += json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
            written += 1
        if _atomic_write(path, content, skip_unchanged=True):
            changed.append(path.name)
    return written, changed


def _inventory_display(inventory: dict[str, Any], event: dict[str, Any]) -> tuple[str, str]:
    chat_key = str(event["chat"]["fingerprint"])
    chat = (inventory.get("chats") or {}).get(chat_key) or {}
    chat_name = _safe_line(chat.get("display_name")) or f"WhatsApp chat {chat_key[:8]}"
    message = event["message"]
    if message.get("is_from_me"):
        return chat_name, "Me"
    sender_key = str((message.get("sender") or {}).get("fingerprint") or "")
    participant = (inventory.get("participants") or {}).get(sender_key) or {}
    sender_name = _safe_line(participant.get("display_name"))
    if not sender_name and event["chat"].get("kind", "").startswith("direct"):
        sender_name = chat_name
    if not sender_name:
        sender_name = f"WhatsApp participant {sender_key[:8] or 'unknown'}"
    return chat_name, sender_name


def _media_description(media: dict[str, Any] | None, source_type: int) -> str:
    if media is None:
        return f"non-text WhatsApp message; source_type={source_type}"
    details = ["WhatsApp attachment", f"source_type={source_type}"]
    if media.get("title"):
        details.append(f"title={_safe_line(media['title'])}")
    if media.get("file_size"):
        details.append(f"size={int(media['file_size'])} bytes")
    if media.get("duration_seconds"):
        details.append(f"duration={int(media['duration_seconds'])}s")
    if media.get("local_name"):
        details.append(f"file={_safe_line(media['local_name'])}")
    return "; ".join(details)


def _split_utf8(text: str, limit: int) -> list[str]:
    if len(text.encode("utf-8")) <= limit:
        return [text]
    result: list[str] = []
    current = ""
    for token in re.findall(r"\S+\s*", text):
        if len(token.encode("utf-8")) > limit:
            if current:
                result.append(current.rstrip())
                current = ""
            token_part = ""
            token_size = 0
            for character in token:
                size = len(character.encode("utf-8"))
                if token_part and token_size + size > limit:
                    result.append(token_part)
                    token_part = ""
                    token_size = 0
                token_part += character
                token_size += size
            if token_part:
                current = token_part
            continue
        if current and len((current + token).encode("utf-8")) > limit:
            result.append(current.rstrip())
            current = ""
        current += token
    if current:
        result.append(current.rstrip())
    return result


def _message_lines(event: dict[str, Any], inventory: dict[str, Any]) -> list[str]:
    _, sender_name = _inventory_display(inventory, event)
    message = event["message"]
    timestamp = str(message.get("date") or "")
    time = timestamp[11:16] if len(timestamp) >= 16 else "unknown"
    classification = message.get("classification") or {}
    if classification.get("redacted_in_brain"):
        text = "[redacted: likely verification/security code]"
    else:
        text = _safe_line(message.get("text"))
    media = message.get("media")
    source_type = int(message.get("source_type") or 0)
    if not text:
        text = f"[{_media_description(media, source_type)}]"
    elif media is not None:
        text += f" [{_media_description(media, source_type)}]"
    reply = message.get("reply_to_id")
    suffix = f" [reply_to: {_safe_line(reply)}]" if reply else ""
    labels = ", ".join(classification.get("labels") or [])
    if labels:
        suffix += f" _[{_safe_line(labels)}]_"
    prefix = f"**[{time}] 👤 {_safe_line(sender_name)}:** "
    available = MAX_PAGE_BYTES - PAGE_HEADER_RESERVE_BYTES - len((prefix + suffix + "\n").encode("utf-8"))
    chunks = _split_utf8(text, max(1_024, available))
    if len(chunks) == 1:
        return [prefix + chunks[0] + suffix]
    return [
        prefix + chunk + f" [message_part: {index}/{len(chunks)}]" + suffix
        for index, chunk in enumerate(chunks, 1)
    ]


def _page_header(
    *,
    event: dict[str, Any],
    inventory: dict[str, Any],
    day: str,
    source_id: str,
    part: int,
    total_parts: int,
    line_count: int,
) -> str:
    chat_name, _ = _inventory_display(inventory, event)
    chat_key = str(event["chat"]["fingerprint"])
    part_title = f" — part {part}/{total_parts}" if total_parts > 1 else ""
    title = f"WhatsApp — {chat_name} — {day}{part_title}"
    page_id = f"whatsapp-page:{chat_key}:{day}:{part}"
    return "\n".join(
        [
            "---",
            "type: conversation",
            f"id: {json.dumps(page_id, ensure_ascii=False)}",
            f"title: {json.dumps(title, ensure_ascii=False)}",
            "source: whatsapp",
            f"source_id: {json.dumps(source_id, ensure_ascii=False)}",
            f"whatsapp_chat_key: {json.dumps(chat_key)}",
            f"date: {day}",
            "timezone: Etc/UTC",
            f"message_line_count: {line_count}",
            f"part: {part}",
            f"parts: {total_parts}",
            "---",
            "",
        ]
    )


def render_active_projection(
    events: Iterable[dict[str, Any]],
    inventory: dict[str, Any],
    *,
    source_id: str = "whatsapp",
) -> dict[str, str]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in _latest_events(events):
        if event.get("availability") != "active":
            continue
        timestamp = str(event["message"].get("date") or "")
        if len(timestamp) < 10:
            raise SourceInvariantError("WhatsApp event has no renderable canonical date")
        grouped[(str(event["chat"]["fingerprint"]), timestamp[:10])].append(event)

    rendered: dict[str, str] = {}
    target_body_size = MAX_PAGE_BYTES - PAGE_HEADER_RESERVE_BYTES
    for (chat_key, day), page_events in sorted(grouped.items()):
        page_events.sort(
            key=lambda event: (
                str(event["message"].get("date") or ""),
                int(event["message"].get("source_pk") or 0),
                str(event["message"].get("id") or ""),
            )
        )
        lines: list[tuple[dict[str, Any], str]] = []
        for event in page_events:
            lines.extend((event, line) for line in _message_lines(event, inventory))
        parts: list[list[tuple[dict[str, Any], str]]] = []
        current: list[tuple[dict[str, Any], str]] = []
        current_size = 0
        for event, line in lines:
            line_size = len((line + "\n").encode("utf-8"))
            if current and current_size + line_size > target_body_size:
                parts.append(current)
                current = []
                current_size = 0
            current.append((event, line))
            current_size += line_size
        if current:
            parts.append(current)
        total_parts = len(parts)
        for part_index, part_lines in enumerate(parts, 1):
            first = part_lines[0][0]
            header = _page_header(
                event=first,
                inventory=inventory,
                day=day,
                source_id=source_id,
                part=part_index,
                total_parts=total_parts,
                line_count=len(part_lines),
            )
            content = header + "\n".join(line for _, line in part_lines) + "\n"
            if len(content.encode("utf-8")) > MAX_PAGE_BYTES:
                raise SourceInvariantError("rendered WhatsApp page exceeds the configured byte limit")
            year = day[:4]
            file_name = f"{day}.md" if total_parts == 1 else f"{day}--part-{part_index:03d}.md"
            rendered[f"chats/{chat_key}/{year}/{file_name}"] = content
    return rendered


def reconcile_rendered_pages(paths: CollectorPaths, desired: dict[str, str]) -> tuple[int, int, list[str]]:
    written = 0
    changed: list[str] = []
    desired_paths = {paths.brain_root / relative for relative in desired}
    for relative, content in sorted(desired.items()):
        path = paths.brain_root / relative
        if _atomic_write(path, content, skip_unchanged=True):
            written += 1
            changed.append(relative)
    removed = 0
    for path in sorted(paths.brain_root.rglob("*.md")):
        if path not in desired_paths:
            path.unlink()
            removed += 1
    return written, removed, changed


def _source_metrics(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT
          COUNT(*),
          COALESCE(MAX(m.Z_PK),0),
          MAX(m.ZMESSAGEDATE),
          SUM(CASE WHEN m.ZSTANZAID IS NULL OR trim(m.ZSTANZAID)='' THEN 1 ELSE 0 END),
          SUM(CASE WHEN c.Z_PK IS NULL OR c.ZCONTACTJID IS NULL OR trim(c.ZCONTACTJID)='' THEN 1 ELSE 0 END),
          SUM(CASE WHEN m.ZMESSAGEDATE IS NULL THEN 1 ELSE 0 END)
        FROM ZWAMESSAGE m
        LEFT JOIN ZWACHATSESSION c ON c.Z_PK=m.ZCHATSESSION
        """
    ).fetchone()
    return {
        "source_row_count": int(row[0] or 0),
        "source_max_pk": int(row[1] or 0),
        "observed_head_at": _source_timestamp(row[2]),
        "missing_stanza_ids": int(row[3] or 0),
        "missing_chat_identities": int(row[4] or 0),
        "missing_message_dates": int(row[5] or 0),
    }


def _validate_source_invariants(
    connection: sqlite3.Connection,
    metrics: dict[str, Any],
) -> None:
    if int(metrics["missing_stanza_ids"]):
        raise SourceInvariantError("WhatsApp contains messages without stable stanza IDs")
    if int(metrics["missing_chat_identities"]):
        raise SourceInvariantError("WhatsApp contains messages without stable chat identities")
    if int(metrics["missing_message_dates"]):
        raise SourceInvariantError("WhatsApp contains messages without canonical timestamps")
    duplicate = connection.execute(
        """
        SELECT COUNT(*) FROM (
          SELECT c.ZCONTACTJID,m.ZSTANZAID,COUNT(*) AS n
          FROM ZWAMESSAGE m
          JOIN ZWACHATSESSION c ON c.Z_PK=m.ZCHATSESSION
          GROUP BY c.ZCONTACTJID,m.ZSTANZAID
          HAVING n>1
        )
        """
    ).fetchone()[0]
    if duplicate:
        raise SourceInvariantError("duplicate stable message identity in WhatsApp snapshot")


def _manifest_name(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ.json")


def run_pipeline(
    *,
    database: Path | str = DEFAULT_DATABASE,
    root: Path | str = DEFAULT_ROOT,
    mode: str,
    recent_days: int = 7,
    source_id: str = "whatsapp",
    now: datetime | None = None,
) -> dict[str, Any]:
    if recent_days < 1:
        raise ValueError("recent_days must be positive")
    run_time = (now or datetime.now(UTC)).astimezone(UTC)
    observed_at = run_time.isoformat().replace("+00:00", "Z")
    paths = CollectorPaths.from_root(root)
    state = _load_json(paths.state_path, {})

    connection = open_consistent_snapshot(Path(database))
    try:
        schema_hash = validate_schema(connection)
        account_id = account_fingerprint(connection)
        prior_account = state.get("account_fingerprint") if isinstance(state, dict) else None
        if prior_account and prior_account != account_id:
            raise SourceInvariantError("WhatsApp account fingerprint changed; refusing to mix archives")
        metrics = _source_metrics(connection)
        _validate_source_invariants(connection, metrics)
        last_pk = int(state.get("last_pk") or 0) if isinstance(state, dict) else 0
        effective_mode = mode
        if mode == "recent" and int(metrics["source_max_pk"]) < last_pk:
            effective_mode = "backfill"
        rows = _message_rows(
            connection,
            mode=effective_mode,
            last_pk=last_pk,
            recent_days=recent_days,
            now=run_time,
        )
        if effective_mode == "backfill" and len(rows) != int(metrics["source_row_count"]):
            raise SourceInvariantError(
                "full WhatsApp snapshot did not preserve every message-to-chat relationship"
            )
        inventory = build_inventory(connection, account_id)
        existing_events = load_all_events(paths.raw_dir)
        prior_sequence = max(
            [int(state.get("observation_sequence") or 0)]
            + [int(event.get("observation_sequence") or 0) for event in existing_events]
        )
        observation_sequence = prior_sequence + 1
        previous_payloads = {
            str(event["message_key"]): str(event["payload_sha256"])
            for event in _latest_events(existing_events)
        }
        events, unchanged = normalize_rows(
            connection,
            rows,
            account_id=account_id,
            schema_hash=schema_hash,
            observed_at=observed_at,
            observation_sequence=observation_sequence,
            previous_payloads=previous_payloads,
        )
    finally:
        connection.close()

    ensure_runtime_dirs(paths)
    appended, changed_raw = write_raw_events(paths, events)
    all_events = existing_events + events
    desired = render_active_projection(all_events, inventory, source_id=source_id)
    pages_written, pages_removed, changed_pages = reconcile_rendered_pages(paths, desired)

    inventory_value = {**inventory, "generated_at": observed_at}
    _atomic_json(paths.inventory_path, inventory_value, skip_unchanged=False)
    next_state = {
        "schema": "gbrain-ops-whatsapp-state/v1",
        "account_fingerprint": account_id,
        "schema_sha256": schema_hash,
        "observation_sequence": observation_sequence,
        "last_pk": int(metrics["source_max_pk"]),
        "last_success_at": observed_at,
        "last_mode": mode,
        "source_row_count": int(metrics["source_row_count"]),
        "observed_head_at": metrics["observed_head_at"],
        "completeness": "local_cache_snapshot_only",
    }
    _atomic_json(paths.state_path, next_state)

    summary = {
        "status": "ok",
        "source_id": source_id,
        "mode": mode,
        "effective_mode": effective_mode,
        "rows_scanned": len(rows),
        "events_appended": appended,
        "unchanged_observations": unchanged,
        "pages_total": len(desired),
        "pages_written": pages_written,
        "pages_removed": pages_removed,
        "source_row_count": int(metrics["source_row_count"]),
        "source_max_pk": int(metrics["source_max_pk"]),
        "observed_head_at": metrics["observed_head_at"],
        "complete_scope": "local_cache_snapshot_only",
    }
    _atomic_json(paths.summary_path, summary)
    manifest = {
        "schema": "gbrain-ops-whatsapp-run/v1",
        "generated_at": observed_at,
        "source_id": source_id,
        "mode": mode,
        "effective_mode": effective_mode,
        "account_fingerprint": account_id,
        "schema_sha256": schema_hash,
        "observation_sequence": observation_sequence,
        "source": metrics,
        "result": summary,
        "changed_artifacts": {
            "raw": changed_raw,
            "pages": changed_pages,
            "removed_pages": pages_removed,
        },
        "errors": 0,
        "partial": False,
    }
    _atomic_json(paths.manifest_dir / _manifest_name(run_time), manifest)
    return summary


def validate_source(database: Path | str = DEFAULT_DATABASE) -> dict[str, Any]:
    connection = open_consistent_snapshot(Path(database))
    try:
        schema_hash = validate_schema(connection)
        account_id = account_fingerprint(connection)
        metrics = _source_metrics(connection)
        _validate_source_invariants(connection, metrics)
        return {
            "status": "ok",
            "schema_sha256": schema_hash,
            "account_fingerprint": account_id,
            **metrics,
            "complete_scope": "local_cache_snapshot_only",
        }
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect macOS WhatsApp messages into a private GBrain archive")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--source-id", default="whatsapp")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    recent = subparsers.add_parser("recent")
    recent.add_argument("--days", type=int, default=7)
    subparsers.add_parser("backfill")
    args = parser.parse_args()
    try:
        if args.command == "validate":
            result = validate_source(args.database)
        else:
            result = run_pipeline(
                database=args.database,
                root=args.root,
                mode=args.command,
                recent_days=getattr(args, "days", 7),
                source_id=args.source_id,
            )
    except (OSError, sqlite3.Error, WhatsAppCollectorError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "source_id": args.source_id,
                    "error_class": type(exc).__name__,
                    "error": str(exc),
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
