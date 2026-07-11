from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .contracts import validate_contract


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def build_source_inventory(source_id: str, root: Path, *, exclude: Iterable[str] = ()) -> dict[str, Any]:
    root = root.resolve(strict=True)
    excluded = set(exclude)
    items: list[dict[str, Any]] = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in excluded:
            continue
        items.append({"path": relative, "size": path.stat().st_size, "sha256": sha256_file(path)})
    canonical = json.dumps(items, sort_keys=True, separators=(",", ":")).encode()
    inventory = {
        "schema": "gbrain-ops/source-inventory/v1",
        "source_id": source_id,
        "root": ".",
        "items": items,
        "digest": hashlib.sha256(canonical).hexdigest(),
    }
    validate_contract("source-inventory", inventory)
    return inventory


def recovery_readiness(
    inventories: Iterable[dict[str, Any]], *, required_sources: Iterable[str], imported: int, replayed: int
) -> dict[str, Any]:
    by_source = {inventory["source_id"]: inventory for inventory in inventories}
    blockers: list[str] = []
    for source_id in required_sources:
        if source_id not in by_source:
            blockers.append(f"missing required source inventory: {source_id}")
    if imported != replayed:
        blockers.append(f"replay mismatch: imported={imported} replayed={replayed}")
    receipt = {
        "schema": "gbrain-ops/recovery-receipt/v1",
        "status": "blocked" if blockers else "ready",
        "source_digests": {key: value["digest"] for key, value in sorted(by_source.items())},
        "imported": imported,
        "replayed": replayed,
        "blockers": blockers,
    }
    validate_contract("recovery-receipt", receipt)
    return receipt
