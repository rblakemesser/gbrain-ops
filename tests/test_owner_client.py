from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
from datetime import date
from pathlib import Path

import httpx
import pytest
from mcp.types import CallToolResult, TextContent

from gbrain_ops.archive_reconcile import ArchiveReconciler, candidates
from gbrain_ops.owner_client import (
    OwnerClient,
    OwnerClientError,
    OwnerCredentials,
    PersistenceReceipt,
    PersistedIngest,
    _tool_payload,
    content_hash,
    write_receipt,
)


def credentials(secret: str = "client-secret-fixture") -> OwnerCredentials:
    return OwnerCredentials(
        base_url="http://127.0.0.1:3131",
        client_id="client-fixture",
        client_secret=secret,
        source_id="messages",
    )


def test_credentials_require_loopback_and_private_mode(tmp_path: Path) -> None:
    path = tmp_path / "owner.json"
    path.write_text(
        json.dumps(
            {
                "base_url": "http://127.0.0.1:3131",
                "client_id": "id",
                "client_secret": "secret",
                "source_id": "messages",
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o644)
    with pytest.raises(OwnerClientError, match="0600"):
        OwnerCredentials.from_file(path)
    path.chmod(0o600)
    assert OwnerCredentials.from_file(path).source_id == "messages"
    with pytest.raises(OwnerClientError, match="loopback"):
        credentials().__class__("https://example.test", "id", "secret", "messages").validate()


def test_persisted_ingest_uses_source_binding_and_does_not_leak_secret() -> None:
    secret = "never-print-this-secret"
    content = "# proof\n"

    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/token":
                assert secret.encode() in request.content
                return httpx.Response(200, json={"access_token": "access-fixture", "expires_in": 60})
            assert request.url.path == "/ingest"
            assert request.headers["prefer"] == "wait=persisted"
            assert request.headers["x-gbrain-source-id"] == "messages"
            assert request.headers["x-gbrain-slug"] == "messages/2026/2026-07-11"
            assert request.headers["authorization"] == "Bearer access-fixture"
            raw_hash = hashlib.sha256(content.encode()).hexdigest()
            return httpx.Response(
                200,
                json={
                    "status": "accepted",
                    "source_id": "messages",
                    "slug": "messages/2026/2026-07-11",
                    "source_content_hash": raw_hash,
                    "content_hash": "b" * 64,
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = OwnerClient(credentials(secret), http=http)
            outcome = await client.ingest_persisted(
                slug="messages/2026/2026-07-11",
                content=content,
                source_uri="file:///private/archive.md",
            )
            assert outcome.source_content_hash == content_hash(content)

            def rejected(request: httpx.Request) -> httpx.Response:
                return httpx.Response(401, text=f"bad {secret}")

        async with httpx.AsyncClient(transport=httpx.MockTransport(rejected)) as http:
            client = OwnerClient(credentials(secret), http=http)
            with pytest.raises(OwnerClientError) as raised:
                await client.access_token()
            assert secret not in str(raised.value)

    asyncio.run(scenario())


def test_persisted_ingest_rejects_incomplete_acknowledgment() -> None:
    with pytest.raises(OwnerClientError, match="invalid hash"):
        PersistedIngest.from_mapping(
            {
                "status": "accepted",
                "source_id": "messages",
                "slug": "messages/day",
                "source_content_hash": "short",
                "content_hash": "b" * 64,
            }
        )


def test_tool_payload_accepts_structured_content() -> None:
    result = CallToolResult(content=[], structuredContent={"brain_id": "brain-fixture"})
    assert _tool_payload(result) == {"brain_id": "brain-fixture"}


def test_tool_payload_accepts_json_array_text() -> None:
    result = CallToolResult(content=[TextContent(type="text", text='[{"slug":"messages/day"}]')])
    assert _tool_payload(result) == [{"slug": "messages/day"}]


class FakeOwnerClient(OwnerClient):
    def __init__(self, *, canonical_hash: str = "c" * 64, source_hash: str | None = None) -> None:
        super().__init__(credentials())
        self.canonical_hash = canonical_hash
        self.source_hash = source_hash
        self.ingest_calls = 0

    async def get_brain_identity(self) -> dict[str, object]:
        return {"brain_id": "brain-fixture", "page_count": 1, "chunk_count": 1}

    async def ingest_persisted(self, *, slug: str, content: str, source_uri: str) -> PersistedIngest:
        self.ingest_calls += 1
        return PersistedIngest(
            status="accepted",
            source_id="messages",
            slug=slug,
            source_content_hash=self.source_hash or content_hash(content),
            content_hash=self.canonical_hash,
        )

    async def get_page(self, slug: str) -> dict[str, object]:
        return {
            "source_id": "messages",
            "slug": slug,
            "content_hash": self.canonical_hash,
            "updated_at": "2026-07-11T00:00:00Z",
        }


def test_reconcile_writes_private_atomic_receipt_and_skips_verified_replay(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    page = root / "messages" / "2026" / "2026-07-11.md"
    page.parent.mkdir(parents=True)
    page.write_text("# recent\n", encoding="utf-8")
    receipt_root = tmp_path / "receipts"
    items = candidates(root=root, receipt_root=receipt_root)
    assert [item.slug for item in items] == ["messages/2026/2026-07-11"]

    client = FakeOwnerClient()
    reconciler = ArchiveReconciler(client)
    first = asyncio.run(reconciler.reconcile(items))
    assert first.submitted == 1
    receipt_path = items[0].receipt_path
    assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o600
    persisted = json.loads(receipt_path.read_text())
    assert persisted["source_content_hash"] == content_hash("# recent\n")
    assert "client_secret" not in persisted

    second = asyncio.run(reconciler.reconcile(items))
    assert second.unchanged == 1
    assert client.ingest_calls == 1


def test_hash_mismatch_does_not_replace_prior_receipt(tmp_path: Path) -> None:
    page = tmp_path / "page.md"
    page.write_text("# changed\n", encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    prior = PersistenceReceipt(
        schema="gbrain-ops-persistence-receipt/v1",
        source_id="messages",
        slug="messages/day",
        source_path=str(page),
        source_content_hash="a" * 64,
        canonical_content_hash="b" * 64,
        canonical_updated_at="before",
        brain_id="brain-fixture",
        outcome="accepted",
        verified_at="before",
    )
    write_receipt(receipt, prior)
    original = receipt.read_bytes()
    client = FakeOwnerClient(source_hash="d" * 64)
    with pytest.raises(OwnerClientError, match="source-content hash mismatch"):
        asyncio.run(client.reconcile_file(path=page, slug="messages/day", receipt_path=receipt))
    assert receipt.read_bytes() == original
    assert not [path for path in tmp_path.iterdir() if path.name.startswith(".receipt.json.")]


def test_candidate_recency_filter(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    root.mkdir()
    old = root / "old.md"
    new = root / "new.md"
    old.write_text("old", encoding="utf-8")
    new.write_text("new", encoding="utf-8")
    os.utime(old, (100, 100))
    os.utime(new, (900, 900))
    result = candidates(
        root=root,
        receipt_root=tmp_path / "receipts",
        modified_within_days=500 / 86_400,
        now=1_000,
    )
    assert [item.slug for item in result] == ["new"]


def test_candidate_date_filter_ignores_rewritten_history(tmp_path: Path) -> None:
    root = tmp_path / "archive" / "messages" / "2026"
    root.mkdir(parents=True)
    for day in ("2026-07-01", "2026-07-10", "2026-07-11"):
        (root / f"{day}.md").write_text(day, encoding="utf-8")
    (root / "INDEX.md").write_text("index", encoding="utf-8")
    result = candidates(
        root=tmp_path / "archive",
        receipt_root=tmp_path / "receipts",
        pattern="messages/**/*.md",
        dated_within_days=3,
        today=date(2026, 7, 11),
    )
    assert [item.slug for item in result] == [
        "messages/2026/2026-07-10",
        "messages/2026/2026-07-11",
    ]


def test_candidate_date_filter_accepts_prefixed_names_and_future_pages(tmp_path: Path) -> None:
    root = tmp_path / "archive" / "granola" / "2026"
    root.mkdir(parents=True)
    for name in (
        "2026-07-01--old.md",
        "2026-07-10--recent.md",
        "2026-07-31--future.md",
        "overview.md",
    ):
        (root / name).write_text(name, encoding="utf-8")
    result = candidates(
        root=tmp_path / "archive",
        receipt_root=tmp_path / "receipts",
        pattern="granola/**/*.md",
        dated_within_days=3,
        today=date(2026, 7, 11),
    )
    assert [item.slug for item in result] == [
        "granola/2026/2026-07-10--recent",
        "granola/2026/2026-07-31--future",
    ]
