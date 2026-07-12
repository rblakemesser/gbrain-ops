from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

from .owner_client import OwnerClient, OwnerClientError, OwnerCredentials, content_hash


@dataclass(frozen=True)
class ArchiveCandidate:
    path: Path
    slug: str
    receipt_path: Path


@dataclass(frozen=True)
class ReconcileSummary:
    source_id: str
    brain_id: str
    scanned: int
    submitted: int
    unchanged: int
    failed: int
    receipts: tuple[dict[str, str], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "brain_id": self.brain_id,
            "scanned": self.scanned,
            "submitted": self.submitted,
            "unchanged": self.unchanged,
            "failed": self.failed,
            "receipts": list(self.receipts),
        }


def candidates(
    *,
    root: Path,
    receipt_root: Path,
    pattern: str = "**/*.md",
    modified_within_days: float | None = None,
    dated_within_days: int | None = None,
    now: float | None = None,
    today: date | None = None,
) -> list[ArchiveCandidate]:
    root = root.resolve()
    cutoff = None
    if modified_within_days is not None:
        if modified_within_days <= 0:
            raise ValueError("modified_within_days must be positive")
        cutoff = (time.time() if now is None else now) - modified_within_days * 86_400
    date_cutoff = None
    if dated_within_days is not None:
        if dated_within_days < 1:
            raise ValueError("dated_within_days must be positive")
        date_cutoff = (today or date.today()) - timedelta(days=dated_within_days - 1)
    result: list[ArchiveCandidate] = []
    for path in sorted(root.glob(pattern)):
        if not path.is_file() or path.suffix.lower() != ".md":
            continue
        if cutoff is not None and path.stat().st_mtime < cutoff:
            continue
        if date_cutoff is not None:
            try:
                page_date = date.fromisoformat(path.stem[:10])
            except ValueError:
                continue
            if page_date < date_cutoff:
                continue
        relative = path.resolve().relative_to(root)
        slug = relative.with_suffix("").as_posix()
        result.append(
            ArchiveCandidate(
                path=path.resolve(),
                slug=slug,
                receipt_path=receipt_root / relative.with_suffix(".json"),
            )
        )
    return result


def read_receipt(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        return None
    return value if isinstance(value, dict) else None


class ArchiveReconciler:
    def __init__(self, client: OwnerClient) -> None:
        self.client = client

    async def reconcile(
        self,
        items: Iterable[ArchiveCandidate],
        *,
        force_verify: bool = False,
    ) -> ReconcileSummary:
        identity = await self.client.get_brain_identity()
        brain_id = str(identity.get("brain_id", ""))
        if not brain_id:
            raise OwnerClientError("owner brain identity is missing")

        scanned = submitted = unchanged = failed = 0
        public_receipts: list[dict[str, str]] = []
        for item in items:
            scanned += 1
            local_hash = content_hash(item.path.read_text(encoding="utf-8"))
            prior = read_receipt(item.receipt_path)
            if (
                not force_verify
                and prior is not None
                and prior.get("schema") == "gbrain-ops-persistence-receipt/v1"
                and prior.get("source_id") == self.client.credentials.source_id
                and prior.get("slug") == item.slug
                and prior.get("source_content_hash") == local_hash
                and prior.get("brain_id") == brain_id
            ):
                unchanged += 1
                public_receipts.append(
                    {
                        "source_id": self.client.credentials.source_id,
                        "slug": item.slug,
                        "outcome": "unchanged",
                    }
                )
                continue
            try:
                receipt = await self.client.reconcile_file(
                    path=item.path,
                    slug=item.slug,
                    receipt_path=item.receipt_path,
                )
            except Exception:
                failed += 1
                raise
            submitted += 1
            public_receipts.append(receipt.public_summary())

        return ReconcileSummary(
            source_id=self.client.credentials.source_id,
            brain_id=brain_id,
            scanned=scanned,
            submitted=submitted,
            unchanged=unchanged,
            failed=failed,
            receipts=tuple(public_receipts),
        )


async def reconcile_archive(
    *,
    credentials_path: Path,
    root: Path,
    receipt_root: Path,
    pattern: str = "**/*.md",
    modified_within_days: float | None = None,
    dated_within_days: int | None = None,
    force_verify: bool = False,
) -> ReconcileSummary:
    credentials = OwnerCredentials.from_file(credentials_path)
    client = OwnerClient(credentials)
    items = candidates(
        root=root,
        receipt_root=receipt_root,
        pattern=pattern,
        modified_within_days=modified_within_days,
        dated_within_days=dated_within_days,
    )
    return await ArchiveReconciler(client).reconcile(items, force_verify=force_verify)


def run_reconcile(**kwargs: Any) -> ReconcileSummary:
    return asyncio.run(reconcile_archive(**kwargs))
