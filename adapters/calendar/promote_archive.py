#!/usr/bin/env python3
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from tempfile import NamedTemporaryFile

ROOT = Path(os.environ.get('GBRAIN_OPS_CALENDAR_ROOT', Path.home() / '.local/share/gbrain-ops/calendar')).expanduser()
EVENTS_DIR = ROOT / 'data' / 'events'
PROMOTED_DIR = ROOT / 'brain' / 'promoted'
SUMMARY_PATH = ROOT / 'data' / 'promotion-summary.json'
SELF_EMAIL = os.environ.get('GBRAIN_OPS_SELF_EMAIL', '')
DOMAINS = ['travel', 'finance', 'health', 'housing', 'recruiting']


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile('w', delete=False, dir=str(path.parent), encoding='utf-8') as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def atomic_write_json(path: Path, data) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-') or 'unknown'


def load_events() -> list[dict]:
    rows=[]
    for p in sorted(EVENTS_DIR.glob('????-??.json')):
        rows.extend(json.loads(p.read_text()))
    rows.sort(key=lambda e: (e.get('day',''), e.get('start',''), e.get('summary','')))
    return rows


def attendee_emails(event: dict) -> list[str]:
    out=[]
    for a in event.get('attendees', []) or []:
        m = re.search(r'([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})', a, re.I)
        if m:
            out.append(m.group(1).lower())
    return out


def attendee_names(event: dict) -> list[str]:
    out=[]
    for a in event.get('attendees', []) or []:
        if '@' in a:
            out.append(a.split('@')[0])
        else:
            out.append(a)
    return out


def human_relationship_signal(event: dict) -> bool:
    return bool(event.get('human_attendees_likely')) and not event.get('low_signal_block') and (event.get('planning_significant') or event.get('recurring_event'))


def people_pages(events: list[dict]):
    buckets=defaultdict(list)
    for e in events:
        if not human_relationship_signal(e):
            continue
        emails = [a for a in attendee_emails(e) if a != SELF_EMAIL]
        if not emails:
            continue
        for email in emails:
            buckets[email].append(e)
    created=0
    stats={}
    for email, items in buckets.items():
        if len(items) < 3:
            continue
        names = [n for ev in items for n in attendee_names(ev) if n and '@' not in n]
        display = Counter(names).most_common(1)[0][0] if names else email.split('@')[0]
        safe_title = display.replace(chr(34), "'")
        slug = slugify(email.split('@')[0])
        path = PROMOTED_DIR / 'people' / f'{slug}.md'
        recurrences = sum(1 for e in items if e.get('recurring_event'))
        tag_counter = Counter(t for e in items for t in e.get('classification_tags', []) if t not in ('human_attendees_likely','planning_significant'))
        lines = [
            '---',
            'type: person',
            f'title: "{safe_title}"',
            'tags: [calendar, promoted, relationship-memory]',
            '---',
            '',
            f'# {display}',
            '',
            '## Compiled Truth',
            '',
            f'- Primary attendee email: {email}',
            f'- Promoted calendar interactions: {len(items)}',
            f'- Recurring interactions: {recurrences}',
            f"- Dominant calendar signals: {', '.join(f'{k} ({v})' for k,v in tag_counter.most_common(6))}",
            '- Source scope: promoted from Google Calendar 10-year archive based on human-attendee and planning-significance signals.',
            '',
            '---',
            '',
            '## Timeline',
            '',
        ]
        for e in sorted(items, key=lambda x: (x.get('day',''), x.get('start','')), reverse=True)[:40]:
            attendees = ', '.join(attendee_names(e))
            loc = f" 📍 {e.get('location','')}" if e.get('location') else ''
            lines.append(f"- {e.get('day','')} | {e.get('summary','')} — with {attendees}{loc} [Source: Google Calendar, {e.get('day','')}]({e.get('htmlLink','')})")
        atomic_write_text(path, '\n'.join(lines).rstrip() + '\n')
        created += 1
        stats[email] = {'page': str(path), 'interactions': len(items), 'recurring': recurrences}
    return created, stats


def admin_pages(events: list[dict]):
    created=0
    stats={}
    for domain in DOMAINS:
        selected = [e for e in events if e.get(domain) and not e.get('low_signal_block')]
        if not selected:
            continue
        by_year = Counter(e.get('day','')[:4] for e in selected if e.get('day'))
        calendars = Counter(e.get('calendar_summary','') for e in selected)
        path = PROMOTED_DIR / 'admin' / f'{domain}.md'
        lines = [
            '---',
            'type: source',
            f'title: "Calendar archive admin memory — {domain}"',
            f'tags: [calendar, promoted, admin, {domain}]',
            '---',
            '',
            f'# Calendar archive admin memory — {domain}',
            '',
            '## Compiled Truth',
            '',
            f'- Promoted {domain} events: {len(selected)}',
            f"- Year distribution: {', '.join(f'{y}: {c}' for y,c in sorted(by_year.items()))}",
            f"- Common calendars: {', '.join(f'{c} ({n})' for c,n in calendars.most_common(10))}",
            '- Promotion rule: included when deterministic calendar domain tags were present and the event was not only a low-signal block.',
            '',
            '---',
            '',
            '## Timeline',
            '',
        ]
        for e in sorted(selected, key=lambda x: (x.get('day',''), x.get('start','')), reverse=True)[:200]:
            loc = f" 📍 {e.get('location','')}" if e.get('location') else ''
            attendees = ', '.join(attendee_names(e))
            extra = f" — with {attendees}" if attendees else ''
            lines.append(f"- {e.get('day','')} | {e.get('summary','')}{extra}{loc} [Source: Google Calendar, {e.get('day','')}]({e.get('htmlLink','')})")
        atomic_write_text(path, '\n'.join(lines).rstrip() + '\n')
        created += 1
        stats[domain] = {'page': str(path), 'events': len(selected)}
    return created, stats


def overview(events: list[dict], people_count: int, admin_count: int, people_stats: dict, admin_stats: dict) -> str:
    human = sum(1 for e in events if e.get('human_attendees_likely'))
    recurring = sum(1 for e in events if e.get('recurring_event'))
    significant = sum(1 for e in events if e.get('planning_significant'))
    lines = [
        '---',
        'type: source',
        'title: "Calendar archive promotion overview"',
        'tags: [calendar, promoted, overview]',
        '---',
        '',
        '# Calendar archive promotion overview',
        '',
        '## Compiled Truth',
        '',
        f'- Raw events considered: {len(events)}',
        f'- human_attendees_likely events: {human}',
        f'- recurring events: {recurring}',
        f'- planning_significant events: {significant}',
        f'- Promoted people pages: {people_count}',
        f'- Promoted admin pages: {admin_count}',
        '',
        '## Promoted admin pages',
        '',
    ]
    for d, info in sorted(admin_stats.items()):
        lines.append(f"- {d}: {info['events']} events -> {info['page']}")
    lines.extend(['', '## Top promoted relationship pages', ''])
    for email, info in sorted(people_stats.items(), key=lambda kv: kv[1]['interactions'], reverse=True)[:30]:
        lines.append(f"- {email}: {info['interactions']} interactions -> {info['page']}")
    return '\n'.join(lines).rstrip() + '\n'


def main():
    events = load_events()
    people_count, people_stats = people_pages(events)
    admin_count, admin_stats = admin_pages(events)
    overview_path = PROMOTED_DIR / 'overview.md'
    atomic_write_text(overview_path, overview(events, people_count, admin_count, people_stats, admin_stats))
    summary = {
        'raw_events_considered': len(events),
        'promoted_people_pages': people_count,
        'promoted_admin_pages': admin_count,
        'overview_page': str(overview_path),
        'people': people_stats,
        'admin': admin_stats,
    }
    atomic_write_json(SUMMARY_PATH, summary)
    print(json.dumps(summary, indent=2))

if __name__ == '__main__':
    main()
