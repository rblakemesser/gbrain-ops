#!/usr/bin/env python3
"""Collect Plaud recordings through the official MCP package.

The collector keeps Plaud source identity and revisions intact: it archives a
normalized current snapshot, writes an immutable revision for every semantic
source hash, and renders one stable GBrain Markdown page per Plaud recording.
Temporary signed URLs and credentials are never persisted.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import json
import os
import re
import shlex
import socket
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

SCHEMA_VERSION = "gbrain-ops-plaud-recording/v1"
STATE_SCHEMA = "gbrain-ops-plaud-state/v1"
SUMMARY_SCHEMA = "gbrain-ops-plaud-sync-summary/v1"
COLLECTOR_VERSION = "1"
DEFAULT_PAGE_SIZE = 100
DEFAULT_TRANSCRIPT_PAGE_SIZE = 500
MAX_CATALOG_PAGES = 10_000
MAX_TRANSCRIPT_PAGES = 10_000
MAX_LINKED_NOTE_BYTES = 25 * 1024 * 1024
RECORDING_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
CAPABILITY_KEYS = {
    "access_token",
    "authorization",
    "client_secret",
    "cookie",
    "data_link",
    "download_url",
    "presigned_url",
    "refresh_token",
}

JsonObject = dict[str, Any]


class PlaudSourceError(RuntimeError):
    """Raised when Plaud violates the supported collector contract."""


class PlaudSource(Protocol):
    server_name: str
    server_version: str

    async def list_files(self, *, page: int, page_size: int) -> list[JsonObject]: ...

    async def get_file(self, recording_id: str) -> JsonObject: ...

    async def get_transcript(
        self,
        recording_id: str,
        *,
        cursor: str | None,
        limit: int,
    ) -> JsonObject: ...

    async def load_data_link(self, url: str) -> str: ...


@dataclass(frozen=True)
class CollectorPaths:
    root: Path
    current_dir: Path
    revisions_dir: Path
    brain_dir: Path
    state_path: Path
    summary_path: Path

    @classmethod
    def from_root(cls, root: Path) -> "CollectorPaths":
        resolved = root.expanduser().resolve()
        return cls(
            root=resolved,
            current_dir=resolved / "raw" / "current",
            revisions_dir=resolved / "raw" / "revisions",
            brain_dir=resolved / "brain",
            state_path=resolved / "state.json",
            summary_path=resolved / "sync-summary.json",
        )


@dataclass(frozen=True)
class Selection:
    recordings: list[JsonObject]
    full_audit: bool


@dataclass(frozen=True)
class HydratedRecording:
    recording_id: str
    snapshot: JsonObject
    source_hash: str
    page_relative: str
    preserved_transcript: bool
    preserved_notes: bool


def _mcp_environment() -> dict[str, str]:
    """Pass only Plaud/runtime essentials to the child and disable telemetry."""

    inherited = {"HOME", "LOGNAME", "PATH", "SHELL", "TERM", "USER"}
    plaud_configuration = {
        "PLAUD_API_BASE",
        "PLAUD_CLIENT_ID",
        "PLAUD_CLIENT_SECRET",
        "PLAUD_MCP_CLIENT_ID",
        "PLAUD_REFRESH_URL",
        "PLAUD_TOKEN_URL",
    }
    allowed = inherited | plaud_configuration
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment["DO_NOT_TRACK"] = "1"
    environment["PLAUD_TELEMETRY_DISABLED"] = "1"
    return environment


def _validate_data_link(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise PlaudSourceError("Plaud data link must be an unauthenticated HTTPS URL")
    hostname = parsed.hostname.casefold().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost") or hostname.endswith(".local"):
        raise PlaudSourceError("Plaud data link cannot target a local host")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise PlaudSourceError("Plaud data link cannot target a non-public address")

    # getaddrinfo also normalizes alternate numeric spellings such as 127.1,
    # 2130706433, and 0x7f000001 that ipaddress intentionally does not parse.
    try:
        resolved = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise PlaudSourceError("Plaud data link host could not be resolved") from exc
    addresses = {ipaddress.ip_address(item[4][0]) for item in resolved}
    if not addresses or any(not address.is_global for address in addresses):
        raise PlaudSourceError("Plaud data link cannot resolve to a non-public address")
    return url


def _validate_response_peer(response: httpx.Response) -> None:
    stream = response.extensions.get("network_stream")
    peer = stream.get_extra_info("server_addr") if stream is not None else None
    if not isinstance(peer, tuple) or not peer:
        raise PlaudSourceError("Plaud data link peer address is unavailable")
    try:
        address = ipaddress.ip_address(str(peer[0]))
    except ValueError as exc:
        raise PlaudSourceError("Plaud data link peer address is invalid") from exc
    if not address.is_global:
        raise PlaudSourceError("Plaud data link connected to a non-public address")


class PlaudMcpSource:
    """Small typed boundary around Plaud's official local MCP server."""

    def __init__(self, command: str, args: list[str], *, cwd: Path | None = None) -> None:
        self.command = command
        self.args = args
        self.cwd = cwd
        self.server_name = "unknown"
        self.server_version = "unknown"
        self._stdio_context: Any = None
        self._session_context: Any = None
        self._session: ClientSession | None = None
        self._stdio_entered = False
        self._session_entered = False

    async def __aenter__(self) -> "PlaudMcpSource":
        params = StdioServerParameters(
            command=self.command,
            args=self.args,
            cwd=self.cwd,
            env=_mcp_environment(),
        )
        self._stdio_context = stdio_client(params, errlog=sys.stderr)
        try:
            read_stream, write_stream = await self._stdio_context.__aenter__()
            self._stdio_entered = True
            self._session_context = ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(minutes=5),
            )
            self._session = await self._session_context.__aenter__()
            self._session_entered = True
            initialized = await self._session.initialize()
        except BaseException:
            await self.__aexit__(*sys.exc_info())
            raise
        server_info = getattr(initialized, "serverInfo", None) or getattr(
            initialized,
            "server_info",
            None,
        )
        if server_info is not None:
            self.server_name = str(getattr(server_info, "name", "unknown"))
            self.server_version = str(getattr(server_info, "version", "unknown"))
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            if self._session_context is not None and self._session_entered:
                await self._session_context.__aexit__(exc_type, exc, traceback)
        finally:
            self._session_entered = False
            if self._stdio_context is not None and self._stdio_entered:
                await self._stdio_context.__aexit__(exc_type, exc, traceback)
            self._stdio_entered = False

    async def _call_text(self, name: str, arguments: JsonObject | None = None) -> str:
        if self._session is None:
            raise PlaudSourceError("Plaud MCP session is not initialized")
        result = await self._session.call_tool(name, arguments=arguments or {})
        is_error = bool(getattr(result, "isError", False) or getattr(result, "is_error", False))
        chunks = [
            str(getattr(item, "text"))
            for item in getattr(result, "content", [])
            if getattr(item, "text", None) is not None
        ]
        text = "\n".join(chunks).strip()
        if is_error:
            raise PlaudSourceError(f"Plaud MCP tool {name} failed")
        if not text:
            raise PlaudSourceError(f"Plaud MCP tool {name} returned no text")
        return text

    @staticmethod
    def _json_prefix(text: str, *, tool: str) -> Any:
        stripped = text.lstrip()
        try:
            value, _ = json.JSONDecoder().raw_decode(stripped)
        except json.JSONDecodeError as exc:
            raise PlaudSourceError(f"Plaud MCP tool {tool} returned invalid JSON") from exc
        return value

    async def list_files(self, *, page: int, page_size: int) -> list[JsonObject]:
        text = await self._call_text("list_files", {"page": page, "page_size": page_size})
        value = self._json_prefix(text, tool="list_files")
        if isinstance(value, dict):
            value = value.get("data")
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise PlaudSourceError("Plaud list_files returned a non-list payload")
        return value

    async def get_file(self, recording_id: str) -> JsonObject:
        text = await self._call_text("get_file", {"file_id": recording_id})
        value = self._json_prefix(text, tool="get_file")
        if not isinstance(value, dict):
            raise PlaudSourceError("Plaud get_file returned a non-object payload")
        return value

    async def get_transcript(
        self,
        recording_id: str,
        *,
        cursor: str | None,
        limit: int,
    ) -> JsonObject:
        arguments: JsonObject = {"file_id": recording_id, "limit": limit}
        if cursor is not None:
            arguments["cursor"] = cursor
        text = await self._call_text("get_transcript", arguments)
        try:
            value = self._json_prefix(text, tool="get_transcript")
        except PlaudSourceError:
            lowered = text.casefold()
            if "not available" in lowered or "no transcript" in lowered or "no content" in lowered:
                return {"available": False, "segments": []}
            return {
                "available": True,
                "format": "plain_text",
                "total": 1,
                "segments": [{"speaker": "", "content": text}],
            }
        if isinstance(value, list) and not value:
            return {"available": False, "segments": []}
        if not isinstance(value, dict):
            raise PlaudSourceError("Plaud get_transcript returned a non-object payload")
        value.setdefault("available", True)
        return value

    async def load_data_link(self, url: str) -> str:
        current = _validate_data_link(url)
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
            async with client.stream("GET", current) as response:
                _validate_response_peer(response)
                if response.is_redirect:
                    raise PlaudSourceError("Plaud data link redirects are not allowed")
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise PlaudSourceError("Plaud data link request failed") from exc
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        declared_size = int(content_length)
                    except ValueError as exc:
                        raise PlaudSourceError("Plaud linked note has an invalid size header") from exc
                    if declared_size > MAX_LINKED_NOTE_BYTES:
                        raise PlaudSourceError("Plaud linked note exceeds the size limit")
                payload = bytearray()
                async for chunk in response.aiter_bytes():
                    payload.extend(chunk)
                    if len(payload) > MAX_LINKED_NOTE_BYTES:
                        raise PlaudSourceError("Plaud linked note exceeds the size limit")
                try:
                    return bytes(payload).decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise PlaudSourceError("Plaud linked note is not UTF-8 text") from exc

    async def ensure_authenticated(self, *, allow_login: bool) -> None:
        try:
            await self._call_text("get_current_user")
            return
        except PlaudSourceError:
            if not allow_login:
                raise
        await self._call_text("login")
        await self._call_text("get_current_user")


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


def _atomic_write(path: Path, content: str, *, skip_unchanged: bool = False) -> bool:
    _private_dir(path.parent)
    encoded = content.encode("utf-8")
    if skip_unchanged and path.exists() and path.read_bytes() == encoded:
        path.chmod(0o600)
        return False
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return True


def _atomic_json(path: Path, value: Any, *, skip_unchanged: bool = False) -> bool:
    return _atomic_write(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        skip_unchanged=skip_unchanged,
    )


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise PlaudSourceError(f"invalid private Plaud state file: {path.name}") from exc


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            seconds = float(value)
            if abs(seconds) >= 100_000_000_000:
                seconds /= 1_000
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if re.fullmatch(r"-?\d+(?:\.\d+)?", normalized):
        try:
            return _parse_datetime(float(normalized))
        except ValueError:
            return None
    normalized = normalized.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _safe_text(value: Any, *, one_line: bool = False) -> str:
    if value is None:
        return ""
    text = str(value).replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u2028", "\n").replace("\u2029", "\n").strip()
    if one_line:
        return " ".join(part.strip() for part in text.splitlines() if part.strip())
    return text


def _validate_recording_id(value: Any) -> str:
    recording_id = _safe_text(value, one_line=True)
    if not RECORDING_ID_RE.fullmatch(recording_id):
        raise PlaudSourceError("Plaud returned an invalid recording ID")
    return recording_id


def _redact_capabilities(value: Any) -> Any:
    if isinstance(value, list):
        return [_redact_capabilities(item) for item in value]
    if not isinstance(value, dict):
        return value
    redacted: JsonObject = {}
    for key, item in value.items():
        normalized_key = str(key).casefold()
        if normalized_key in CAPABILITY_KEYS or normalized_key.endswith("_token"):
            redacted[f"{key}_redacted"] = bool(item)
            continue
        redacted[str(key)] = _redact_capabilities(item)
    return redacted


def _semantic_hash(snapshot: JsonObject) -> str:
    encoded = json.dumps(
        snapshot,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _previous_current(paths: CollectorPaths, recording_id: str) -> JsonObject | None:
    value = _load_json(paths.current_dir / f"{recording_id}.json", None)
    if value is None:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("snapshot"), dict):
        raise PlaudSourceError(f"invalid current snapshot for recording {recording_id}")
    return value


def _valid_page_relative(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    if len(candidate.parts) != 3 or candidate.parts[0] != "plaud":
        return None
    if candidate.suffix != ".md":
        return None
    return candidate.as_posix()


def _page_relative(
    recording_id: str,
    snapshot: JsonObject,
    previous: JsonObject | None,
    state_entry: JsonObject,
) -> str:
    for candidate in (
        previous.get("page_relative") if previous else None,
        state_entry.get("page_relative"),
    ):
        valid = _valid_page_relative(candidate)
        if valid:
            return valid
    file_value = snapshot.get("file") if isinstance(snapshot.get("file"), dict) else {}
    catalog = snapshot.get("catalog") if isinstance(snapshot.get("catalog"), dict) else {}
    observed = _parse_datetime(file_value.get("start_at") or file_value.get("created_at"))
    observed = observed or _parse_datetime(catalog.get("start_at") or catalog.get("created_at"))
    day = (observed or datetime(1970, 1, 1, tzinfo=UTC)).date().isoformat()
    return f"plaud/{day[:4]}/{day}--{recording_id}.md"


async def list_all_recordings(
    source: PlaudSource,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> list[JsonObject]:
    recordings: dict[str, JsonObject] = {}
    for page in range(1, MAX_CATALOG_PAGES + 1):
        batch = await source.list_files(page=page, page_size=page_size)
        for item in batch:
            recording_id = _validate_recording_id(item.get("id"))
            if recording_id in recordings and recordings[recording_id] != item:
                raise PlaudSourceError(f"Plaud catalog returned conflicting rows for {recording_id}")
            recordings[recording_id] = item
        if len(batch) < page_size:
            break
    else:
        raise PlaudSourceError("Plaud catalog exceeded the pagination safety limit")
    return sorted(
        recordings.values(),
        key=lambda item: (
            _safe_text(item.get("created_at") or item.get("start_at"), one_line=True),
            _safe_text(item.get("id"), one_line=True),
        ),
    )


def select_recordings(
    catalog: list[JsonObject],
    state: JsonObject,
    *,
    mode: str,
    recent_days: int,
    full_audit_hours: int,
    now: datetime,
) -> Selection:
    if recent_days <= 0 or full_audit_hours <= 0:
        raise ValueError("recent_days and full_audit_hours must be positive")
    if mode == "backfill":
        return Selection(recordings=list(catalog), full_audit=True)
    if mode != "recent":
        raise ValueError(f"unsupported Plaud sync mode: {mode}")

    last_full = _parse_datetime(state.get("last_full_audit_at"))
    full_due = last_full is None or now.astimezone(UTC) - last_full >= timedelta(hours=full_audit_hours)
    if full_due:
        return Selection(recordings=list(catalog), full_audit=True)

    known = state.get("files") if isinstance(state.get("files"), dict) else {}
    cutoff = now.astimezone(UTC) - timedelta(days=recent_days)
    selected: list[JsonObject] = []
    for item in catalog:
        recording_id = _validate_recording_id(item.get("id"))
        observed = _parse_datetime(item.get("start_at") or item.get("created_at"))
        if recording_id not in known or observed is None or observed >= cutoff:
            selected.append(item)
    return Selection(recordings=selected, full_audit=False)


async def fetch_full_transcript(
    source: PlaudSource,
    recording_id: str,
    *,
    page_size: int = DEFAULT_TRANSCRIPT_PAGE_SIZE,
) -> JsonObject:
    segments: list[Any] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    block: Any = None
    expected_total: int | None = None
    for _ in range(MAX_TRANSCRIPT_PAGES):
        payload = await source.get_transcript(recording_id, cursor=cursor, limit=page_size)
        if payload.get("available") is False:
            return {"available": False, "block": None, "segments": [], "total": 0}
        page_segments = payload.get("segments")
        if not isinstance(page_segments, list):
            raise PlaudSourceError(f"Plaud transcript for {recording_id} has invalid segments")
        if block is None:
            block = _redact_capabilities(payload.get("block"))
        total = payload.get("total")
        if isinstance(total, int) and total >= 0:
            expected_total = total
        segments.extend(_redact_capabilities(page_segments))
        next_cursor = payload.get("next_cursor")
        if next_cursor is None or next_cursor == "":
            break
        cursor = str(next_cursor)
        if cursor in seen_cursors:
            raise PlaudSourceError(f"Plaud transcript cursor repeated for {recording_id}")
        seen_cursors.add(cursor)
    else:
        raise PlaudSourceError(f"Plaud transcript for {recording_id} exceeded the pagination safety limit")
    if expected_total is not None and len(segments) != expected_total:
        raise PlaudSourceError(
            f"Plaud transcript for {recording_id} returned {len(segments)} of {expected_total} segments"
        )
    return {
        "available": True,
        "block": block,
        "segments": segments,
        "total": len(segments),
    }


async def fetch_full_notes(source: PlaudSource, recording_id: str, notes: list[Any]) -> list[JsonObject]:
    hydrated: list[JsonObject] = []
    for index, value in enumerate(notes):
        if not isinstance(value, dict):
            raise PlaudSourceError(f"Plaud note {index} for {recording_id} is not an object")
        note = dict(value)
        content = note.get("data_content")
        has_content = content not in (None, "", [], {})
        link = note.get("data_link")
        if not has_content and isinstance(link, str) and link.strip():
            note["data_content"] = await source.load_data_link(link.strip())
        hydrated.append(note)
    return hydrated


async def hydrate_recording(
    source: PlaudSource,
    paths: CollectorPaths,
    catalog_item: JsonObject,
    state_entry: JsonObject,
) -> HydratedRecording:
    recording_id = _validate_recording_id(catalog_item.get("id"))
    details = await source.get_file(recording_id)
    returned_id = details.get("id")
    if returned_id is not None and _validate_recording_id(returned_id) != recording_id:
        raise PlaudSourceError(f"Plaud get_file identity mismatch for {recording_id}")
    transcript = await fetch_full_transcript(source, recording_id)
    previous = _previous_current(paths, recording_id)
    previous_snapshot = previous.get("snapshot") if previous else None
    previous_transcript = (
        previous_snapshot.get("transcript")
        if isinstance(previous_snapshot, dict) and isinstance(previous_snapshot.get("transcript"), dict)
        else None
    )
    preserved_transcript = False
    if not transcript.get("segments") and previous_transcript and previous_transcript.get("segments"):
        transcript = previous_transcript
        preserved_transcript = True

    notes = details.get("note_list")
    if notes is None:
        notes = []
    if not isinstance(notes, list):
        raise PlaudSourceError(f"Plaud notes for {recording_id} are not a list")
    previous_notes = (
        previous_snapshot.get("notes")
        if isinstance(previous_snapshot, dict) and isinstance(previous_snapshot.get("notes"), list)
        else []
    )
    preserved_notes = False
    if not notes and previous_notes:
        notes = previous_notes
        preserved_notes = True
    else:
        notes = await fetch_full_notes(source, recording_id, notes)
    details = dict(details)
    details["note_list"] = notes

    snapshot: JsonObject = {
        "schema": SCHEMA_VERSION,
        "recording_id": recording_id,
        "catalog": _redact_capabilities(catalog_item),
        "file": _redact_capabilities(details),
        "transcript": _redact_capabilities(transcript),
        "notes": _redact_capabilities(notes),
    }
    source_hash = _semantic_hash(snapshot)
    return HydratedRecording(
        recording_id=recording_id,
        snapshot=snapshot,
        source_hash=source_hash,
        page_relative=_page_relative(recording_id, snapshot, previous, state_entry),
        preserved_transcript=preserved_transcript,
        preserved_notes=preserved_notes,
    )


def _yaml_string(value: Any) -> str:
    return json.dumps(_safe_text(value, one_line=True), ensure_ascii=False)


def _format_milliseconds(value: Any) -> str:
    try:
        total = max(0, int(float(value)))
    except (TypeError, ValueError):
        return "--:--:--.---"
    hours, remainder = divmod(total, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def _note_content(note: JsonObject) -> str:
    value = note.get("data_content")
    if isinstance(value, str):
        return _safe_text(value)
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _speaker_labels(transcript: JsonObject) -> list[str]:
    labels = {
        _safe_text(segment.get("speaker"), one_line=True)
        for segment in transcript.get("segments", [])
        if isinstance(segment, dict) and _safe_text(segment.get("speaker"), one_line=True)
    }
    return sorted(labels, key=str.casefold)


def render_markdown(recording: HydratedRecording) -> str:
    snapshot = recording.snapshot
    file_value = snapshot.get("file") if isinstance(snapshot.get("file"), dict) else {}
    catalog = snapshot.get("catalog") if isinstance(snapshot.get("catalog"), dict) else {}
    transcript = snapshot.get("transcript") if isinstance(snapshot.get("transcript"), dict) else {}
    notes = snapshot.get("notes") if isinstance(snapshot.get("notes"), list) else []
    title = _safe_text(file_value.get("name") or catalog.get("name"), one_line=True)
    title = title or "Untitled Plaud recording"
    created_at = _safe_text(
        file_value.get("created_at") or catalog.get("created_at"),
        one_line=True,
    )
    start_at = _safe_text(file_value.get("start_at") or catalog.get("start_at"), one_line=True)
    duration_ms = file_value.get("duration")
    if duration_ms is None:
        duration_ms = catalog.get("duration")
    speakers = _speaker_labels(transcript)
    segments = [item for item in transcript.get("segments", []) if isinstance(item, dict)]

    lines = [
        "---",
        f"title: {_yaml_string(title)}",
        "type: meeting-note",
        "source: plaud",
        "source_id: plaud",
        f"plaud_recording_id: {_yaml_string(recording.recording_id)}",
        f"source_content_hash: {_yaml_string(recording.source_hash)}",
        f"source_created_at: {_yaml_string(created_at)}",
        f"meeting_start_at: {_yaml_string(start_at)}",
        f"duration_ms: {json.dumps(duration_ms)}",
        f"transcript_segments: {len(segments)}",
        f"generated_notes: {len(notes)}",
    ]
    if speakers:
        lines.append("speakers:")
        lines.extend(f"  - {_yaml_string(speaker)}" for speaker in speakers)
    else:
        lines.append("speakers: []")
    lines.extend(
        [
            "---",
            "",
            f"# {title}",
            "",
            f"- **Plaud recording ID:** `{recording.recording_id}`",
            f"- **Recorded:** {start_at or created_at or 'Unknown'}",
            f"- **Transcript segments:** {len(segments)}",
            f"- **Generated notes:** {len(notes)}",
            "",
            "## Plaud speaker labels",
            "",
        ]
    )
    if speakers:
        lines.extend(f"- {speaker}" for speaker in speakers)
    else:
        lines.append("_No speaker labels are currently available from Plaud._")

    lines.extend(["", "## Plaud-generated notes", ""])
    if not notes:
        lines.append("_No generated notes are currently available from Plaud._")
    for index, note in enumerate(notes, start=1):
        if not isinstance(note, dict):
            continue
        note_type = _safe_text(note.get("data_type"), one_line=True) or "note"
        lines.extend([f"### Note {index}: {note_type}", ""])
        content = _note_content(note)
        if not content:
            lines.append("_Plaud returned note metadata without inline content._")
        else:
            lines.extend(f"> {line}" if line else ">" for line in content.splitlines())
        lines.append("")

    lines.extend(["## Full transcript", ""])
    if not segments:
        lines.append("_No transcript is currently available from Plaud._")
    for segment in segments:
        start = _format_milliseconds(segment.get("start_time"))
        end = _format_milliseconds(segment.get("end_time"))
        speaker = _safe_text(segment.get("speaker"), one_line=True) or "Unknown speaker"
        content = _safe_text(segment.get("content"), one_line=True)
        lines.append(f"- `{start}–{end}` **{speaker}:** {content}")
    return "\n".join(lines).rstrip() + "\n"


def _write_revision(
    paths: CollectorPaths,
    recording: HydratedRecording,
    *,
    observed_at: str,
    collector: JsonObject,
) -> bool:
    path = paths.revisions_dir / recording.recording_id / f"{recording.source_hash}.json"
    value = {
        "schema": SCHEMA_VERSION,
        "source_content_hash": recording.source_hash,
        "first_observed_at": observed_at,
        "collector": collector,
        "snapshot": recording.snapshot,
    }
    content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            existing = _load_json(path, {})
            if existing.get("snapshot") != recording.snapshot:
                raise PlaudSourceError(f"immutable Plaud revision mismatch for {recording.recording_id}")
        path.chmod(0o600)
        return False
    return _atomic_write(path, content)


def ensure_runtime_dirs(paths: CollectorPaths) -> None:
    for path in (
        paths.root,
        paths.current_dir,
        paths.revisions_dir,
        paths.brain_dir,
        paths.state_path.parent,
        paths.summary_path.parent,
    ):
        _private_dir(path)


def harden_plaud_token_permissions() -> None:
    root = Path.home() / ".plaud"
    if not root.exists():
        _private_dir(root)
    else:
        root.chmod(0o700)
    for path in root.glob("tokens*.json"):
        if path.is_file():
            path.chmod(0o600)


async def collect_from_source(
    source: PlaudSource,
    *,
    root: Path,
    mode: str,
    recent_days: int = 30,
    full_audit_hours: int = 24,
    now: datetime | None = None,
) -> JsonObject:
    run_time = (now or datetime.now(UTC)).astimezone(UTC)
    observed_at = _iso_z(run_time)
    paths = CollectorPaths.from_root(root)
    ensure_runtime_dirs(paths)
    state = _load_json(paths.state_path, {})
    if not isinstance(state, dict):
        raise PlaudSourceError("Plaud state must be an object")
    files_state = state.get("files") if isinstance(state.get("files"), dict) else {}

    catalog = await list_all_recordings(source)
    selection = select_recordings(
        catalog,
        state,
        mode=mode,
        recent_days=recent_days,
        full_audit_hours=full_audit_hours,
        now=run_time,
    )

    hydrated: list[HydratedRecording] = []
    failures: list[tuple[str, Exception]] = []
    for item in selection.recordings:
        recording_id = _validate_recording_id(item.get("id"))
        state_entry = files_state.get(recording_id)
        if not isinstance(state_entry, dict):
            state_entry = {}
        try:
            hydrated.append(await hydrate_recording(source, paths, item, state_entry))
        except Exception as exc:  # noqa: BLE001 - aggregate without leaking meeting content
            failures.append((recording_id, exc))
    if failures:
        _atomic_json(
            paths.summary_path,
            {
                "schema": SUMMARY_SCHEMA,
                "status": "error",
                "mode": mode,
                "catalog_count": len(catalog),
                "selected_count": len(selection.recordings),
                "failure_count": len(failures),
                "completed_at": observed_at,
            },
        )
        first_id, first_error = failures[0]
        raise PlaudSourceError(
            f"failed to hydrate {len(failures)} Plaud recording(s); first={first_id}: {type(first_error).__name__}"
        ) from first_error

    collector = {
        "name": "gbrain-ops-plaud",
        "version": COLLECTOR_VERSION,
        "mcp_server": _safe_text(getattr(source, "server_name", "unknown"), one_line=True),
        "mcp_version": _safe_text(getattr(source, "server_version", "unknown"), one_line=True),
    }
    pages_written = 0
    revisions_written = 0
    unchanged_pages = 0
    preserved_transcripts = 0
    preserved_notes = 0
    next_files = dict(files_state)
    for recording in hydrated:
        revisions_written += int(
            _write_revision(
                paths,
                recording,
                observed_at=observed_at,
                collector=collector,
            )
        )
        current_value = {
            "schema": SCHEMA_VERSION,
            "source_content_hash": recording.source_hash,
            "fetched_at": observed_at,
            "collector": collector,
            "page_relative": recording.page_relative,
            "snapshot": recording.snapshot,
        }
        _atomic_json(paths.current_dir / f"{recording.recording_id}.json", current_value)
        page_path = paths.brain_dir / recording.page_relative
        if _atomic_write(page_path, render_markdown(recording), skip_unchanged=True):
            pages_written += 1
        else:
            unchanged_pages += 1
        transcript = recording.snapshot.get("transcript")
        notes = recording.snapshot.get("notes")
        next_files[recording.recording_id] = {
            "source_content_hash": recording.source_hash,
            "page_relative": recording.page_relative,
            "last_checked_at": observed_at,
            "transcript_segments": len(transcript.get("segments", [])) if isinstance(transcript, dict) else 0,
            "generated_notes": len(notes) if isinstance(notes, list) else 0,
        }
        preserved_transcripts += int(recording.preserved_transcript)
        preserved_notes += int(recording.preserved_notes)

    next_state: JsonObject = {
        "schema": STATE_SCHEMA,
        "files": next_files,
        "last_catalog_at": observed_at,
        "catalog_count": len(catalog),
        "last_success_at": observed_at,
    }
    if selection.full_audit:
        next_state["last_full_audit_at"] = observed_at
    elif state.get("last_full_audit_at"):
        next_state["last_full_audit_at"] = state["last_full_audit_at"]
    _atomic_json(paths.state_path, next_state)

    summary: JsonObject = {
        "schema": SUMMARY_SCHEMA,
        "status": "ok",
        "mode": mode,
        "full_audit": selection.full_audit,
        "catalog_count": len(catalog),
        "selected_count": len(selection.recordings),
        "pages_written": pages_written,
        "pages_unchanged": unchanged_pages,
        "revisions_written": revisions_written,
        "preserved_transcripts": preserved_transcripts,
        "preserved_notes": preserved_notes,
        "completed_at": observed_at,
    }
    _atomic_json(paths.summary_path, summary)
    return summary


def _mcp_command() -> tuple[str, list[str]]:
    parts = shlex.split(os.environ.get("PLAUD_MCP_COMMAND", "plaud-mcp"))
    if not parts:
        raise PlaudSourceError("PLAUD_MCP_COMMAND is empty")
    extra = shlex.split(os.environ.get("PLAUD_MCP_ARGS", ""))
    return parts[0], [*parts[1:], *extra]


async def run_pipeline_async(
    *,
    root: Path,
    mode: str,
    recent_days: int,
    full_audit_hours: int,
    now: datetime | None = None,
) -> JsonObject:
    harden_plaud_token_permissions()
    command, args = _mcp_command()
    async with PlaudMcpSource(command, args, cwd=root) as source:
        await source.ensure_authenticated(allow_login=False)
        return await collect_from_source(
            source,
            root=root,
            mode=mode,
            recent_days=recent_days,
            full_audit_hours=full_audit_hours,
            now=now,
        )


async def authenticate(root: Path) -> None:
    harden_plaud_token_permissions()
    command, args = _mcp_command()
    async with PlaudMcpSource(command, args, cwd=root) as source:
        await source.ensure_authenticated(allow_login=True)
    harden_plaud_token_permissions()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(os.environ.get("GBRAIN_OPS_PLAUD_ROOT", Path.home() / ".gbrain/integrations/plaud-to-brain")),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    auth_parser = subparsers.add_parser("auth", help="authenticate the official Plaud MCP package")
    auth_parser.set_defaults(mode=None)
    for command in ("backfill", "recent"):
        child = subparsers.add_parser(command)
        child.add_argument("--days", type=int, default=30)
        child.add_argument("--full-audit-hours", type=int, default=24)
        child.set_defaults(mode=command)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "auth":
            asyncio.run(authenticate(args.root))
            print("collector=plaud auth=ok")
            return 0
        summary = asyncio.run(
            run_pipeline_async(
                root=args.root,
                mode=args.mode,
                recent_days=args.days,
                full_audit_hours=args.full_audit_hours,
            )
        )
    except (OSError, PlaudSourceError, ValueError) as exc:
        print(f"collector=plaud status=error error={type(exc).__name__}", file=sys.stderr)
        return 1
    print(
        "collector=plaud status=collected "
        f"catalog={summary['catalog_count']} selected={summary['selected_count']} "
        f"written={summary['pages_written']} revisions={summary['revisions_written']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
