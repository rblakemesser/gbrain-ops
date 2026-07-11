#!/usr/bin/env python3
"""Import a Telegram Desktop per-chat HTML export into the Telegram GBrain source."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from render_markdown import render_active_projection
from telegram_collector import _atomic_write, _raw_artifacts
from telegram_event_model import normalize_event

ROOT = Path(os.environ.get("GBRAIN_OPS_TELEGRAM_ROOT", Path.home() / ".local/share/gbrain-ops/telegram")).expanduser()
DEFAULT_OUTPUT_ROOT = ROOT
MESSAGE_FILE_RE = re.compile(r"^messages(?:(\d+))?\.html$")
MESSAGE_ID_RE = re.compile(r"^message(-?\d+)$")
REPLY_ID_RE = re.compile(r"(?:go_to_message|message)(-?\d+)")


def _page_sort_key(path: Path) -> int:
    match = MESSAGE_FILE_RE.match(path.name)
    if not match:
        raise ValueError(f"unsupported Telegram export page: {path.name}")
    return int(match.group(1) or 1)


def export_pages(export_root: Path) -> list[Path]:
    pages = sorted(
        (path for path in export_root.glob("messages*.html") if MESSAGE_FILE_RE.match(path.name)),
        key=_page_sort_key,
    )
    if not pages:
        raise ValueError(f"no Telegram Desktop messages*.html pages found in {export_root}")
    return pages


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _parse_timestamp(value: str) -> tuple[str, str]:
    """Convert Telegram Desktop's localized timestamp to canonical UTC."""

    match = re.fullmatch(r"(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}) UTC([+-]\d{2}:\d{2})", value.strip())
    if not match:
        raise ValueError(f"unsupported Telegram Desktop timestamp: {value!r}")
    parsed = datetime.strptime("".join(match.groups()), "%d.%m.%Y %H:%M:%S%z")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z"), match.group(2)


def _sender(display_name: str) -> dict[str, str]:
    stable_name_id = hashlib.sha256(display_name.encode("utf-8")).hexdigest()[:24]
    return {"kind": "export_name", "raw_id": f"name-{stable_name_id}", "display_name": display_name}


def _relative_attachments(message: Any, export_root: Path) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in message.select(".media_wrap a[href], .media a[href], a.photo_wrap[href], a.video_file_wrap[href], a.document_wrap[href], a.audio_file[href]"):
        href = str(anchor.get("href") or "").strip()
        if not href or href.startswith(("http://", "https://", "#")) or href in seen:
            continue
        seen.add(href)
        target = (export_root / href).resolve()
        try:
            target.relative_to(export_root.resolve())
        except ValueError:
            continue
        attachments.append({
            "path": href,
            "name": Path(href).name,
            "present": target.is_file(),
            "size": target.stat().st_size if target.is_file() else None,
        })
    return attachments


def _reply_to_id(message: Any) -> int | None:
    reply = message.select_one(".reply_to a[href]")
    if not reply:
        return None
    match = REPLY_ID_RE.search(str(reply.get("href") or ""))
    return int(match.group(1)) if match else None


def _reactions(message: Any) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for reaction in message.select(".reactions .reaction"):
        text = " ".join(reaction.get_text(" ", strip=True).split())
        if text:
            values.append({"display": text})
    return values


def parse_export(
    export_root: Path,
    *,
    account_id: str,
    peer_kind: str,
    peer_raw_id: str,
    peer_marked_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    pages = export_pages(export_root)
    events: list[dict[str, Any]] = []
    chat_title: str | None = None
    last_sender: str | None = None
    page_hashes: dict[str, str] = {}
    service_record_count = 0
    joined_message_count = 0

    for page in pages:
        payload = page.read_bytes()
        artifact_sha = _sha256_bytes(payload)
        page_hashes[page.name] = artifact_sha
        soup = BeautifulSoup(payload, "html.parser")
        service_record_count += len(soup.select("div.message.service"))
        page_title_node = soup.select_one(".page_header .text.bold")
        page_title = page_title_node.get_text(" ", strip=True) if page_title_node else ""
        if page_title:
            if chat_title is not None and page_title != chat_title:
                raise ValueError(f"chat title changed across export pages: {page_title!r}")
            chat_title = page_title

        for node in soup.select("div.message.default"):
            joined_message_count += int("joined" in (node.get("class") or []))
            id_match = MESSAGE_ID_RE.match(str(node.get("id") or ""))
            if not id_match:
                raise ValueError(f"message without stable export ID in {page.name}")
            message_id = int(id_match.group(1))
            if message_id <= 0:
                continue
            date_node = node.select_one(".date.details[title]")
            if date_node is None:
                raise ValueError(f"message {message_id} lacks an exact timestamp")
            source_date = str(date_node.get("title"))
            timestamp, export_utc_offset = _parse_timestamp(source_date)

            sender_node = node.select_one(".from_name")
            if sender_node is not None:
                last_sender = " ".join(sender_node.get_text(" ", strip=True).split())
            if not last_sender:
                raise ValueError(f"message {message_id} cannot inherit a sender")

            text_node = node.select_one(".text")
            text = text_node.get_text("\n", strip=True) if text_node is not None else ""
            attachments = _relative_attachments(node, export_root)
            media = {
                "kind": "attachments" if attachments else "none",
                "downloaded": bool(attachments) and all(item["present"] for item in attachments),
                "attachments": attachments,
            }
            candidate = {
                "schema_version": 2,
                "source": "telegram",
                "account_id": account_id,
                "event_kind": "upsert",
                "observed_at": timestamp,
                "availability": "active",
                "peer": {
                    "kind": peer_kind,
                    "raw_id": str(peer_raw_id),
                    "marked_id": str(peer_marked_id),
                    "title": chat_title or "Telegram chat",
                    "is_forum": False,
                },
                "topic": {"id": None, "title": None, "latest_message_id": None},
                "message": {
                    "id": message_id,
                    "date": timestamp,
                    "source_date": source_date,
                    "edit_date": None,
                    "sender": _sender(last_sender),
                    "text": text,
                    "raw_text": text,
                    "reply_to_msg_id": _reply_to_id(node),
                    "reply_to_top_id": None,
                    "reactions": _reactions(node),
                    "entities": [],
                    "media": media,
                },
                "provenance": {
                    "capture_method": "desktop_html",
                    "export_utc_offset": export_utc_offset,
                    "artifact": page.name,
                    "artifact_sha256": artifact_sha,
                    "collector_version": "telegram-desktop-html-v1",
                    "confidence": "export",
                },
            }
            candidate["payload_sha256"] = _sha256_bytes(
                json.dumps(candidate["message"], sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
            events.append(normalize_event(candidate))

    ids = [event["message"]["id"] for event in events]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate positive message IDs in Telegram Desktop export")
    observed_at = max((event["message"]["date"] for event in events), default=None)
    if observed_at is not None:
        events = [normalize_event({**event, "observed_at": observed_at}) for event in events]
    id_set = set(ids)
    for event in events:
        reply_id = event["message"].get("reply_to_msg_id")
        event["message"]["reply_to_resolved"] = reply_id is None or reply_id in id_set
    manifest = {
        "format": "telegram_desktop_html",
        "capture_method": "desktop_html",
        "account_id": account_id,
        "chat_title": chat_title,
        "peer_kind": peer_kind,
        "peer_raw_id": str(peer_raw_id),
        "peer_marked_id": str(peer_marked_id),
        "page_count": len(pages),
        "ordered_pages": [page.name for page in pages],
        "observed_at": observed_at,
        "timezone_policy": "Parse each explicit numeric UTC offset and normalize message.date to UTC; preserve source_date and export_utc_offset.",
        "message_count": len(events),
        "service_record_count": service_record_count,
        "joined_message_count": joined_message_count,
        "first_message_id": min(ids) if ids else None,
        "last_message_id": max(ids) if ids else None,
        "first_message_at": min((event["message"]["date"] for event in events), default=None),
        "last_message_at": max((event["message"]["date"] for event in events), default=None),
        "page_sha256": page_hashes,
    }
    return events, manifest


def import_export(export_root: Path, output_root: Path, **identity: str) -> dict[str, Any]:
    events, manifest = parse_export(export_root, **identity)
    artifacts = _raw_artifacts(events)
    artifacts.update({
        f"brain/telegram/{path}": body
        for path, body in render_active_projection(events, source_id="telegram").items()
    })
    artifacts["data/exports/ship-ship-ship-2026-07-10.manifest.json"] = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, indent=2
    ) + "\n"
    for relative_path, content in sorted(artifacts.items()):
        _atomic_write(output_root / relative_path, content)
    return {
        **{key: value for key, value in manifest.items() if key != "page_sha256"},
        "raw_artifacts": sum(path.startswith("data/raw/") for path in artifacts),
        "markdown_pages": sum(path.startswith("brain/telegram/") for path in artifacts),
        "manifest": str(output_root / "data/exports/ship-ship-ship-2026-07-10.manifest.json"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export_root", type=Path)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--peer-kind", choices=("chat", "supergroup", "channel"), required=True)
    parser.add_argument("--peer-raw-id", required=True)
    parser.add_argument("--peer-marked-id", required=True)
    args = parser.parse_args()
    summary = import_export(
        args.export_root,
        args.output_root,
        account_id=args.account_id,
        peer_kind=args.peer_kind,
        peer_raw_id=args.peer_raw_id,
        peer_marked_id=args.peer_marked_id,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
