#!/usr/bin/env python3
import argparse
import json
import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterable

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

HERMES_HOME = Path(os.environ.get('HERMES_HOME', Path.home() / '.hermes')).expanduser()
TOKEN_PATH = Path(os.environ.get('GBRAIN_OPS_GMAIL_TOKEN', HERMES_HOME / 'google_token_gmail.json')).expanduser()
ROOT = Path(os.environ.get('GBRAIN_OPS_GMAIL_ROOT', Path.home() / '.local/share/gbrain-ops/gmail')).expanduser()
RAW_DIR = ROOT / 'data' / 'messages'
DIGEST_DIR = ROOT / 'data' / 'digests'
BRAIN_DIR = ROOT / 'brain' / 'email'
STATE_PATH = ROOT / 'data' / 'state.json'
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/calendar',
    # Match the broad Hermes Google token's Drive scope; requesting drive.readonly
    # against that refresh token returns invalid_scope even though full Drive is present.
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/contacts.readonly',
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/documents.readonly',
]
NOISE_PATTERNS = [
    'noreply', 'no-reply', 'notifications@', 'calendar-notification',
    'mailer-daemon', 'postmaster', 'donotreply', 'newsletter', 'unsubscribe',
]
SIGNATURE_PATTERNS = [
    'docusign', 'dropbox sign', 'hellosign', 'pandadoc',
    'please sign', 'signature needed', 'ready for your signature',
    'everyone has signed', 'you just signed',
]


@dataclass
class FetchResult:
    account_email: str
    records: list[dict]
    count: int


def ensure_dirs() -> None:
    for p in [RAW_DIR, DIGEST_DIR, BRAIN_DIR, STATE_PATH.parent]:
        p.mkdir(parents=True, exist_ok=True)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile('w', delete=False, dir=str(path.parent), encoding='utf-8') as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def atomic_write_json(path: Path, data: dict | list) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def save_state(state: dict) -> None:
    atomic_write_json(STATE_PATH, state)


def get_creds() -> Credentials:
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        atomic_write_text(TOKEN_PATH, creds.to_json())
    return creds


def gmail_service():
    return build('gmail', 'v1', credentials=get_creds(), cache_discovery=False)


def gmail_profile() -> dict:
    service = gmail_service()
    return service.users().getProfile(userId='me').execute()


def gmail_link(message_id: str, authuser: str) -> str:
    return f'https://mail.google.com/mail/u/?authuser={authuser}#inbox/{message_id}'


def header_map(payload: dict) -> dict[str, str]:
    headers = payload.get('headers') or []
    return {h.get('name', '').lower(): h.get('value', '') for h in headers}


def normalize_account_slug(email: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', email.lower()).strip('-')


def classify_noise(sender: str, subject: str) -> bool:
    hay = f'{sender} {subject}'.lower()
    return any(p in hay for p in NOISE_PATTERNS)


def classify_signature(sender: str, subject: str) -> bool:
    hay = f'{sender} {subject}'.lower()
    return any(p in hay for p in SIGNATURE_PATTERNS)


def extract_message(service, msg_id: str, account_email: str) -> dict:
    msg = service.users().messages().get(
        userId='me',
        id=msg_id,
        format='metadata',
        metadataHeaders=['From', 'To', 'Cc', 'Bcc', 'Subject', 'Date']
    ).execute()
    payload = msg.get('payload') or {}
    headers = header_map(payload)
    ts = datetime.fromtimestamp(int(msg.get('internalDate', '0')) / 1000, tz=timezone.utc)
    label_ids = msg.get('labelIds') or []
    sender = headers.get('from', '')
    subject = headers.get('subject', '(no subject)')
    return {
        'id': msg['id'],
        'threadId': msg.get('threadId', ''),
        'historyId': msg.get('historyId', ''),
        'internalDate': msg.get('internalDate', ''),
        'timestamp_utc': ts.isoformat(),
        'day': ts.date().isoformat(),
        'from': sender,
        'to': headers.get('to', ''),
        'cc': headers.get('cc', ''),
        'bcc': headers.get('bcc', ''),
        'subject': subject,
        'date_header': headers.get('date', ''),
        'snippet': msg.get('snippet', ''),
        'labelIds': label_ids,
        'is_sent': 'SENT' in label_ids,
        'is_draft': 'DRAFT' in label_ids,
        'is_important': 'IMPORTANT' in label_ids,
        'is_starred': 'STARRED' in label_ids,
        'is_unread': 'UNREAD' in label_ids,
        'is_noise': classify_noise(sender, subject),
        'is_signature': classify_signature(sender, subject),
        'gmail_link': gmail_link(msg['id'], account_email),
        'account_email': account_email,
    }


def list_message_ids_for_range(service, start_day: date, end_day: date) -> list[str]:
    query = f'in:anywhere after:{start_day.isoformat()} before:{end_day.isoformat()}'
    ids: list[str] = []
    page_token = None
    while True:
        resp = service.users().messages().list(
            userId='me',
            q=query,
            includeSpamTrash=True,
            maxResults=500,
            pageToken=page_token,
        ).execute()
        ids.extend(m['id'] for m in resp.get('messages', []))
        page_token = resp.get('nextPageToken')
        if not page_token:
            break
    return ids


def fetch_range(start_day: date, end_day: date, workers: int = 1) -> FetchResult:
    profile = gmail_profile()
    account_email = profile['emailAddress']
    service = gmail_service()
    msg_ids = list_message_ids_for_range(service, start_day, end_day)
    records: list[dict] = []
    print(f'Found {len(msg_ids)} messages for {account_email} in {start_day}..{end_day}')
    if not msg_ids:
        return FetchResult(account_email=account_email, records=[], count=0)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(extract_message, service, msg_id, account_email): msg_id for msg_id in msg_ids}
        done = 0
        for fut in as_completed(futures):
            records.append(fut.result())
            done += 1
            if done % 100 == 0 or done == len(msg_ids):
                print(f'  fetched {done}/{len(msg_ids)} metadata records')
    records.sort(key=lambda r: (r['timestamp_utc'], r['id']))
    return FetchResult(account_email=account_email, records=records, count=len(records))


def merge_raw_records(path: Path, new_records: list[dict]) -> list[dict]:
    existing = []
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except Exception:
            existing = []
    merged = {r['id']: r for r in existing}
    for r in new_records:
        merged[r['id']] = r
    out = list(merged.values())
    out.sort(key=lambda r: (r['timestamp_utc'], r['id']))
    atomic_write_json(path, out)
    return out


def render_day_page(day: str, account_email: str, records: list[dict]) -> str:
    title = f'Gmail — {account_email} — {day}'
    tags = ['email', 'gmail', normalize_account_slug(account_email)]
    lines = [
        '---',
        'type: source',
        f'title: "{title}"',
        f'tags: [{", ".join(tags)}]',
        '---',
        '',
        f'# {title}',
        '',
        f'Total messages: {len(records)}',
        '',
    ]

    sections = [
        ('Signatures pending / signature-related', [r for r in records if r['is_signature']]),
        ('Received mail', [r for r in records if not r['is_sent'] and not r['is_noise'] and not r['is_signature']]),
        ('Sent mail', [r for r in records if r['is_sent']]),
        ('Noise / automated', [r for r in records if r['is_noise'] and not r['is_sent'] and not r['is_signature']]),
    ]

    for heading, items in sections:
        if not items:
            continue
        lines.extend([f'## {heading}', ''])
        for r in items:
            ts = r['timestamp_utc'][11:16]
            flags = []
            if r['is_unread']:
                flags.append('unread')
            if r['is_starred']:
                flags.append('starred')
            if r['is_important']:
                flags.append('important')
            flag_txt = f" ({', '.join(flags)})" if flags else ''
            lines.append(f"- {ts} | From: {r['from'] or '(unknown)'}{flag_txt}")
            lines.append(f"  - Subject: {r['subject']}")
            if r['snippet']:
                lines.append(f"  - Snippet: {r['snippet']}")
            lines.append(f"  - [Open in Gmail]({r['gmail_link']})")
            lines.append(f"  - Labels: {', '.join(r['labelIds']) if r['labelIds'] else '(none)'}")
            lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def write_pages(account_email: str, records: list[dict]) -> list[Path]:
    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_day[r['day']].append(r)
    written = []
    acct = normalize_account_slug(account_email)
    for day, day_records in by_day.items():
        year = day[:4]
        path = BRAIN_DIR / acct / year / f'{day}.md'
        atomic_write_text(path, render_day_page(day, account_email, day_records))
        written.append(path)
    return written


def write_digest(account_email: str, records: list[dict], label: str) -> Path:
    path = DIGEST_DIR / f'{label}.md'
    lines = [
        f'# Email Digest — {account_email} — {label}',
        '',
        f'Total messages collected: {len(records)}',
        '',
    ]
    sig = [r for r in records if r['is_signature']]
    triage = [r for r in records if not r['is_sent'] and not r['is_noise'] and not r['is_signature']][:100]
    noise = [r for r in records if r['is_noise']][:50]
    sections = [('Signatures pending', sig), ('Messages to triage', triage), ('Noise', noise)]
    for heading, items in sections:
        lines.extend([f'## {heading}', ''])
        if not items:
            lines.extend(['(none)', ''])
            continue
        for r in items:
            lines.append(f"- {r['day']} {r['timestamp_utc'][11:16]} | {r['from'] or '(unknown)'} | {r['subject']}")
            lines.append(f"  - {r['snippet']}")
            lines.append(f"  - [Open in Gmail]({r['gmail_link']})")
        lines.append('')
    atomic_write_text(path, '\n'.join(lines).rstrip() + '\n')
    return path


def month_iter(start_month: str, end_month: str) -> Iterable[tuple[date, date, str]]:
    start = datetime.strptime(start_month + '-01', '%Y-%m-%d').date()
    end = datetime.strptime(end_month + '-01', '%Y-%m-%d').date()
    cur = start
    while cur <= end:
        if cur.month == 12:
            nxt = date(cur.year + 1, 1, 1)
        else:
            nxt = date(cur.year, cur.month + 1, 1)
        yield cur, nxt, cur.strftime('%Y-%m')
        cur = nxt


def cmd_recent(args) -> None:
    ensure_dirs()
    end_day = datetime.now(timezone.utc).date() + timedelta(days=1)
    start_day = end_day - timedelta(days=args.days)
    result = fetch_range(start_day, end_day, workers=args.workers)
    label = f'recent-{start_day.isoformat()}-to-{(end_day - timedelta(days=1)).isoformat()}'
    raw_path = RAW_DIR / f'{label}.json'
    merge_raw_records(raw_path, result.records)
    page_paths = write_pages(result.account_email, result.records)
    digest_path = write_digest(result.account_email, result.records, label)
    state = load_state()
    state['account_email'] = result.account_email
    state['recent'] = {
        'last_run_utc': datetime.now(timezone.utc).isoformat(),
        'days': args.days,
        'last_count': result.count,
        'digest_path': str(digest_path),
        'pages_written': len(page_paths),
    }
    save_state(state)
    print(json.dumps({
        'account_email': result.account_email,
        'messages': result.count,
        'pages_written': len(page_paths),
        'digest_path': str(digest_path),
        'raw_path': str(raw_path),
    }, indent=2))


def cmd_backfill(args) -> None:
    ensure_dirs()
    now = datetime.now(timezone.utc).date()
    end_month = now.replace(day=1).strftime('%Y-%m')
    state = load_state()
    if args.resume and state.get('backfill', {}).get('next_month'):
        start_month = state['backfill']['next_month']
    else:
        start_month = args.start_month
    profile = gmail_profile()
    account_email = profile['emailAddress']
    completed = 0
    for start_day, end_day, label in month_iter(start_month, end_month):
        print(f'=== Backfilling {label} ===')
        result = fetch_range(start_day, end_day, workers=args.workers)
        raw_path = RAW_DIR / f'{label}.json'
        all_records = merge_raw_records(raw_path, result.records)
        page_paths = write_pages(account_email, all_records)
        next_month = end_day.strftime('%Y-%m')
        state = load_state()
        state['account_email'] = account_email
        state['backfill'] = {
            'last_completed_month': label,
            'next_month': next_month,
            'last_run_utc': datetime.now(timezone.utc).isoformat(),
            'last_count': len(all_records),
            'pages_written': len(page_paths),
        }
        save_state(state)
        completed += 1
        if args.max_months and completed >= args.max_months:
            break
    print(json.dumps(load_state().get('backfill', {}), indent=2))


def cmd_profile(_args) -> None:
    ensure_dirs()
    print(json.dumps(gmail_profile(), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description='Gmail collector for GBrain email-to-brain')
    sub = parser.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('profile')
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser('recent')
    p.add_argument('--days', type=int, default=30)
    p.add_argument('--workers', type=int, default=1)
    p.set_defaults(func=cmd_recent)

    p = sub.add_parser('backfill')
    p.add_argument('--start-month', default='2004-01')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--max-months', type=int, default=0)
    p.add_argument('--workers', type=int, default=1)
    p.set_defaults(func=cmd_backfill)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
