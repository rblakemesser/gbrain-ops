from __future__ import annotations

import asyncio
import json
import socket
import stat
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

ADAPTER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADAPTER))

import plaud_collector as plaud_module  # noqa: E402
from plaud_collector import (  # noqa: E402
    CollectorPaths,
    PlaudMcpSource,
    PlaudSourceError,
    _mcp_environment,
    _validate_data_link,
    collect_from_source,
    fetch_full_transcript,
    list_all_recordings,
    select_recordings,
)

RECORDING_A = "fixture-recording-00000001"
RECORDING_B = "fixture-recording-00000002"
RECORDING_C = "fixture-recording-00000003"
NOW = datetime(2026, 8, 3, 18, 0, tzinfo=UTC)


def catalog_item(recording_id: str, *, created_at: str, name: str) -> dict:
    return {
        "id": recording_id,
        "name": name,
        "created_at": created_at,
        "start_at": created_at,
        "duration": 120_000,
    }


def file_detail(recording_id: str, *, created_at: str, name: str, with_notes: bool = True) -> dict:
    notes = []
    if with_notes:
        notes = [
            {
                "data_type": "auto_sum_note",
                "data_content": "## Summary\n\nA source-faithful summary.",
                "data_link": "https://private.invalid/note?token=do-not-persist",
            },
            {
                "data_type": "action_items",
                "data_content": "- Follow up with the fixture participant.",
            },
        ]
    return {
        "id": recording_id,
        "name": name,
        "created_at": created_at,
        "start_at": created_at,
        "duration": 120_000,
        "serial_number": "fixture-device",
        "presigned_url": "https://private.invalid/audio?signature=do-not-persist",
        "source_list": [
            {
                "data_type": "transaction",
                "data_link": "https://private.invalid/transcript?token=do-not-persist",
            }
        ],
        "note_list": notes,
    }


def segments(*, first_speaker: str = "Speaker 1") -> list[dict]:
    return [
        {
            "start_time": 0,
            "end_time": 1_250,
            "speaker": first_speaker,
            "content": "Opening fixture statement.",
        },
        {
            "start_time": 1_250,
            "end_time": 2_500,
            "speaker": "Speaker 2",
            "content": "Second fixture statement.",
        },
        {
            "start_time": 2_500,
            "end_time": 3_000,
            "speaker": first_speaker,
            "content": "Closing fixture statement.",
        },
    ]


class FakePlaudSource:
    server_name = "fake-plaud-mcp"
    server_version = "fixture-1"

    def __init__(
        self, catalog: list[dict], details: dict[str, dict], transcripts: dict[str, list[dict] | None]
    ) -> None:
        self.catalog = catalog
        self.details = details
        self.transcripts = transcripts
        self.linked_content: dict[str, str] = {}
        self.list_calls: list[tuple[int, int]] = []
        self.transcript_calls: list[tuple[str, str | None, int]] = []
        self.loaded_links: list[str] = []

    async def list_files(self, *, page: int, page_size: int) -> list[dict]:
        self.list_calls.append((page, page_size))
        start = (page - 1) * page_size
        return deepcopy(self.catalog[start : start + page_size])

    async def get_file(self, recording_id: str) -> dict:
        return deepcopy(self.details[recording_id])

    async def get_transcript(
        self,
        recording_id: str,
        *,
        cursor: str | None,
        limit: int,
    ) -> dict:
        self.transcript_calls.append((recording_id, cursor, limit))
        values = self.transcripts[recording_id]
        if values is None:
            return {"available": False, "segments": []}
        start = int(cursor or 0)
        page = deepcopy(values[start : start + limit])
        next_cursor = start + len(page)
        return {
            "available": True,
            "block": {
                "data_type": "transaction",
                "data_link": "https://private.invalid/transcript?token=do-not-persist",
            },
            "total": len(values),
            "returned": len(page),
            "next_cursor": next_cursor if next_cursor < len(values) else None,
            "segments": page,
        }

    async def load_data_link(self, url: str) -> str:
        self.loaded_links.append(url)
        return self.linked_content[url]


def fixture_source() -> FakePlaudSource:
    recent = "2026-08-02T15:00:00Z"
    old = "2025-02-01T12:00:00Z"
    catalog = [
        catalog_item(RECORDING_B, created_at=old, name="Older fixture meeting"),
        catalog_item(RECORDING_A, created_at=recent, name="Recent fixture meeting"),
    ]
    details = {
        RECORDING_A: file_detail(
            RECORDING_A,
            created_at=recent,
            name="Recent fixture meeting",
        ),
        RECORDING_B: file_detail(
            RECORDING_B,
            created_at=old,
            name="Older fixture meeting",
            with_notes=False,
        ),
    }
    transcripts = {RECORDING_A: segments(), RECORDING_B: None}
    return FakePlaudSource(catalog, details, transcripts)


def run(source: FakePlaudSource, root: Path, *, mode: str, now: datetime) -> dict:
    return asyncio.run(
        collect_from_source(
            source,
            root=root,
            mode=mode,
            recent_days=30,
            full_audit_hours=24,
            now=now,
        )
    )


def test_backfill_preserves_full_source_revisions_and_renders_stable_pages(tmp_path: Path) -> None:
    source = fixture_source()

    summary = run(source, tmp_path, mode="backfill", now=NOW)

    assert summary == {
        "schema": "gbrain-ops-plaud-sync-summary/v1",
        "status": "ok",
        "mode": "backfill",
        "full_audit": True,
        "catalog_count": 2,
        "selected_count": 2,
        "pages_written": 2,
        "pages_unchanged": 0,
        "revisions_written": 2,
        "preserved_transcripts": 0,
        "preserved_notes": 0,
        "completed_at": "2026-08-03T18:00:00Z",
    }
    paths = CollectorPaths.from_root(tmp_path)
    recent_page = paths.brain_dir / f"plaud/2026/2026-08-02--{RECORDING_A}.md"
    old_page = paths.brain_dir / f"plaud/2025/2025-02-01--{RECORDING_B}.md"
    rendered = recent_page.read_text(encoding="utf-8")
    assert "source: plaud" in rendered
    assert "generated_notes: 2" in rendered
    assert "transcript_segments: 3" in rendered
    assert "Speaker 1" in rendered and "Speaker 2" in rendered
    assert "Opening fixture statement." in rendered
    assert "A source-faithful summary." in rendered
    assert "Follow up with the fixture participant." in rendered
    assert "No transcript is currently available" in old_page.read_text(encoding="utf-8")

    current_path = paths.current_dir / f"{RECORDING_A}.json"
    current_text = current_path.read_text(encoding="utf-8")
    assert "do-not-persist" not in current_text
    assert "presigned_url_redacted" in current_text
    assert "data_link_redacted" in current_text
    current = json.loads(current_text)
    source_hash = current["source_content_hash"]
    revision = paths.revisions_dir / RECORDING_A / f"{source_hash}.json"
    assert revision.is_file()
    assert stat.S_IMODE(current_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(revision.stat().st_mode) == 0o600
    assert stat.S_IMODE(recent_page.stat().st_mode) == 0o600


def test_no_change_is_idempotent_and_speaker_rename_updates_same_page(tmp_path: Path) -> None:
    source = fixture_source()
    first = run(source, tmp_path, mode="backfill", now=NOW)
    assert first["revisions_written"] == 2
    paths = CollectorPaths.from_root(tmp_path)
    state = json.loads(paths.state_path.read_text(encoding="utf-8"))
    page_relative = state["files"][RECORDING_A]["page_relative"]

    second = run(source, tmp_path, mode="recent", now=NOW + timedelta(hours=1))
    assert second["full_audit"] is False
    assert second["selected_count"] == 1
    assert second["pages_written"] == 0
    assert second["revisions_written"] == 0

    source.transcripts[RECORDING_A] = segments(first_speaker="Fixture Person")
    source.details[RECORDING_A]["name"] = "Renamed fixture meeting"
    third = run(source, tmp_path, mode="recent", now=NOW + timedelta(hours=2))
    assert third["pages_written"] == 1
    assert third["revisions_written"] == 1

    next_state = json.loads(paths.state_path.read_text(encoding="utf-8"))
    assert next_state["files"][RECORDING_A]["page_relative"] == page_relative
    page = paths.brain_dir / page_relative
    rendered = page.read_text(encoding="utf-8")
    assert "Renamed fixture meeting" in rendered
    assert "Fixture Person" in rendered
    assert len(list((paths.revisions_dir / RECORDING_A).glob("*.json"))) == 2


def test_recent_selection_includes_new_and_recent_then_daily_full_audit() -> None:
    source = fixture_source()
    state = {
        "last_full_audit_at": "2026-08-03T17:00:00Z",
        "files": {RECORDING_A: {}, RECORDING_B: {}},
    }
    selection = select_recordings(
        source.catalog,
        state,
        mode="recent",
        recent_days=30,
        full_audit_hours=24,
        now=NOW,
    )
    assert selection.full_audit is False
    assert [item["id"] for item in selection.recordings] == [RECORDING_A]

    new_old = catalog_item(
        RECORDING_C,
        created_at="2024-01-01T00:00:00Z",
        name="Newly discovered old recording",
    )
    selection = select_recordings(
        [*source.catalog, new_old],
        state,
        mode="recent",
        recent_days=30,
        full_audit_hours=24,
        now=NOW,
    )
    assert {item["id"] for item in selection.recordings} == {RECORDING_A, RECORDING_C}

    selection = select_recordings(
        source.catalog,
        state,
        mode="recent",
        recent_days=30,
        full_audit_hours=24,
        now=NOW + timedelta(hours=25),
    )
    assert selection.full_audit is True
    assert {item["id"] for item in selection.recordings} == {RECORDING_A, RECORDING_B}


def test_recent_selection_accepts_epoch_millisecond_timestamps() -> None:
    source = fixture_source()
    recent_epoch_ms = int((NOW - timedelta(days=1)).timestamp() * 1_000)
    source.catalog[0]["created_at"] = recent_epoch_ms
    source.catalog[0]["start_at"] = recent_epoch_ms
    state = {
        "last_full_audit_at": "2026-08-03T17:00:00Z",
        "files": {RECORDING_A: {}, RECORDING_B: {}},
    }

    selection = select_recordings(
        source.catalog,
        state,
        mode="recent",
        recent_days=30,
        full_audit_hours=24,
        now=NOW,
    )

    assert {item["id"] for item in selection.recordings} == {RECORDING_A, RECORDING_B}


def test_transcript_and_catalog_pagination_are_complete() -> None:
    source = fixture_source()
    source.transcripts[RECORDING_A] = [
        {
            "start_time": index * 1_000,
            "end_time": (index + 1) * 1_000,
            "speaker": "Fixture",
            "content": f"Segment {index}",
        }
        for index in range(5)
    ]

    catalog = asyncio.run(list_all_recordings(source, page_size=1))
    transcript = asyncio.run(fetch_full_transcript(source, RECORDING_A, page_size=2))

    assert len(catalog) == 2
    assert source.list_calls == [(1, 1), (2, 1), (3, 1)]
    assert transcript["total"] == 5
    assert [item["content"] for item in transcript["segments"]] == [
        "Segment 0",
        "Segment 1",
        "Segment 2",
        "Segment 3",
        "Segment 4",
    ]
    assert [call[1] for call in source.transcript_calls] == [None, "2", "4"]


def test_mcp_transport_accepts_official_list_wrapper_and_empty_transcript() -> None:
    source = PlaudMcpSource("unused", [])
    responses = {
        "list_files": json.dumps({"data": [{"id": RECORDING_A}], "total": 1}),
        "get_transcript": "[]",
    }

    async def call_text(name: str, arguments: dict | None = None) -> str:
        return responses[name]

    source._call_text = call_text  # type: ignore[method-assign]
    listed = asyncio.run(source.list_files(page=1, page_size=100))
    transcript = asyncio.run(source.get_transcript(RECORDING_A, cursor=None, limit=500))

    assert listed == [{"id": RECORDING_A}]
    assert transcript == {"available": False, "segments": []}


def test_link_only_notes_are_loaded_then_capability_url_is_redacted(tmp_path: Path) -> None:
    source = fixture_source()
    link = "https://private.invalid/generated-note?token=do-not-persist"
    source.details[RECORDING_A]["note_list"] = [{"data_type": "auto_sum_note", "data_content": "", "data_link": link}]
    source.linked_content[link] = "Linked generated note body."

    run(source, tmp_path, mode="backfill", now=NOW)

    paths = CollectorPaths.from_root(tmp_path)
    state = json.loads(paths.state_path.read_text(encoding="utf-8"))
    page = paths.brain_dir / state["files"][RECORDING_A]["page_relative"]
    current = (paths.current_dir / f"{RECORDING_A}.json").read_text(encoding="utf-8")
    assert source.loaded_links == [link]
    assert "Linked generated note body." in page.read_text(encoding="utf-8")
    assert "Linked generated note body." in current
    assert "do-not-persist" not in current


def test_mcp_child_environment_is_minimal_and_telemetry_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNRELATED_PRIVATE_SECRET", "must-not-propagate")
    monkeypatch.setenv("PLAUD_UNRELATED_PRIVATE_SECRET", "must-not-propagate")
    monkeypatch.setenv("PLAUD_API_BASE", "https://api.example.invalid")

    environment = _mcp_environment()

    assert environment["DO_NOT_TRACK"] == "1"
    assert environment["PLAUD_TELEMETRY_DISABLED"] == "1"
    assert environment["PLAUD_API_BASE"] == "https://api.example.invalid"
    assert "UNRELATED_PRIVATE_SECRET" not in environment
    assert "PLAUD_UNRELATED_PRIVATE_SECRET" not in environment


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/note",
        "https://localhost/note",
        "https://127.0.0.1/note",
        "https://127.1/note",
        "https://2130706433/note",
        "https://0x7f000001/note",
    ],
)
def test_data_link_validation_rejects_unsafe_targets(url: str) -> None:
    with pytest.raises(PlaudSourceError):
        _validate_data_link(url)


def test_data_link_validation_accepts_only_all_public_dns_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: public)
    url = "https://cdn.example.invalid/generated-note"
    assert _validate_data_link(url) == url

    mixed = public + [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: mixed)
    with pytest.raises(PlaudSourceError, match="non-public"):
        _validate_data_link(url)


class FakeNetworkStream:
    def __init__(self, address: str) -> None:
        self.address = address

    def get_extra_info(self, name: str) -> tuple[str, int] | None:
        return (self.address, 443) if name == "server_addr" else None


def test_link_fetch_checks_connected_peer_and_disables_ambient_http(monkeypatch: pytest.MonkeyPatch) -> None:
    public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: public)
    real_client = httpx.AsyncClient
    client_options: dict[str, object] = {}

    def client_factory(**kwargs: object) -> httpx.AsyncClient:
        client_options.update(kwargs)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b"Generated note body.",
                request=request,
                extensions={"network_stream": FakeNetworkStream("8.8.8.8")},
            )

        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(plaud_module.httpx, "AsyncClient", client_factory)
    source = PlaudMcpSource("unused", [])

    assert asyncio.run(source.load_data_link("https://cdn.example.invalid/note")) == "Generated note body."
    assert client_options["follow_redirects"] is False
    assert client_options["trust_env"] is False


@pytest.mark.parametrize("status,peer", [(302, "8.8.8.8"), (200, "127.0.0.1")])
def test_link_fetch_rejects_redirects_and_private_connected_peers(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    peer: str,
) -> None:
    public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: public)
    real_client = httpx.AsyncClient

    def client_factory(**kwargs: object) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status,
                headers={"location": "https://cdn.example.invalid/other"},
                request=request,
                extensions={"network_stream": FakeNetworkStream(peer)},
            )

        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(plaud_module.httpx, "AsyncClient", client_factory)
    source = PlaudMcpSource("unused", [])

    with pytest.raises(PlaudSourceError):
        asyncio.run(source.load_data_link("https://cdn.example.invalid/note"))


def test_transient_missing_derivatives_preserve_last_nonempty_source(tmp_path: Path) -> None:
    source = fixture_source()
    run(source, tmp_path, mode="backfill", now=NOW)

    source.transcripts[RECORDING_A] = None
    source.details[RECORDING_A]["note_list"] = []
    summary = run(source, tmp_path, mode="recent", now=NOW + timedelta(hours=1))

    assert summary["preserved_transcripts"] == 1
    assert summary["preserved_notes"] == 1
    assert summary["revisions_written"] == 0
    paths = CollectorPaths.from_root(tmp_path)
    state = json.loads(paths.state_path.read_text(encoding="utf-8"))
    page = paths.brain_dir / state["files"][RECORDING_A]["page_relative"]
    rendered = page.read_text(encoding="utf-8")
    assert "Opening fixture statement." in rendered
    assert "A source-faithful summary." in rendered


def test_first_missing_derivatives_are_retriable_and_converge(tmp_path: Path) -> None:
    source = fixture_source()
    run(source, tmp_path, mode="backfill", now=NOW)
    paths = CollectorPaths.from_root(tmp_path)
    state = json.loads(paths.state_path.read_text(encoding="utf-8"))
    page_relative = state["files"][RECORDING_B]["page_relative"]

    source.transcripts[RECORDING_B] = segments(first_speaker="Updated Person")
    source.details[RECORDING_B]["note_list"] = [{"data_type": "auto_sum_note", "data_content": "Generated later."}]
    summary = run(source, tmp_path, mode="recent", now=NOW + timedelta(hours=25))

    assert summary["full_audit"] is True
    page = paths.brain_dir / page_relative
    rendered = page.read_text(encoding="utf-8")
    assert "Updated Person" in rendered
    assert "Generated later." in rendered
    assert "No transcript is currently available" not in rendered


def test_invalid_recording_identity_fails_closed() -> None:
    source = fixture_source()
    source.catalog[0]["id"] = "../escape"

    with pytest.raises(PlaudSourceError, match="invalid recording ID"):
        asyncio.run(list_all_recordings(source))
