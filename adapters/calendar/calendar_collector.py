#!/usr/bin/env python3
import argparse
import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

HERMES_HOME = Path(os.environ.get('HERMES_HOME', Path.home() / '.hermes')).expanduser()
TOKEN_PATH = Path(os.environ.get('GBRAIN_OPS_CALENDAR_TOKEN', HERMES_HOME / 'google_token_calendar.json')).expanduser()
ROOT = Path(os.environ.get('GBRAIN_OPS_CALENDAR_ROOT', Path.home() / '.local/share/gbrain-ops/calendar')).expanduser()
RAW_DIR = ROOT / 'data' / 'events'
BRAIN_DIR = ROOT / 'brain' / 'daily' / 'calendar'
STATE_PATH = ROOT / 'data' / 'state.json'
SCOPES = ['https://www.googleapis.com/auth/calendar']


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


def calendar_service():
    return build('calendar', 'v3', credentials=get_creds(), cache_discovery=False)


def calendar_profile() -> dict:
    svc = calendar_service()
    me = []
    page_token = None
    while True:
        res = svc.calendarList().list(pageToken=page_token).execute()
        me.extend(res.get('items', []))
        page_token = res.get('nextPageToken')
        if not page_token:
            break
    return {'calendars': me}


def selected_calendars(include_holidays: bool = False) -> list[dict]:
    items = calendar_profile()['calendars']
    out = []
    identity_calendar_ids = {
        value.strip()
        for value in os.environ.get('GBRAIN_OPS_IDENTITY_CALENDAR_IDS', '').split(',')
        if value.strip()
    }
    for item in items:
        if item.get('hidden'):
            continue
        cid = item.get('id', '')
        if not include_holidays and '#holiday@group.v.calendar.google.com' in cid:  # privacy-scan: allow
            continue
        # Google sometimes leaves shared identity calendars unselected in
        # calendarList even though they are explicitly shared/writeable and
        # should be archived. Keep normal selected/primary calendars, plus
        # explicitly configured identity calendars that may be shared but unselected.
        if not (item.get('selected') or item.get('primary') or cid in identity_calendar_ids):
            continue
        out.append({
            'id': cid,
            'summary': item.get('summary', cid),
            'primary': item.get('primary', False),
            'accessRole': item.get('accessRole'),
        })
    return out


def month_iter(start_month: str, end_month: str):
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


def parse_start_end(event: dict) -> tuple[str, str, bool]:
    start = event.get('start', {})
    end = event.get('end', {})
    if 'date' in start:
        return start['date'], end.get('date', start['date']), True
    return start.get('dateTime', ''), end.get('dateTime', ''), False


def attendee_names(event: dict) -> list[str]:
    names = []
    for a in event.get('attendees', []) or []:
        name = a.get('displayName') or a.get('email') or ''
        if name:
            names.append(name)
    return names


def normalize_event(event: dict, cal: dict) -> dict:
    start_raw, end_raw, is_all_day = parse_start_end(event)
    day = start_raw[:10] if start_raw else ''
    return {
        'calendar_id': cal['id'],
        'calendar_summary': cal['summary'],
        'calendar_primary': cal['primary'],
        'id': event.get('id', ''),
        'status': event.get('status', ''),
        'summary': event.get('summary', '(no title)'),
        'description': event.get('description', ''),
        'location': event.get('location', ''),
        'htmlLink': event.get('htmlLink', ''),
        'created': event.get('created', ''),
        'updated': event.get('updated', ''),
        'organizer_email': (event.get('organizer') or {}).get('email', ''),
        'organizer_name': (event.get('organizer') or {}).get('displayName', ''),
        'recurringEventId': event.get('recurringEventId', ''),
        'iCalUID': event.get('iCalUID', ''),
        'is_all_day': is_all_day,
        'start': start_raw,
        'end': end_raw,
        'day': day,
        'attendees': attendee_names(event),
        'attendee_count': len(event.get('attendees', []) or []),
        'visibility': event.get('visibility', ''),
        'transparency': event.get('transparency', ''),
    }


def fetch_month(start_day: date, end_day: date, include_holidays: bool = False) -> list[dict]:
    svc = calendar_service()
    events = []
    calendars = selected_calendars(include_holidays=include_holidays)
    print(f'Calendars in scope: {len(calendars)}')
    for cal in calendars:
        print(f"  fetching {cal['summary']} ({cal['id']})")
        page_token = None
        cal_count = 0
        while True:
            res = svc.events().list(
                calendarId=cal['id'],
                timeMin=datetime.combine(start_day, datetime.min.time(), tzinfo=timezone.utc).isoformat(),
                timeMax=datetime.combine(end_day, datetime.min.time(), tzinfo=timezone.utc).isoformat(),
                singleEvents=True,
                orderBy='startTime',
                showDeleted=False,
                maxResults=2500,
                pageToken=page_token,
            ).execute()
            items = res.get('items', [])
            for item in items:
                if item.get('status') == 'cancelled':
                    continue
                norm = normalize_event(item, cal)
                if norm['day']:
                    events.append(norm)
                    cal_count += 1
            page_token = res.get('nextPageToken')
            if not page_token:
                break
        print(f"    kept {cal_count} events")
    events.sort(key=lambda e: (e['day'], e['start'], e['summary'], e['id']))
    return events


def merge_raw_month(path: Path, new_events: list[dict]) -> list[dict]:
    existing = []
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except Exception:
            existing = []
    merged = {(e['calendar_id'], e['id']): e for e in existing}
    for e in new_events:
        key = (e['calendar_id'], e['id'])
        prior = merged.get(key)
        if prior is None or (str(e.get('updated') or ''), str(e.get('start') or '')) >= (str(prior.get('updated') or ''), str(prior.get('start') or '')):
            merged[key] = e
    out = list(merged.values())
    out.sort(key=lambda e: (e['day'], e['start'], e['summary'], e['id']))
    atomic_write_json(path, out)
    return out


def render_day(day: str, events: list[dict]) -> str:
    day_dt = datetime.strptime(day, '%Y-%m-%d').date()
    title = f'{day} ({day_dt.strftime("%A")})'
    all_day = [e for e in events if e['is_all_day']]
    timed = [e for e in events if not e['is_all_day']]
    timed.sort(key=lambda e: e['start'])
    lines = [
        '---',
        'type: source',
        f'title: "Calendar — {title}"',
        'tags: [calendar, google-calendar, archive]',
        '---',
        '',
        f'# {title}',
        '',
    ]
    for e in all_day + timed:
        cal = e['calendar_summary']
        attendees = ', '.join(e['attendees']) if e['attendees'] else ''
        loc = f" 📍 {e['location']}" if e['location'] else ''
        link = f" [Source: Google Calendar]({e['htmlLink']})" if e['htmlLink'] else ''
        if e['is_all_day']:
            prefix = '- **All day** '
        else:
            start = e['start'][11:16] if 'T' in e['start'] else e['start']
            end = e['end'][11:16] if 'T' in e['end'] else e['end']
            prefix = f'- {start}-{end} '
        line = f"{prefix}**{e['summary']}** ({cal})"
        if attendees:
            line += f" — with {attendees}"
        line += loc + link
        lines.append(line)
        if e['description']:
            desc = e['description'].replace('\n', ' ').strip()
            if desc:
                lines.append(f"  - {desc[:300]}")
    return '\n'.join(lines).rstrip() + '\n'


def write_daily_pages(events: list[dict]) -> int:
    by_day = defaultdict(list)
    for e in events:
        by_day[e['day']].append(e)
    count = 0
    for day, day_events in by_day.items():
        year = day[:4]
        path = BRAIN_DIR / year / f'{day}.md'
        atomic_write_text(path, render_day(day, day_events))
        count += 1
    return count


def rebuild_daily_pages_from_raw() -> int:
    """Rebuild derived pages from the latest revision of each event identity."""
    latest: dict[tuple[str, str], dict] = {}
    for raw_path in sorted(RAW_DIR.glob('*.json')):
        try:
            payload = json.loads(raw_path.read_text())
        except Exception as exc:
            raise RuntimeError(f'invalid calendar raw archive: {raw_path.name}') from exc
        if not isinstance(payload, list):
            raise RuntimeError(f'invalid calendar raw archive shape: {raw_path.name}')
        for event in payload:
            if not isinstance(event, dict) or not event.get('calendar_id') or not event.get('id'):
                raise RuntimeError(f'invalid calendar event in raw archive: {raw_path.name}')
            key = (str(event['calendar_id']), str(event['id']))
            prior = latest.get(key)
            if prior is None or (str(event.get('updated') or ''), str(event.get('start') or '')) >= (str(prior.get('updated') or ''), str(prior.get('start') or '')):
                latest[key] = event

    by_day = defaultdict(list)
    for event in latest.values():
        by_day[event['day']].append(event)
    expected: set[Path] = set()
    for day, day_events in by_day.items():
        path = BRAIN_DIR / day[:4] / f'{day}.md'
        atomic_write_text(path, render_day(day, day_events))
        expected.add(path)
    for path in sorted(BRAIN_DIR.rglob('*.md')):
        if path not in expected:
            path.unlink()
    for directory in sorted((p for p in BRAIN_DIR.rglob('*') if p.is_dir()), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass
    return len(expected)


def cmd_list_calendars(args):
    ensure_dirs()
    print(json.dumps(selected_calendars(include_holidays=args.include_holidays), indent=2))


def cmd_backfill(args):
    ensure_dirs()
    now = datetime.now(timezone.utc).date()
    end_month = now.replace(day=1).strftime('%Y-%m')
    state = load_state()
    start_month = state.get('backfill', {}).get('next_month') if args.resume and state.get('backfill', {}).get('next_month') else args.start_month
    processed = 0
    for start_day, end_day, label in month_iter(start_month, end_month):
        print(f'=== Backfilling {label} ===')
        events = fetch_month(start_day, end_day, include_holidays=args.include_holidays)
        raw_path = RAW_DIR / f'{label}.json'
        merged = merge_raw_month(raw_path, events)
        pages_written = rebuild_daily_pages_from_raw()
        state = load_state()
        state['backfill'] = {
            'last_completed_month': label,
            'next_month': end_day.strftime('%Y-%m'),
            'last_run_utc': datetime.now(timezone.utc).isoformat(),
            'last_count': len(merged),
            'pages_written': pages_written,
        }
        save_state(state)
        processed += 1
        if args.max_months and processed >= args.max_months:
            break
    print(json.dumps(load_state().get('backfill', {}), indent=2))


def cmd_recent(args):
    ensure_dirs()
    end_day = datetime.now(timezone.utc).date() + timedelta(days=1)
    start_day = end_day - timedelta(days=args.days)
    events = fetch_month(start_day, end_day, include_holidays=args.include_holidays)
    label = f'recent-{start_day.isoformat()}-to-{(end_day - timedelta(days=1)).isoformat()}'
    raw_path = RAW_DIR / f'{label}.json'
    merged = merge_raw_month(raw_path, events)
    pages_written = rebuild_daily_pages_from_raw()
    state = load_state()
    state['recent'] = {
        'last_run_utc': datetime.now(timezone.utc).isoformat(),
        'days': args.days,
        'last_count': len(merged),
        'pages_written': pages_written,
        'raw_path': str(raw_path),
    }
    save_state(state)
    print(json.dumps(state['recent'], indent=2))


def main():
    parser = argparse.ArgumentParser(description='Google Calendar collector for GBrain')
    sub = parser.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('list-calendars')
    p.add_argument('--include-holidays', action='store_true')
    p.set_defaults(func=cmd_list_calendars)

    p = sub.add_parser('recent')
    p.add_argument('--days', type=int, default=30)
    p.add_argument('--include-holidays', action='store_true')
    p.set_defaults(func=cmd_recent)

    p = sub.add_parser('backfill')
    p.add_argument('--start-month', default='2016-04')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--max-months', type=int, default=0)
    p.add_argument('--include-holidays', action='store_true')
    p.set_defaults(func=cmd_backfill)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
