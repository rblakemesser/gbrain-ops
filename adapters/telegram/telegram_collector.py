#!/usr/bin/env python3
"""Telegram → GBrain collector.

``fixture-render`` is deliberately offline and is the only command usable before
Telegram app credentials/session setup. The ``auth``, ``inventory``, ``sync``,
and ``render`` commands use a local Telethon user-account session when the
operator has created the dedicated ``runtime.env`` and completed the interactive
Telegram login. None of the commands use the Hermes Bot API or source
``~/.hermes/.env``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import defaultdict
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Iterable

from render_markdown import render_active_projection
from telegram_event_model import normalize_event

ROOT = Path(os.environ.get("GBRAIN_OPS_TELEGRAM_ROOT", Path.home() / ".local/share/gbrain-ops/telegram")).expanduser()
RAW_DIR = ROOT / "data" / "raw"
BRAIN_DIR = ROOT / "brain" / "telegram"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as temp:
        temp.write(content)
        temp_path = Path(temp.name)
    os.replace(temp_path, path)
    path.chmod(0o600)


def _peer_key(event: dict) -> str:
    peer = event["peer"]
    return f"{peer['kind']}-{peer['raw_id']}"


def _month(event: dict) -> str:
    return event["message"]["date"][:7]


def _read_jsonl(input_path: Path) -> list[dict]:
    events: list[dict] = []
    for line_number, line in enumerate(input_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at line {line_number}") from exc
        events.append(normalize_event(candidate))
    return events


def _raw_artifacts(events: Iterable[dict]) -> dict[str, str]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for event in events:
        grouped[(_peer_key(event), _month(event))].append(event)

    artifacts: dict[str, str] = {}
    for (peer_key, month), values in sorted(grouped.items()):
        values.sort(key=lambda item: (item["message"]["date"], item["message"]["id"], item["observed_at"]))
        relative_path = f"data/raw/{peer_key}/{month}.jsonl"
        artifacts[relative_path] = "".join(
            json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n" for event in values
        )
    return artifacts


def fixture_render(input_path: Path | str, root: Path | str) -> dict[str, str]:
    """Write deterministic synthetic fixture outputs and return path → content."""
    events = _read_jsonl(Path(input_path))
    artifacts = _raw_artifacts(events)
    artifacts.update({f"brain/telegram/{path}": body for path, body in render_active_projection(events).items()})
    root_path = Path(root)
    for relative_path, content in sorted(artifacts.items()):
        _atomic_write(root_path / relative_path, content)
    return {path: artifacts[path] for path in sorted(artifacts)}


def load_live_events(raw_dir: Path = RAW_DIR) -> list[dict]:
    events: list[dict] = []
    for path in sorted(raw_dir.glob("*/*.jsonl")):
        events.extend(_read_jsonl(path))
    return events


def render_live_archive(*, source_id: str, raw_dir: Path = RAW_DIR, brain_dir: Path = BRAIN_DIR) -> dict[str, int]:
    """Project persisted raw lifecycle events to deterministic searchable Markdown."""
    events = load_live_events(raw_dir)
    pages = render_active_projection(events, source_id=source_id)
    for relative_path, content in pages.items():
        _atomic_write(brain_dir / relative_path, content)
    return {"events": len(events), "pages": len(pages)}


async def _authorized_client():  # type: ignore[no-untyped-def]
    from telegram_runtime import build_client, require_owner_only_session

    require_owner_only_session()
    client = build_client()
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError("Telegram session is not authorized; run auth from an operator-controlled terminal")
    return client


async def _resolve_entity(client: Any, peer: dict[str, Any]) -> Any:
    from telegram_runtime import _telethon

    _, _, _, types = _telethon()
    peer_id = int(peer["raw_id"])
    kind = peer["kind"]
    if kind == "user":
        reference = types.PeerUser(peer_id)
    elif kind == "chat":
        reference = types.PeerChat(peer_id)
    elif kind in {"supergroup", "channel"}:
        reference = types.PeerChannel(peer_id)
    else:
        raise RuntimeError(f"unsupported inventory peer kind: {kind}")
    return await client.get_entity(reference)


async def _inventory_async(config_path: Path) -> dict[str, Any]:
    from telegram_runtime import inventory, load_config

    client = await _authorized_client()
    try:
        return await inventory(client, load_config(config_path))
    finally:
        await client.disconnect()


async def _collect_with_flood_wait(
    client: Any,
    entity: Any,
    peer: dict[str, Any],
    account_id: str,
    *,
    limit: int,
    max_id: int | None,
    config: Any,
) -> tuple[list[dict[str, Any]], int]:
    """Fetch one peer segment, honoring bounded Telegram FloodWait responses."""
    from telegram_runtime import _telethon, collect_messages

    _, errors, _, _ = _telethon()
    retries = 0
    while True:
        try:
            return await collect_messages(client, entity, peer, account_id, limit=limit, max_id=max_id), retries
        except errors.FloodWaitError as exc:
            wait_seconds = max(1, int(getattr(exc, "seconds", 0) or 0))
            if wait_seconds > config.max_flood_wait_seconds:
                raise RuntimeError("Telegram FloodWait exceeds configured per-peer wait ceiling") from exc
            if retries >= config.max_flood_wait_retries:
                raise RuntimeError("Telegram FloodWait retry budget exhausted") from exc
            retries += 1
            await asyncio.sleep(wait_seconds + 1)


async def _sync_async(config_path: Path, mode: str, batch_size: int | None) -> dict[str, Any]:
    from telegram_runtime import (
        DIALOGS_PATH,
        append_events,
        authorized_account_id,
        load_checkpoint,
        load_config,
        recent_delete_events,
        save_checkpoint,
        utc_now,
    )

    if not DIALOGS_PATH.exists():
        raise RuntimeError("dialog inventory is missing; run inventory first")
    dialog_data = json.loads(DIALOGS_PATH.read_text(encoding="utf-8"))
    config = load_config(config_path)
    limit = batch_size or (config.recent_limit_per_peer if mode == "recent" else config.backfill_batch_size)
    client = await _authorized_client()
    outcomes: list[dict[str, Any]] = []
    try:
        account_id = await authorized_account_id(client)
        for peer in dialog_data.get("dialogs", []):
            if not peer.get("included"):
                continue
            try:
                entity = await _resolve_entity(client, peer)
                checkpoint = load_checkpoint(peer)
                if mode == "backfill" and checkpoint.get("backfill_complete") is True:
                    outcomes.append({
                        "peer_id": peer["raw_id"],
                        "kind": peer["kind"],
                        "mode": mode,
                        "status": "complete",
                    })
                    continue
                max_id = None
                if mode == "backfill" and checkpoint.get("oldest_collected_id") is not None:
                    max_id = int(checkpoint["oldest_collected_id"])
                events, flood_wait_retries = await _collect_with_flood_wait(
                    client,
                    entity,
                    peer,
                    account_id,
                    limit=limit,
                    max_id=max_id,
                    config=config,
                )
                if mode == "recent":
                    events.extend(recent_delete_events(peer, events))
                written = append_events(events)
                # Checkpoint follows successful raw persistence; it never advances first.
                updated_checkpoint = save_checkpoint(
                    peer,
                    events,
                    mode=mode,
                    backfill_complete=(len(events) < limit) if mode == "backfill" else None,
                )
                outcomes.append({
                    "peer_id": peer["raw_id"],
                    "kind": peer["kind"],
                    "mode": mode,
                    "events": len(events),
                    "flood_wait_retries": flood_wait_retries,
                    "written_segments": len(written),
                    "checkpoint": updated_checkpoint,
                    "status": "ok",
                })
            except Exception as exc:  # Keep other peers progressing; no message content is logged.
                outcomes.append({
                    "peer_id": peer.get("raw_id", "unknown"),
                    "kind": peer.get("kind", "unknown"),
                    "mode": mode,
                    "status": "error",
                    "error_class": type(exc).__name__,
                })
        return {
            "mode": mode,
            "generated_at": utc_now(),
            "peers_attempted": len(outcomes),
            "peers_ok": sum(1 for outcome in outcomes if outcome["status"] in {"ok", "complete"}),
            "peers_error": sum(1 for outcome in outcomes if outcome["status"] == "error"),
            "backfill_complete": sum(
                1
                for outcome in outcomes
                if outcome["status"] == "complete" or outcome.get("checkpoint", {}).get("backfill_complete") is True
            ) if mode == "backfill" else None,
            "backfill_remaining": sum(
                1
                for outcome in outcomes
                if outcome["status"] == "error"
                or (
                    outcome["status"] == "ok"
                    and outcome.get("checkpoint", {}).get("backfill_complete") is not True
                )
            ) if mode == "backfill" else None,
            "outcomes": outcomes,
        }
    finally:
        await client.disconnect()


def _print_safe_summary(summary: dict[str, Any]) -> None:
    """Print only counts/IDs/status classes, never credential/session/message content."""
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Telegram user-account history into local GBrain artifacts")
    subcommands = parser.add_subparsers(dest="command", required=True)

    fixture = subcommands.add_parser("fixture-render", help="offline: render validated synthetic JSONL into a scratch root")
    fixture.add_argument("--input", required=True, type=Path)
    fixture.add_argument("--root", required=True, type=Path)

    subcommands.add_parser("auth", help="interactive: create/reuse the owner-only MTProto session")

    inventory = subcommands.add_parser("inventory", help="enumerate every accessible dialog/topic without history collection")
    inventory.add_argument("--config", type=Path, default=ROOT / "config.json")

    sync = subcommands.add_parser("sync", help="collect one bounded account-wide recent/backfill segment")
    sync.add_argument("--config", type=Path, default=ROOT / "config.json")
    sync.add_argument("--mode", choices=("recent", "backfill"), required=True)
    sync.add_argument("--batch-size", type=int)

    render = subcommands.add_parser("render", help="project raw lifecycle JSONL to deterministic Markdown")
    render.add_argument("--source-id", default="telegram")
    render.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    render.add_argument("--brain-dir", type=Path, default=BRAIN_DIR)

    args = parser.parse_args()
    if args.command == "fixture-render":
        fixture_render(args.input, args.root)
        print("fixture_render_complete=true")
        return 0
    if args.command == "auth":
        from telegram_runtime import authenticate_interactively, run

        run(authenticate_interactively())
        print("telegram_auth_complete=true")
        return 0
    if args.command == "inventory":
        summary = asyncio.run(_inventory_async(args.config))
        _print_safe_summary(summary["summary"])
        return 0
    if args.command == "sync":
        summary = asyncio.run(_sync_async(args.config, args.mode, args.batch_size))
        keys = ["mode", "peers_attempted", "peers_ok", "peers_error"]
        if args.mode == "backfill":
            keys.extend(["backfill_complete", "backfill_remaining"])
        _print_safe_summary({key: summary[key] for key in keys})
        return 0
    if args.command == "render":
        _print_safe_summary(render_live_archive(source_id=args.source_id, raw_dir=args.raw_dir, brain_dir=args.brain_dir))
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
