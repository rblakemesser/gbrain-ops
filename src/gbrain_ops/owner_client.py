from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent


class OwnerClientError(RuntimeError):
    """Secret-free failure crossing the GBrain owner boundary."""


class OwnerResourceNotFoundError(OwnerClientError):
    """A typed, non-operational absence reported by the owner."""


@dataclass(frozen=True)
class OwnerCredentials:
    base_url: str
    client_id: str
    client_secret: str
    source_id: str
    timeout_seconds: float = 30.0

    @property
    def token_url(self) -> str:
        return urljoin(self.base_url.rstrip("/") + "/", "token")

    @property
    def ingest_url(self) -> str:
        return urljoin(self.base_url.rstrip("/") + "/", "ingest")

    @property
    def mcp_url(self) -> str:
        return urljoin(self.base_url.rstrip("/") + "/", "mcp")

    def validate(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise OwnerClientError("owner base_url must be loopback HTTP")
        if not self.client_id or not self.client_secret or not self.source_id:
            raise OwnerClientError("owner credentials are incomplete")
        if self.timeout_seconds <= 0:
            raise OwnerClientError("owner timeout must be positive")

    @classmethod
    def from_file(cls, path: Path) -> "OwnerCredentials":
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode & 0o077:
                raise OwnerClientError("owner credential file must be mode 0600")
            raw = json.loads(path.read_text(encoding="utf-8"))
            credentials = cls(
                base_url=str(raw["base_url"]),
                client_id=str(raw["client_id"]),
                client_secret=str(raw["client_secret"]),
                source_id=str(raw["source_id"]),
                timeout_seconds=float(raw.get("timeout_seconds", 30.0)),
            )
        except OwnerClientError:
            raise
        except Exception as exc:
            raise OwnerClientError("owner credential file is invalid") from exc
        credentials.validate()
        return credentials


@dataclass(frozen=True)
class PersistedIngest:
    status: str
    source_id: str
    slug: str
    source_content_hash: str
    content_hash: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "PersistedIngest":
        result = cls(
            status=str(value.get("status", "")),
            source_id=str(value.get("source_id", "")),
            slug=str(value.get("slug", "")),
            source_content_hash=str(value.get("source_content_hash", "")),
            content_hash=str(value.get("content_hash", "")),
        )
        if result.status not in {"accepted", "duplicate"}:
            raise OwnerClientError("owner did not acknowledge persistence")
        if not result.source_id or not result.slug:
            raise OwnerClientError("owner persistence acknowledgment is incomplete")
        for digest in (result.source_content_hash, result.content_hash):
            if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest.lower()):
                raise OwnerClientError("owner persistence acknowledgment has an invalid hash")
        return result


@dataclass(frozen=True)
class PersistenceReceipt:
    schema: str
    source_id: str
    slug: str
    source_path: str
    source_content_hash: str
    canonical_content_hash: str
    canonical_updated_at: str
    brain_id: str
    outcome: str
    verified_at: str

    def public_summary(self) -> dict[str, str]:
        return {
            "schema": self.schema,
            "source_id": self.source_id,
            "slug": self.slug,
            "source_content_hash": self.source_content_hash,
            "canonical_content_hash": self.canonical_content_hash,
            "brain_id": self.brain_id,
            "outcome": self.outcome,
            "verified_at": self.verified_at,
        }


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _tool_payload(result: CallToolResult) -> dict[str, Any] | list[Any]:
    if result.isError:
        for block in result.content:
            if not isinstance(block, TextContent):
                continue
            try:
                error_value = json.loads(block.text)
            except json.JSONDecodeError:
                continue
            if isinstance(error_value, dict) and error_value.get("error") == "page_not_found":
                raise OwnerResourceNotFoundError("owner resource was not found")
        raise OwnerClientError("owner tool call failed")
    if result.structuredContent is not None:
        return dict(result.structuredContent)
    for block in result.content:
        if isinstance(block, TextContent):
            try:
                value = json.loads(block.text)
            except json.JSONDecodeError:
                continue
            if isinstance(value, (dict, list)):
                return value
    raise OwnerClientError("owner tool response was not structured JSON")


class OwnerClient:
    def __init__(self, credentials: OwnerCredentials, *, http: httpx.AsyncClient | None = None) -> None:
        credentials.validate()
        self.credentials = credentials
        self._http = http
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0

    async def _client(self) -> tuple[httpx.AsyncClient, bool]:
        if self._http is not None:
            return self._http, False
        return httpx.AsyncClient(timeout=self.credentials.timeout_seconds), True

    async def access_token(self) -> str:
        now = time.monotonic()
        if self._access_token and now < self._access_token_expires_at:
            return self._access_token
        client, owned = await self._client()
        try:
            response = await client.post(
                self.credentials.token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.credentials.client_id,
                    "client_secret": self.credentials.client_secret,
                    "scope": "read write",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if response.status_code != 200:
                raise OwnerClientError("owner token request was rejected")
            value = response.json()
            token = value.get("access_token")
            if not isinstance(token, str) or not token:
                raise OwnerClientError("owner token response was invalid")
            expires_in = max(30, int(value.get("expires_in", 300)))
            self._access_token = token
            self._access_token_expires_at = now + max(1, expires_in - 15)
            return token
        except OwnerClientError:
            raise
        except Exception as exc:
            raise OwnerClientError("owner token request failed") from exc
        finally:
            if owned:
                await client.aclose()

    async def ingest_persisted(self, *, slug: str, content: str, source_uri: str) -> PersistedIngest:
        token = await self.access_token()
        client, owned = await self._client()
        try:
            response = await client.post(
                self.credentials.ingest_url,
                content=content.encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "text/markdown; charset=utf-8",
                    "Prefer": "wait=persisted",
                    "X-Gbrain-Content-Type": "text/markdown",
                    "X-Gbrain-Source-Id": self.credentials.source_id,
                    "X-Gbrain-Source-Uri": source_uri,
                    "X-Gbrain-Slug": slug,
                },
            )
            if response.status_code != 200:
                raise OwnerClientError("owner did not persist the archive page")
            return PersistedIngest.from_mapping(response.json())
        except OwnerClientError:
            raise
        except Exception as exc:
            raise OwnerClientError("owner persisted-ingestion request failed") from exc
        finally:
            if owned:
                await client.aclose()

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any] | list[Any]:
        token = await self.access_token()
        client, owned = await self._client()
        try:
            client.headers["Authorization"] = f"Bearer {token}"
            async with streamable_http_client(self.credentials.mcp_url, http_client=client) as streams:
                read_stream, write_stream, _ = streams
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(name, dict(arguments))
                    return _tool_payload(result)
        except OwnerClientError:
            raise
        except Exception as exc:
            raise OwnerClientError(f"owner tool call failed: {name}") from exc
        finally:
            if owned:
                await client.aclose()

    async def get_page(self, slug: str) -> dict[str, Any]:
        value = await self.call_tool("get_page", {"slug": slug})
        if not isinstance(value, dict):
            raise OwnerClientError("owner get_page response was invalid")
        return value

    async def get_brain_identity(self) -> dict[str, Any]:
        value = await self.call_tool("get_brain_identity", {})
        if not isinstance(value, dict):
            raise OwnerClientError("owner identity response was invalid")
        return value

    async def search(self, query: str, *, limit: int = 20) -> dict[str, Any] | list[Any]:
        return await self.call_tool("search", {"query": query, "limit": limit})

    async def reconcile_file(self, *, path: Path, slug: str, receipt_path: Path) -> PersistenceReceipt:
        content = path.read_text(encoding="utf-8")
        local_hash = content_hash(content)
        acknowledged = await self.ingest_persisted(
            slug=slug,
            content=content,
            source_uri=path.resolve().as_uri(),
        )
        if acknowledged.source_id != self.credentials.source_id or acknowledged.slug != slug:
            raise OwnerClientError("owner persistence acknowledgment identity mismatch")
        if acknowledged.source_content_hash != local_hash:
            raise OwnerClientError("owner source-content hash mismatch")

        page, identity = await asyncio.gather(self.get_page(slug), self.get_brain_identity())
        if str(page.get("source_id", "")) != self.credentials.source_id:
            raise OwnerClientError("canonical page source mismatch")
        if str(page.get("slug", "")) != slug:
            raise OwnerClientError("canonical page slug mismatch")
        if str(page.get("content_hash", "")) != acknowledged.content_hash:
            raise OwnerClientError("canonical page hash mismatch")
        brain_id = str(identity.get("brain_id", ""))
        if not brain_id:
            raise OwnerClientError("owner brain identity is missing")

        receipt = PersistenceReceipt(
            schema="gbrain-ops-persistence-receipt/v1",
            source_id=self.credentials.source_id,
            slug=slug,
            source_path=str(path.resolve()),
            source_content_hash=local_hash,
            canonical_content_hash=acknowledged.content_hash,
            canonical_updated_at=str(page.get("updated_at", "")),
            brain_id=brain_id,
            outcome=acknowledged.status,
            verified_at=datetime.now(UTC).isoformat(),
        )
        write_receipt(receipt_path, receipt)
        return receipt


def write_receipt(path: Path, receipt: PersistenceReceipt) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(asdict(receipt), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
