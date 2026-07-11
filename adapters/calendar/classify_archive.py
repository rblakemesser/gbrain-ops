#!/usr/bin/env python3
import json
import os
import re
from collections import Counter
from pathlib import Path
from tempfile import NamedTemporaryFile

ROOT = Path(os.environ.get('GBRAIN_OPS_CALENDAR_ROOT', Path.home() / '.local/share/gbrain-ops/calendar')).expanduser()
EVENTS_DIR = ROOT / 'data' / 'events'
SUMMARY_PATH = ROOT / 'data' / 'classification-summary.json'
SELF_EMAIL = os.environ.get('GBRAIN_OPS_SELF_EMAIL', '')
PERSONAL_CAL_NAMES = {value.strip() for value in os.environ.get('GBRAIN_OPS_PERSONAL_CALENDAR_NAMES', 'Personal,Family').split(',') if value.strip()}
TRAVEL_PATTERNS = ['flight', 'airport', 'hotel', 'airbnb', 'trip', 'travel', 'boarding', 'rental', 'delta', 'united', 'southwest', 'vacation']
FINANCE_PATTERNS = ['tax', 'finance', 'financial', 'insurance', 'mortgage', 'loan', 'closing', 'bank', 'broker', 'advisor', 'payroll']
HEALTH_PATTERNS = ['doctor', 'dentist', 'therapy', 'pt ', 'physical therapy', 'eval assessment', 'medical', 'prenuvo', 'clinic', 'hospital', 'fitness']
HOUSING_PATTERNS = ['tour', 'inspection', 'closing', 'mortgage', 'realtor', 'house', 'home', 'property', 'valley view', 'contractor', 'apartment', 'lease']
RECRUITING_PATTERNS = ['interview', 'recruit', 'recruiting', 'candidate', 'hiring', 'job', 'onsite', 'screen']
SOCIAL_PATTERNS = ['birthday', 'dinner', 'lunch', 'brunch', 'party', 'wedding', 'coffee', 'date night', 'family dinner']
LOW_SIGNAL_PATTERNS = ['hold', 'block', 'focus time', 'ooo', 'out of office', 'busy', 'reminder', 'recycling day', 'reciclaje', 'glp']
WORK_PATTERNS = ['standup', '1:1', 'board', 'sync', 'retro', 'planning', 'review', 'launch', 'prod', 'deploy', 'team']


def atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile('w', delete=False, dir=str(path.parent), encoding='utf-8') as tmp:
        json.dump(data, tmp, indent=2, ensure_ascii=False)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def text_blob(event: dict) -> str:
    return ' '.join([
        event.get('summary', ''),
        event.get('description', ''),
        event.get('location', ''),
        event.get('calendar_summary', ''),
        event.get('organizer_name', ''),
        event.get('organizer_email', ''),
        ' '.join(event.get('attendees', []) or []),
    ]).lower()


def matches_any(text: str, pats: list[str]) -> bool:
    return any(p in text for p in pats)


def attendee_emails(event: dict) -> list[str]:
    out=[]
    for a in event.get('attendees', []) or []:
        m = re.search(r'([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})', a, re.I)
        if m:
            out.append(m.group(1).lower())
    return out


def human_attendees_likely(event: dict) -> bool:
    attendees = attendee_emails(event)
    nonself = [a for a in attendees if a != SELF_EMAIL]
    if nonself:
        return True
    org = (event.get('organizer_email') or '').lower()
    return bool(org and org != SELF_EMAIL and 'group.calendar.google.com' not in org)


def classify(event: dict) -> dict:
    text = text_blob(event)
    attendees = attendee_emails(event)
    nonself_count = len([a for a in attendees if a != SELF_EMAIL])
    recurring = bool(event.get('recurringEventId'))
    all_day = bool(event.get('is_all_day'))
    human = human_attendees_likely(event)
    travel = matches_any(text, TRAVEL_PATTERNS)
    finance = matches_any(text, FINANCE_PATTERNS)
    health = matches_any(text, HEALTH_PATTERNS)
    housing = matches_any(text, HOUSING_PATTERNS)
    recruiting = matches_any(text, RECRUITING_PATTERNS)
    social = matches_any(text, SOCIAL_PATTERNS)
    work_related = matches_any(text, WORK_PATTERNS) or (event.get('calendar_summary') not in PERSONAL_CAL_NAMES)
    family_related = 'family' in text or event.get('calendar_summary') in PERSONAL_CAL_NAMES
    low_signal = matches_any(text, LOW_SIGNAL_PATTERNS) and nonself_count == 0 and not any([travel, finance, health, housing, recruiting, social])
    planning_significant = recurring or nonself_count >= 2 or any([travel, finance, health, housing, recruiting])

    tags=[]
    if human: tags.append('human_attendees_likely')
    if recurring: tags.append('recurring_event')
    if all_day: tags.append('all_day')
    if travel: tags.append('travel')
    if finance: tags.append('finance')
    if health: tags.append('health')
    if housing: tags.append('housing')
    if recruiting: tags.append('recruiting')
    if social: tags.append('social')
    if work_related: tags.append('work_related')
    if family_related: tags.append('family_related')
    if low_signal: tags.append('low_signal_block')
    if planning_significant: tags.append('planning_significant')

    event['human_attendees_likely'] = human
    event['recurring_event'] = recurring
    event['all_day'] = all_day
    event['travel'] = travel
    event['finance'] = finance
    event['health'] = health
    event['housing'] = housing
    event['recruiting'] = recruiting
    event['social'] = social
    event['work_related'] = work_related
    event['family_related'] = family_related
    event['low_signal_block'] = low_signal
    event['planning_significant'] = planning_significant
    event['classification_tags'] = tags
    return event


def main() -> None:
    monthly = sorted(EVENTS_DIR.glob('????-??.json'))
    summary = {'files_processed': 0, 'events_processed': 0, 'classification_counts': Counter(), 'months': {}}
    for path in monthly:
        rows = json.loads(path.read_text())
        out=[]
        counts=Counter()
        for row in rows:
            row = classify(row)
            for tag in row['classification_tags']:
                counts[tag]+=1
                summary['classification_counts'][tag]+=1
            out.append(row)
        atomic_write_json(path, out)
        summary['files_processed'] += 1
        summary['events_processed'] += len(out)
        summary['months'][path.stem] = {'events': len(out), 'counts': dict(counts)}
        print(f'classified {path.stem}: {len(out)} events')
    summary['classification_counts'] = dict(summary['classification_counts'])
    atomic_write_json(SUMMARY_PATH, summary)
    print(json.dumps(summary, indent=2))

if __name__ == '__main__':
    main()
