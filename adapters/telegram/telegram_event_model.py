"""Fixture-only Telegram lifecycle-event contract.

This module intentionally has no MTProto, credential, filesystem, network, or
GBrain dependency. It is the offline contract that a future collector must
satisfy before it can write real Telegram content.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Iterable


class EventValidationError(ValueError):
    """Raised when a candidate lifecycle event violates the safe raw contract."""


CONFIDENCE_RANK = {
    "authoritative": 4,
    "export": 3,
    "parsed": 2,
    "ocr": 1,
}


def _walk(value: Any, path: str = "$"):  # noqa: ANN401
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key == "access_hash":
                raise EventValidationError(f"access_hash is forbidden in persisted events ({child_path})")
            yield from _walk(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")
    else:
        yield path, value


def _require(mapping: dict[str, Any], key: str, label: str) -> Any:
    value = mapping.get(key)
    if value is None or value == "":
        raise EventValidationError(f"{label}.{key} is required")
    return value


def _iso(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise EventValidationError(f"{label} must be an ISO-8601 string")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EventValidationError(f"{label} must be ISO-8601") from exc
    return value


def normalize_event(candidate: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a synthetic/raw Telegram lifecycle event.

    Topic identity derives only from a stable explicit topic ID or
    ``reply_to_top_id``. An ordinary reply target is not a forum-topic ID.
    """

    if not isinstance(candidate, dict):
        raise EventValidationError("event must be an object")
    for _ in _walk(candidate):
        pass
    event = deepcopy(candidate)

    if event.get("schema_version") != 2:
        raise EventValidationError("schema_version must be 2")
    if event.get("source") != "telegram":
        raise EventValidationError("source must be telegram")
    if event.get("event_kind") not in {"upsert", "edit", "reaction_snapshot", "delete"}:
        raise EventValidationError("unsupported event_kind")
    if event.get("availability") not in {"active", "deleted", "ambiguous_delete", "inaccessible"}:
        raise EventValidationError("unsupported availability")
    _iso(_require(event, "observed_at", "event"), "observed_at")

    account_id = str(_require(event, "account_id", "event"))
    peer = event.get("peer")
    message = event.get("message")
    topic = event.get("topic")
    provenance = event.get("provenance")
    if not all(isinstance(part, dict) for part in (peer, message, topic, provenance)):
        raise EventValidationError("peer, topic, message, and provenance must be objects")

    peer_kind = str(_require(peer, "kind", "peer"))
    raw_peer_id = str(_require(peer, "raw_id", "peer"))
    message_id = int(_require(message, "id", "message"))
    _iso(_require(message, "date", "message"), "message.date")
    confidence = provenance.get("confidence")
    if confidence not in CONFIDENCE_RANK:
        raise EventValidationError("unsupported provenance.confidence")
    if provenance.get("capture_method") not in {"mtproto", "desktop_json", "desktop_html", "ocr"}:
        raise EventValidationError("unsupported provenance.capture_method")

    explicit_topic_id = topic.get("id")
    reply_to_top_id = message.get("reply_to_top_id")
    if explicit_topic_id is None and reply_to_top_id is not None:
        topic["id"] = int(reply_to_top_id)
    elif explicit_topic_id is not None:
        topic["id"] = int(explicit_topic_id)

    event["message_key"] = f"{account_id}:{peer_kind}:{raw_peer_id}:{message_id}"
    return event


def _selection_key(event: dict[str, Any]) -> tuple[int, int, str, str]:
    """Prefer terminal delete state, then evidence confidence, then newest observation."""

    terminal = 1 if event.get("availability") == "deleted" or event.get("event_kind") == "delete" else 0
    confidence = CONFIDENCE_RANK[event["provenance"]["confidence"]]
    return (terminal, confidence, str(event.get("observed_at") or ""), str(event.get("payload_sha256") or ""))


def merge_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one current lifecycle state per message key without mutating input."""

    selected: dict[str, dict[str, Any]] = {}
    for raw in events:
        event = normalize_event(raw)
        key = event["message_key"]
        previous = selected.get(key)
        if previous is None or _selection_key(event) > _selection_key(previous):
            selected[key] = event
    return [selected[key] for key in sorted(selected)]
