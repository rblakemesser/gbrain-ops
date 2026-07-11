"""Fixture-only deterministic projection for validated Telegram lifecycle events."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Iterable
import json
import os

from telegram_event_model import merge_events


def _safe_component(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value.lower()).strip("-") or "unknown"


def _date_parts(iso_timestamp: str) -> tuple[str, str]:
    day = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00")).date().isoformat()
    return day[:4], day


def projection_path(event: dict) -> str:
    """Return the sole deterministic projection path for an active message."""

    year, day = _date_parts(event["message"]["date"])
    peer = event["peer"]
    peer_key = f"{_safe_component(str(peer['kind']))}-{_safe_component(str(peer['raw_id']))}"
    topic_id = event.get("topic", {}).get("id")
    if topic_id is not None:
        return f"topics/{peer_key}/topic-{int(topic_id)}/{year}/{day}.md"
    return f"chats/{peer_key}/{year}/{day}.md"


def _safe_text(value: object) -> str:
    """Render user-controlled values as one safe transcript line."""

    text = str(value or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("<!--", "< !--").replace("-->", "-- >")
    text = text.replace("```", "ˋˋˋ").replace("\x00", "")
    text = text.replace("\n", " ↩ ").replace("---", "—")
    return " ".join(text.split()).strip()


def _message_line(event: dict) -> str:
    message = event["message"]
    sender = message.get("sender") or {}
    text = _safe_text(message.get("text")) or "[non-text message]"
    date = message["date"]
    time = date[11:16] if len(date) >= 16 else "unknown"
    display_name = _safe_text(sender.get("display_name") or "Unknown sender")
    reply = message.get("reply_to_msg_id")
    suffix = f" [reply_to: {int(reply)}]" if reply is not None else ""
    # Exact GBrain telegram-bracket conversation-parser format.
    return f"**[{time}] 👤 {display_name}:** {text}{suffix}"


def _page_id(event: dict) -> str:
    _, day = _date_parts(event["message"]["date"])
    peer = event["peer"]
    topic_id = (event.get("topic") or {}).get("id")
    topic_part = str(topic_id) if topic_id is not None else "no-topic"
    return f"telegram-page:{peer['raw_id']}:{topic_part}:{day}"


def render_active_projection(events: Iterable[dict], *, source_id: str = "telegram-fixture") -> dict[str, str]:
    """Render each current active message once into parser-compatible Markdown.

    Deleted/inaccessible/ambiguous messages are intentionally omitted from the
    searchable projection. Their lifecycle evidence remains in raw events.
    """

    by_path: dict[str, list[dict]] = defaultdict(list)
    for event in merge_events(events):
        if event.get("availability") != "active":
            continue
        by_path[projection_path(event)].append(event)

    rendered: dict[str, str] = {}
    for path, page_events in sorted(by_path.items()):
        page_events.sort(key=lambda item: (item["message"]["date"], item["message"]["id"]))
        first = page_events[0]
        peer = first["peer"]
        topic = first.get("topic") or {}
        _, day = _date_parts(first["message"]["date"])
        title_parts = ["Telegram", _safe_text(peer.get("title") or "Telegram chat")]
        if topic.get("id") is not None:
            title_parts.append(_safe_text(topic.get("title") or f"Topic {topic['id']}"))
        title_parts.append(day)
        frontmatter = [
            "---",
            "type: conversation",
            f"id: {_page_id(first)}",
            f"title: {json.dumps(' — '.join(title_parts), ensure_ascii=False)}",
            "source: telegram",
            f"source_id: {json.dumps(source_id)}",
            f"telegram_peer_id: {json.dumps(str(peer['raw_id']))}",
            f"telegram_peer_type: {peer['kind']}",
            f"date: {day}",
            "timezone: Etc/UTC",
            f"message_count: {len(page_events)}",
        ]
        if topic.get("id") is not None:
            frontmatter.append(f"telegram_topic_id: {json.dumps(str(topic['id']))}")
        frontmatter.extend(["---", ""])
        body = [_message_line(event) for event in page_events]
        rendered[path] = "\n".join(frontmatter + body).rstrip() + "\n"
    return rendered
