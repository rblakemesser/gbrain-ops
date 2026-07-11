#!/usr/bin/env python3
"""Collect Granola notes via the public API and render deterministic GBrain markdown."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

ROOT = Path(os.environ.get("GBRAIN_OPS_GRANOLA_ROOT", Path.home() / ".local/share/gbrain-ops/granola")).expanduser()
RAW_DIR = ROOT / "data" / "notes"
BRAIN_DIR = ROOT / "brain" / "granola"
STATE_PATH = ROOT / "data" / "state.json"
API_BASE = "https://public-api.granola.ai/v1"

CORRECTIONS_PATH = Path(os.environ.get(
    "GBRAIN_OPS_GRANOLA_CORRECTIONS", ROOT / "config" / "rendering-corrections.json"
)).expanduser()

def apply_local_note_corrections(note: dict[str, Any]) -> dict[str, Any]:
    """Apply optional private rendering-only replacements without mutating raw JSON."""
    if not CORRECTIONS_PATH.exists():
        return note
    try:
        rules = json.loads(CORRECTIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return note
    note_rules = rules.get(str(note.get("id") or ""), []) if isinstance(rules, dict) else []
    corrected: Any = note
    for rule in note_rules if isinstance(note_rules, list) else []:
        if not isinstance(rule, dict):
            continue
        old, new = rule.get("from"), rule.get("to")
        if not isinstance(old, str) or not isinstance(new, str) or not old:
            continue
        def replace(value: Any) -> Any:
            if isinstance(value, str): return value.replace(old, new)
            if isinstance(value, list): return [replace(item) for item in value]
            if isinstance(value, dict): return {key: replace(item) for key, item in value.items()}
            return value
        corrected = replace(corrected)
    return corrected if isinstance(corrected, dict) else note


def ensure_dirs() -> None:
    for path in [RAW_DIR, BRAIN_DIR, STATE_PATH.parent, ROOT / "logs"]:
        path.mkdir(parents=True, exist_ok=True)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def slugify(text: str, fallback: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    text = re.sub(r"-+", "-", text)
    return (text[:80].strip("-") or fallback)


def yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    return json.dumps(value, ensure_ascii=False)


def person_label(person: Any) -> str:
    if not isinstance(person, dict):
        return ""
    name = person.get("name") or ""
    email = person.get("email") or ""
    if name and email:
        return f"{name} <{email}>"
    return name or email


class GranolaClient:
    def __init__(self, api_key: str, timeout: int = 60):
        self.api_key = api_key
        self.timeout = timeout

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        qs = urllib.parse.urlencode(params)
        url = f"{API_BASE}{path}" + (f"?{qs}" if qs else "")
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:1000]
            raise RuntimeError(f"Granola API HTTP {exc.code}: {body}") from exc


def list_notes(client: GranolaClient, updated_after: str | None, page_size: int) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params = {"page_size": page_size, "updated_after": updated_after, "cursor": cursor}
        data = client.get("/notes", params)
        batch = data.get("notes") if isinstance(data, dict) else None
        if not isinstance(batch, list):
            raise RuntimeError(f"Unexpected /notes response shape: {type(data).__name__}")
        notes.extend([n for n in batch if isinstance(n, dict)])
        cursor = data.get("cursor") if isinstance(data, dict) else None
        if not data.get("hasMore") or not cursor:
            break
    notes.sort(key=lambda n: (n.get("updated_at") or "", n.get("id") or ""))
    return notes


def fetch_note_detail(client: GranolaClient, note_id: str) -> dict[str, Any]:
    quoted = urllib.parse.quote(note_id, safe="")
    data = client.get(f"/notes/{quoted}", {"include": "transcript"})
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected /notes/{note_id} response shape: {type(data).__name__}")
    return data


def raw_path_for(note: dict[str, Any]) -> Path:
    dt = parse_dt(note.get("created_at")) or parse_dt(note.get("updated_at")) or datetime.now(timezone.utc)
    month = dt.strftime("%Y-%m")
    note_id = str(note.get("id") or "unknown")
    return RAW_DIR / month / f"{note_id}.json"


def merge_preserving_transcript(path: Path, detail: dict[str, Any]) -> dict[str, Any]:
    old = load_json(path, {})
    merged = dict(old) if isinstance(old, dict) else {}
    merged.update(detail)
    # Granola can omit transcript fields in some contexts; preserve the last non-empty copy.
    if not detail.get("transcript") and old.get("transcript"):
        merged["transcript"] = old["transcript"]
    merged["fetched_at"] = iso_z(datetime.now(timezone.utc))
    atomic_write_json(path, merged)
    return merged


def note_content_hash(note: dict[str, Any]) -> str:
    payload = {
        "title": note.get("title"),
        "updated_at": note.get("updated_at"),
        "summary_markdown": note.get("summary_markdown"),
        "transcript": note.get("transcript"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def page_path_for(note: dict[str, Any]) -> Path:
    created = parse_dt(note.get("created_at")) or parse_dt(note.get("updated_at")) or datetime.now(timezone.utc)
    day = created.strftime("%Y-%m-%d")
    title = str(note.get("title") or "granola-note")
    slug = slugify(title, str(note.get("id") or "note"))
    return BRAIN_DIR / created.strftime("%Y") / f"{day}--{slug}.md"


def render_note(note: dict[str, Any]) -> str:
    created = parse_dt(note.get("created_at"))
    updated = parse_dt(note.get("updated_at"))
    title = str(note.get("title") or "Untitled Granola note")
    note_id = str(note.get("id") or "")
    owner = person_label(note.get("owner"))
    attendees = [person_label(a) for a in (note.get("attendees") or []) if person_label(a)]
    calendar_event = note.get("calendar_event") if isinstance(note.get("calendar_event"), dict) else None
    folders = note.get("folder_membership") if isinstance(note.get("folder_membership"), list) else []
    content_hash = note_content_hash(note)
    transcript = note.get("transcript") if isinstance(note.get("transcript"), list) else []
    summary = str(note.get("summary_markdown") or note.get("summary_text") or "").strip()

    frontmatter = {
        "type": "meeting-note",
        "source": "granola",
        "granola_note_id": note_id,
        "created_at": iso_z(created) if created else note.get("created_at"),
        "updated_at": iso_z(updated) if updated else note.get("updated_at"),
        "web_url": note.get("web_url"),
        "owner": owner or None,
        "attendees": attendees,
        "calendar_event_id": calendar_event.get("id") if calendar_event else None,
        "content_hash": content_hash,
        "transcript_segments": len(transcript),
    }
    lines = ["---"]
    for key, value in frontmatter.items():
        lines.append(f"{key}: {yaml_scalar(value)}")
    lines.extend(["---", "", f"# {title}", ""])
    lines.append("## Metadata")
    lines.append(f"- Granola note ID: `{note_id}`")
    if created:
        lines.append(f"- Created: {iso_z(created)}")
    if updated:
        lines.append(f"- Updated: {iso_z(updated)}")
    if note.get("web_url"):
        lines.append(f"- URL: {note.get('web_url')}")
    if owner:
        lines.append(f"- Owner: {owner}")
    if attendees:
        lines.append("- Attendees: " + "; ".join(attendees))
    if calendar_event:
        lines.append("- Calendar event: " + json.dumps(calendar_event, ensure_ascii=False, sort_keys=True))
    if folders:
        lines.append("- Folders: " + json.dumps(folders, ensure_ascii=False, sort_keys=True))
    lines.append("")
    lines.append("## Summary")
    lines.append(summary or "_(No summary returned by Granola.)_")
    lines.append("")
    lines.append("## Transcript")
    if transcript:
        for segment in transcript:
            if not isinstance(segment, dict):
                continue
            speaker = segment.get("speaker") or "Unknown"
            start = segment.get("start_time")
            end = segment.get("end_time")
            stamp = f"{start}–{end}" if start is not None and end is not None else ""
            text = str(segment.get("text") or "").strip()
            if text:
                prefix = f"- **{speaker}**"
                if stamp:
                    prefix += f" ({stamp})"
                lines.append(f"{prefix}: {text}")
    else:
        lines.append("_(No transcript returned by Granola.)_")
    lines.append("")
    lines.append("## Raw provenance")
    lines.append(f"- Fetched at: {note.get('fetched_at') or iso_z(datetime.now(timezone.utc))}")
    lines.append(f"- Content hash: `{content_hash}`")
    lines.append("")
    return "\n".join(lines)


def collect(args: argparse.Namespace) -> int:
    ensure_dirs()
    key = os.environ.get("GRANOLA_API_KEY")
    if not key:
        print("GRANOLA_API_KEY is missing", file=sys.stderr)
        return 2
    client = GranolaClient(key, timeout=args.timeout)
    state = load_json(STATE_PATH, {})

    updated_after: str | None = None
    if args.updated_after:
        updated_after = args.updated_after
    elif args.mode == "recent":
        last = state.get("last_successful_updated_after") or state.get("last_seen_updated_at")
        if last and not args.ignore_state:
            # Overlap to catch late transcript edits and clock skew.
            dt = parse_dt(str(last))
            updated_after = iso_z(dt - timedelta(hours=args.overlap_hours)) if dt else str(last)
        else:
            updated_after = iso_z(datetime.now(timezone.utc) - timedelta(days=args.days))

    print(f"Granola API sync mode={args.mode} updated_after={updated_after or 'ALL'}")
    note_refs = list_notes(client, updated_after, args.page_size)
    print(f"Granola notes listed: {len(note_refs)}")

    rendered = 0
    raw_written = 0
    max_updated: str | None = state.get("last_seen_updated_at") if isinstance(state, dict) else None
    errors: list[str] = []

    for idx, ref in enumerate(note_refs, start=1):
        note_id = str(ref.get("id") or "")
        if not note_id:
            continue
        try:
            detail = fetch_note_detail(client, note_id)
            raw_path = raw_path_for(detail)
            merged = merge_preserving_transcript(raw_path, detail)
            raw_written += 1
            corrected = apply_local_note_corrections(merged)
            md = render_note(corrected)
            page_path = page_path_for(corrected)
            old = page_path.read_text() if page_path.exists() else None
            if old != md:
                atomic_write_text(page_path, md)
                rendered += 1
            upd = merged.get("updated_at")
            if upd and (not max_updated or str(upd) > str(max_updated)):
                max_updated = str(upd)
            print(f"[{idx}/{len(note_refs)}] {note_id} raw={raw_path.name} page={page_path.name}")
            if args.sleep:
                time.sleep(args.sleep)
        except Exception as exc:
            msg = f"{note_id}: {type(exc).__name__}: {exc}"
            print(f"ERROR {msg}", file=sys.stderr)
            errors.append(msg)

    now = iso_z(datetime.now(timezone.utc))
    new_state = dict(state) if isinstance(state, dict) else {}
    new_state.update({
        "last_run_at": now,
        "last_mode": args.mode,
        "last_updated_after": updated_after,
        "last_notes_listed": len(note_refs),
        "last_raw_written": raw_written,
        "last_pages_rendered": rendered,
        "last_errors": errors[-20:],
    })
    if max_updated:
        new_state["last_seen_updated_at"] = max_updated
        if not errors:
            new_state["last_successful_updated_after"] = max_updated
    if not errors:
        new_state["last_success_at"] = now
    atomic_write_json(STATE_PATH, new_state)
    print(json.dumps({
        "notes_listed": len(note_refs),
        "raw_written": raw_written,
        "pages_rendered": rendered,
        "errors": len(errors),
        "last_seen_updated_at": max_updated,
    }, indent=2))
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    for name in ["recent", "backfill"]:
        p = sub.add_parser(name)
        p.add_argument("--page-size", type=int, default=30)
        p.add_argument("--timeout", type=int, default=60)
        p.add_argument("--sleep", type=float, default=0.0)
        p.add_argument("--updated-after", default=None)
        p.add_argument("--ignore-state", action="store_true")
        p.add_argument("--overlap-hours", type=int, default=12)
        p.add_argument("--days", type=int, default=14)
    args = parser.parse_args()
    return collect(args)


if __name__ == "__main__":
    raise SystemExit(main())
