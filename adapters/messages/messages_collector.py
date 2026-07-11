#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

try:
    from Foundation import NSData, NSUnarchiver  # type: ignore
except Exception:  # pragma: no cover - only available on macOS with PyObjC
    NSData = None
    NSUnarchiver = None

ROOT = Path(os.environ.get('GBRAIN_OPS_MESSAGES_ROOT', Path.home() / '.local/share/gbrain-ops/messages')).expanduser()
CHAT_DB = Path(os.environ.get('GBRAIN_OPS_MESSAGES_DB', Path.home() / 'Library/Messages/chat.db')).expanduser()
RAW_DIR = ROOT / 'data' / 'messages'
BRAIN_DIR = ROOT / 'brain' / 'daily' / 'messages'
STATE_PATH = ROOT / 'data' / 'state.json'
SUMMARY_PATH = ROOT / 'data' / 'sync-summary.json'

APPLE_EPOCH_OFFSET = 978_307_200
OTP_RE = re.compile(r'(?i)\b(code|verification|verify|2fa|two[- ]factor|otp|passcode|security code)\b|\b\d{4,8}\b')
URL_RE = re.compile(r'https?://\S+')
WS_RE = re.compile(r'\s+')


def ensure_dirs() -> None:
    for p in [RAW_DIR, BRAIN_DIR, STATE_PATH.parent]:
        p.mkdir(parents=True, exist_ok=True)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile('w', delete=False, dir=str(path.parent), encoding='utf-8') as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def atomic_write_json(path: Path, data) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True))


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {'last_rowid': 0}
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {'last_rowid': 0}


def save_state(state: dict) -> None:
    atomic_write_json(STATE_PATH, state)


def mac_time_to_iso(value) -> str:
    if value is None:
        return ''
    try:
        v = int(value)
    except Exception:
        return ''
    if v == 0:
        return ''
    # Messages has used seconds and nanoseconds since 2001-01-01.
    if abs(v) > 10_000_000_000:
        unix = (v / 1_000_000_000) + APPLE_EPOCH_OFFSET
    else:
        unix = v + APPLE_EPOCH_OFFSET
    try:
        return datetime.fromtimestamp(unix, tz=timezone.utc).isoformat()
    except Exception:
        return ''


def day_from_iso(iso: str) -> str:
    return iso[:10] if iso else 'unknown-date'


def month_from_iso(iso: str) -> str:
    return iso[:7] if iso else 'unknown-month'


def normalize_text(text) -> str:
    if text is None:
        return ''
    if isinstance(text, bytes):
        try:
            text = text.decode('utf-8', errors='ignore')
        except Exception:
            return ''
    text = str(text).replace('\uFFFC', ' ')
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', ' ', text)
    return WS_RE.sub(' ', text).strip()


def _fallback_decode_attributed_body(blob) -> str:
    """Best-effort typedstream fallback when Foundation/NSUnarchiver is unavailable."""
    if not blob:
        return ''
    if isinstance(blob, memoryview):
        blob = blob.tobytes()
    if not isinstance(blob, (bytes, bytearray)):
        return ''
    data = bytes(blob)
    # Messages.app stores attributed bodies as NSArchiver typedstreams. The primary
    # NSString payload immediately follows the first `NSString\x01` class marker and
    # usually terminates before the next archived-object marker (`\x86\x84`).
    marker = b'NSString\x01'
    start = data.find(marker)
    if start >= 0:
        seg = data[start + len(marker):]
        payload_marker = b'\x84\x01+'
        payload = seg.find(payload_marker)
        if payload >= 0:
            seg = seg[payload + len(payload_marker):]
            # NSArchiver typedstream length prefix: one ASCII-looking byte for
            # short strings, or 0x81/0x82/... followed by that many length bytes.
            if seg:
                if seg[0] & 0x80 and (seg[0] & 0x7F) <= 8:
                    seg = seg[1 + (seg[0] & 0x7F):]
                else:
                    seg = seg[1:]
        end = seg.find(b'\x86\x84')
        if end > 0:
            seg = seg[:end]
        decoded = seg.decode('utf-8', errors='ignore')
        decoded = re.sub(r'^[\x00-\x1f\x7f-\x9f]+', '', decoded)
        text = normalize_text(decoded)
        if text and text not in {'NSString', 'NSObject', 'NSAttributedString'}:
            return text
    decoded = data.decode('utf-8', errors='ignore')
    chunks = re.findall(r'[\w\U0001F300-\U0010FFFF][^\x00-\x08\x0b\x0c\x0e-\x1f]{1,}', decoded)
    noise = {'streamtyped', 'NSAttributedString', 'NSObject', 'NSString', 'NSDictionary', 'NSNumber', 'NSValue', 'NSMutableString'}
    candidates = [normalize_text(c) for c in chunks]
    candidates = [c for c in candidates if c and c not in noise and not c.startswith('__kIM')]
    return max(candidates, key=len, default='')


def decode_attributed_body(blob) -> str:
    """Decode Messages.app attributedBody blobs into the visible message string.

    Most modern Messages rows have NULL `message.text`; the user-visible body lives
    in `message.attributedBody` as an NSAttributedString archived with NSArchiver.
    When PyObjC/Foundation is available, so use the native
    unarchiver first and fall back to a typedstream string scrape for portability.
    """
    if not blob:
        return ''
    if NSData is not None and NSUnarchiver is not None:
        try:
            raw = blob.tobytes() if isinstance(blob, memoryview) else bytes(blob)
            data = NSData.dataWithBytes_length_(raw, len(raw))
            obj = NSUnarchiver.unarchiveObjectWithData_(data)
            if obj is not None and hasattr(obj, 'string'):
                text = normalize_text(obj.string())
                if text:
                    return text
        except Exception:
            pass
    return _fallback_decode_attributed_body(blob)


def classify_message(text: str, service: str, handle: str, is_from_me: bool, attachment_count: int) -> dict:
    t = text or ''
    labels = []
    promote = False
    redacted = False
    if OTP_RE.search(t) and len(t) < 240:
        labels.append('sensitive-otp-or-code')
        redacted = True
    if attachment_count:
        labels.append('has-attachment')
        promote = True
    if URL_RE.search(t):
        labels.append('has-link')
        promote = True
    if len(t) > 120:
        labels.append('substantive')
        promote = True
    if any(k in t.lower() for k in ['address', 'reservation', 'appointment', 'flight', 'hotel', 'order', 'receipt', 'meeting', 'dinner', 'lunch', 'birthday', 'wedding']):
        labels.append('planning-or-admin')
        promote = True
    if len(t) <= 12 and t.lower() in {'ok', 'okay', 'yes', 'no', 'lol', 'thanks', 'thank you', '👍', '❤️'}:
        labels.append('low-signal')
    if redacted:
        promote = False
    return {'labels': sorted(set(labels)), 'promote_candidate': promote, 'redacted_in_brain': redacted}


def open_db() -> sqlite3.Connection:
    if not CHAT_DB.exists():
        raise FileNotFoundError(f'Messages DB not found: {CHAT_DB}')
    # uri+mode=ro avoids journaling writes; immutable=1 is intentionally not used because Messages may have WAL data.
    uri = f'file:{CHAT_DB}?mode=ro'
    return sqlite3.connect(uri, uri=True)


def fetch_attachments(conn: sqlite3.Connection, message_ids: list[int]) -> dict[int, list[dict]]:
    if not message_ids:
        return {}
    out: dict[int, list[dict]] = defaultdict(list)
    chunk_size = 900
    for i in range(0, len(message_ids), chunk_size):
        chunk = message_ids[i:i + chunk_size]
        qs = ','.join('?' for _ in chunk)
        sql = f'''
        SELECT maj.message_id, a.ROWID, a.guid, a.filename, a.mime_type, a.total_bytes, a.transfer_name
        FROM message_attachment_join maj
        JOIN attachment a ON a.ROWID = maj.attachment_id
        WHERE maj.message_id IN ({qs})
        ORDER BY maj.message_id, a.ROWID
        '''
        for row in conn.execute(sql, chunk):
            message_id, rowid, guid, filename, mime_type, total_bytes, transfer_name = row
            out[int(message_id)].append({
                'attachment_id': rowid,
                'guid': guid or '',
                'filename': filename or '',
                'mime_type': mime_type or '',
                'total_bytes': total_bytes or 0,
                'transfer_name': transfer_name or '',
            })
    return out


def fetch_messages(after_rowid: int = 0, since_days: int | None = None, limit: int | None = None) -> list[dict]:
    conn = open_db()
    conn.row_factory = sqlite3.Row
    params: list = [after_rowid]
    where = ['m.ROWID > ?']
    if since_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
        apple_ns = int((cutoff.timestamp() - APPLE_EPOCH_OFFSET) * 1_000_000_000)
        where.append('m.date >= ?')
        params.append(apple_ns)
    limit_clause = ' LIMIT ?' if limit else ''
    if limit:
        params.append(limit)
    sql = f'''
    SELECT
      m.ROWID AS message_id,
      m.guid AS message_guid,
      m.text AS text,
      m.attributedBody AS attributed_body,
      m.service AS message_service,
      m.date AS date_raw,
      m.date_read AS date_read_raw,
      m.date_delivered AS date_delivered_raw,
      m.is_from_me AS is_from_me,
      m.cache_roomnames AS cache_roomnames,
      h.id AS handle_id,
      h.service AS handle_service,
      c.ROWID AS chat_id,
      c.guid AS chat_guid,
      c.display_name AS chat_display_name,
      c.chat_identifier AS chat_identifier
    FROM message m
    LEFT JOIN handle h ON h.ROWID = m.handle_id
    LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
    LEFT JOIN chat c ON c.ROWID = cmj.chat_id
    WHERE {' AND '.join(where)}
    ORDER BY m.ROWID ASC{limit_clause}
    '''
    rows = [dict(r) for r in conn.execute(sql, params)]
    attachments = fetch_attachments(conn, [int(r['message_id']) for r in rows])
    seen: set[tuple[int, str]] = set()
    out = []
    for r in rows:
        message_id = int(r['message_id'])
        chat_key = str(r.get('chat_id') or r.get('chat_guid') or '')
        key = (message_id, chat_key)
        if key in seen:
            continue
        seen.add(key)
        text = normalize_text(r.get('text'))
        text_source = 'text' if text else ''
        if not text:
            text = decode_attributed_body(r.get('attributed_body'))
            text_source = 'attributedBody' if text else ''
        sent_at = mac_time_to_iso(r.get('date_raw'))
        atts = attachments.get(message_id, [])
        service = r.get('message_service') or r.get('handle_service') or ''
        handle = r.get('handle_id') or ''
        is_from_me = bool(r.get('is_from_me'))
        cls = classify_message(text, service, handle, is_from_me, len(atts))
        out.append({
            'message_id': message_id,
            'message_guid': r.get('message_guid') or '',
            'sent_at': sent_at,
            'day': day_from_iso(sent_at),
            'month': month_from_iso(sent_at),
            'is_from_me': is_from_me,
            'direction': 'outbound' if is_from_me else 'inbound',
            'service': service,
            'handle': handle,
            'chat_id': r.get('chat_id') or '',
            'chat_guid': r.get('chat_guid') or '',
            'chat_display_name': r.get('chat_display_name') or '',
            'chat_identifier': r.get('chat_identifier') or '',
            'text': text,
            'text_source': text_source,
            'has_text': bool(text),
            'attachments': atts,
            'classification': cls,
        })
    conn.close()
    return out


def merge_raw(messages: list[dict]) -> dict:
    by_month: dict[str, list[dict]] = defaultdict(list)
    for m in messages:
        by_month[m['month']].append(m)
    summary = {'months': {}, 'messages_seen': len(messages), 'messages_written': 0}
    for month, items in by_month.items():
        path = RAW_DIR / f'{month}.jsonl'
        merged: dict[tuple[int, str], dict] = {}
        if path.exists():
            for line in path.read_text(encoding='utf-8').splitlines():
                if not line.strip():
                    continue
                try:
                    old = json.loads(line)
                    merged[(int(old['message_id']), str(old.get('chat_id') or ''))] = old
                except Exception:
                    continue
        for m in items:
            merged[(int(m['message_id']), str(m.get('chat_id') or ''))] = m
        ordered = sorted(merged.values(), key=lambda x: (x.get('sent_at') or '', int(x.get('message_id') or 0)))
        content = ''.join(json.dumps(x, ensure_ascii=False, sort_keys=True) + '\n' for x in ordered)
        atomic_write_text(path, content)
        summary['months'][month] = {'path': str(path), 'count': len(ordered)}
        summary['messages_written'] += len(items)
    return summary


def display_chat(m: dict) -> str:
    return m.get('chat_display_name') or m.get('chat_identifier') or m.get('handle') or '(unknown chat)'


def md_escape(s: str) -> str:
    return (s or '').replace('\n', ' ').strip()


def render_day(day: str, messages: list[dict]) -> str:
    dt_title = day
    try:
        dt = datetime.strptime(day, '%Y-%m-%d')
        dt_title = f'{day} ({dt.strftime("%A")})'
    except Exception:
        pass
    lines = [
        '---',
        'type: source',
        f'title: "Messages — {dt_title}"',
        'tags: [messages, imessage, sms, archive]',
        f'date: {day}',
        '---',
        '',
        f'# Messages — {dt_title}',
        '',
        f'Source: macOS Messages.app chat.db. Message count: {len(messages)}.',
        '',
    ]
    by_chat: dict[str, list[dict]] = defaultdict(list)
    for m in sorted(messages, key=lambda x: (x.get('sent_at') or '', int(x.get('message_id') or 0))):
        by_chat[display_chat(m)].append(m)
    for chat, items in sorted(by_chat.items(), key=lambda kv: (-len(kv[1]), kv[0].lower())):
        lines += [f'## {md_escape(chat)}', '']
        for m in items:
            time = (m.get('sent_at') or '')[11:16]
            who = 'Me' if m.get('is_from_me') else md_escape(m.get('handle') or 'Them')
            cls = m.get('classification') or {}
            labels = ', '.join(cls.get('labels') or [])
            if cls.get('redacted_in_brain'):
                text = '[redacted: likely verification/security code]'
            else:
                text = md_escape(m.get('text') or '')
            if not text and m.get('attachments'):
                text = '[attachment only]'
            if not text:
                text = '[empty/no text body]'
            suffix = f' _[{labels}]_' if labels else ''
            lines.append(f'- {time} — **{who}:** {text}{suffix}')
            for a in m.get('attachments') or []:
                meta = ', '.join(x for x in [a.get('mime_type') or '', str(a.get('total_bytes') or '') + ' bytes' if a.get('total_bytes') else '', a.get('transfer_name') or ''] if x)
                lines.append(f'  - Attachment: `{md_escape(a.get("filename") or "")}` {md_escape(meta)}')
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def load_all_raw() -> list[dict]:
    messages = []
    for path in sorted(RAW_DIR.glob('*.jsonl')):
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.strip():
                try:
                    messages.append(json.loads(line))
                except Exception:
                    pass
    return messages


def write_daily_pages(messages: list[dict] | None = None) -> dict:
    if messages is None:
        messages = load_all_raw()
    by_day: dict[str, list[dict]] = defaultdict(list)
    for m in messages:
        by_day[m.get('day') or day_from_iso(m.get('sent_at') or '')].append(m)
    written = []
    for day, items in by_day.items():
        year = day[:4] if len(day) >= 4 else 'unknown-year'
        path = BRAIN_DIR / year / f'{day}.md'
        atomic_write_text(path, render_day(day, items))
        written.append(str(path))
    index_lines = ['---', 'type: index', 'title: "Messages archive index"', 'tags: [messages, archive, index]', '---', '', '# Messages archive index', '']
    for day in sorted(by_day):
        year = day[:4] if len(day) >= 4 else 'unknown-year'
        index_lines.append(f'- [[{year}/{day}|{day}]] — {len(by_day[day])} messages')
    atomic_write_text(BRAIN_DIR / 'INDEX.md', '\n'.join(index_lines) + '\n')
    return {'days': len(by_day), 'pages_written': len(written), 'index': str(BRAIN_DIR / 'INDEX.md')}


def run_recent(days: int, limit: int | None = None) -> dict:
    ensure_dirs()
    state = load_state()
    # For recent sync, don't rely only on rowid; this regenerates recent source truth and is idempotent.
    messages = fetch_messages(after_rowid=0, since_days=days, limit=limit)
    raw_summary = merge_raw(messages)
    page_summary = write_daily_pages(load_all_raw())
    max_rowid = max([int(m['message_id']) for m in messages], default=int(state.get('last_rowid') or 0))
    state.update({'last_rowid': max(max_rowid, int(state.get('last_rowid') or 0)), 'last_sync_at': datetime.now(timezone.utc).isoformat(), 'last_mode': f'recent:{days}'})
    save_state(state)
    summary = {'mode': 'recent', 'days': days, 'max_rowid_seen': max_rowid, **raw_summary, **page_summary}
    atomic_write_json(SUMMARY_PATH, summary)
    return summary


def run_incremental(limit: int | None = None) -> dict:
    ensure_dirs()
    state = load_state()
    after = int(state.get('last_rowid') or 0)
    messages = fetch_messages(after_rowid=after, limit=limit)
    raw_summary = merge_raw(messages)
    page_summary = write_daily_pages(load_all_raw())
    max_rowid = max([int(m['message_id']) for m in messages], default=after)
    state.update({'last_rowid': max_rowid, 'last_sync_at': datetime.now(timezone.utc).isoformat(), 'last_mode': 'incremental'})
    save_state(state)
    summary = {'mode': 'incremental', 'after_rowid': after, 'max_rowid_seen': max_rowid, **raw_summary, **page_summary}
    atomic_write_json(SUMMARY_PATH, summary)
    return summary


def run_backfill(limit: int | None = None) -> dict:
    ensure_dirs()
    messages = fetch_messages(after_rowid=0, limit=limit)
    raw_summary = merge_raw(messages)
    page_summary = write_daily_pages(load_all_raw())
    max_rowid = max([int(m['message_id']) for m in messages], default=0)
    state = load_state()
    state.update({'last_rowid': max(max_rowid, int(state.get('last_rowid') or 0)), 'last_sync_at': datetime.now(timezone.utc).isoformat(), 'last_mode': 'backfill'})
    save_state(state)
    summary = {'mode': 'backfill', 'max_rowid_seen': max_rowid, **raw_summary, **page_summary}
    atomic_write_json(SUMMARY_PATH, summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description='Export macOS Messages.app data into GBrain-ready markdown.')
    sub = parser.add_subparsers(dest='cmd', required=True)
    p_recent = sub.add_parser('recent', help='Refresh a recent window idempotently')
    p_recent.add_argument('--days', type=int, default=7)
    p_recent.add_argument('--limit', type=int)
    p_inc = sub.add_parser('incremental', help='Import messages after the saved ROWID')
    p_inc.add_argument('--limit', type=int)
    p_back = sub.add_parser('backfill', help='Backfill all messages')
    p_back.add_argument('--limit', type=int)
    sub.add_parser('render', help='Regenerate markdown from raw JSONL')
    args = parser.parse_args()

    try:
        if args.cmd == 'recent':
            summary = run_recent(args.days, args.limit)
        elif args.cmd == 'incremental':
            summary = run_incremental(args.limit)
        elif args.cmd == 'backfill':
            summary = run_backfill(args.limit)
        elif args.cmd == 'render':
            ensure_dirs()
            summary = write_daily_pages(load_all_raw())
            atomic_write_json(SUMMARY_PATH, {'mode': 'render', **summary})
        else:
            parser.error('unknown command')
            return 2
    except sqlite3.OperationalError as e:
        if 'authorization denied' in str(e).lower() or 'unable to open database' in str(e).lower():
            print('ERROR: Cannot read macOS Messages database.', file=sys.stderr)
            print(f'Path: {CHAT_DB}', file=sys.stderr)
            print('Grant Full Disk Access to the app/process running Hermes/Terminal, then rerun.', file=sys.stderr)
            print('System Settings → Privacy & Security → Full Disk Access → enable Terminal/iTerm and the Hermes app/runner.', file=sys.stderr)
            return 13
        raise
    except PermissionError:
        print('ERROR: Permission denied reading macOS Messages database. Grant Full Disk Access and rerun.', file=sys.stderr)
        return 13

    print(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
