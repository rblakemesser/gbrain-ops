#!/usr/bin/env python3
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path
from tempfile import NamedTemporaryFile

ROOT = Path(os.environ.get('GBRAIN_OPS_GMAIL_ROOT', Path.home() / '.local/share/gbrain-ops/gmail')).expanduser()
MESSAGES_DIR = ROOT / 'data' / 'messages'
PROMOTED_DIR = ROOT / 'brain' / 'promoted'
SUMMARY_PATH = ROOT / 'data' / 'promotion-summary.json'
SELF_EMAIL = os.environ.get('GBRAIN_OPS_SELF_EMAIL', '')
DOMAINS = ['travel', 'finance', 'health', 'housing', 'recruiting']


@dataclass
class Interaction:
    day: str
    timestamp_utc: str
    subject: str
    sender_name: str
    sender_email: str
    gmail_link: str
    tags: list[str]
    snippet: str
    sent_by_me: bool
    replied_thread: bool


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


def sender_parts(record: dict) -> tuple[str, str]:
    name, email = parseaddr(record.get('from', ''))
    return (name.strip() or email or 'Unknown Sender', (email or '').lower())


def parse_ts(ts: str) -> datetime:
    try:
        return datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except Exception:
        return datetime.min


def load_monthly_records() -> list[dict]:
    rows = []
    for path in sorted(MESSAGES_DIR.glob('????-??.json')):
        rows.extend(json.loads(path.read_text()))
    rows.sort(key=lambda r: (r.get('timestamp_utc', ''), r.get('id', '')))
    return rows


def is_human_high_signal(record: dict) -> bool:
    if record.get('promotional') or record.get('bot_notification'):
        return False
    if record.get('sent_by_me') or record.get('replied_thread'):
        return True
    return bool(record.get('human_sender_likely'))


def is_domain_high_signal(record: dict, domain: str) -> bool:
    if not record.get(domain):
        return False
    if record.get('promotional') and not record.get('receipt'):
        return False
    if record.get('bot_notification') and not record.get('receipt') and not record.get('travel'):
        return False
    return True


def build_people_pages(records: list[dict]) -> tuple[int, dict]:
    by_sender: dict[str, list[Interaction]] = defaultdict(list)
    for r in records:
        name, email = sender_parts(r)
        if not email or email == SELF_EMAIL:
            continue
        if not is_human_high_signal(r):
            continue
        by_sender[email].append(Interaction(
            day=r.get('day', ''),
            timestamp_utc=r.get('timestamp_utc', ''),
            subject=r.get('subject', ''),
            sender_name=name,
            sender_email=email,
            gmail_link=r.get('gmail_link', ''),
            tags=r.get('classification_tags', []),
            snippet=r.get('snippet', ''),
            sent_by_me=bool(r.get('sent_by_me')),
            replied_thread=bool(r.get('replied_thread')),
        ))

    created = 0
    stats = {}
    for email, items in by_sender.items():
        if len(items) < 3:
            continue
        items.sort(key=lambda x: x.timestamp_utc)
        name_counter = Counter(i.sender_name for i in items)
        display_name = name_counter.most_common(1)[0][0]
        slug = slugify(email.split('@')[0])
        path = PROMOTED_DIR / 'people' / f'{slug}.md'
        sent_count = sum(1 for i in items if i.sent_by_me)
        replied_count = sum(1 for i in items if i.replied_thread)
        tag_counter = Counter(t for i in items for t in i.tags if t not in ('human_sender_likely', 'replied_thread', 'sent_by_me'))
        recent = sorted(items, key=lambda x: x.timestamp_utc, reverse=True)[:20]
        safe_title = display_name.replace(chr(34), "'")
        lines = [
            '---',
            'type: person',
            f'title: "{safe_title}"',
            f'tags: [email, promoted, correspondence, {slugify(email.split("@")[1])}]',
            '---',
            '',
            f'# {display_name}',
            '',
            '## Compiled Truth',
            '',
            f'- Primary email: {email}',
            f'- High-signal archived interactions: {len(items)}',
            f'- Messages sent by user in this relationship: {sent_count}',
            f'- Received messages in replied threads: {replied_count}',
        ]
        if tag_counter:
            lines.append(f"- Common email domains/tags: {', '.join(f'{k} ({v})' for k, v in tag_counter.most_common(5))}")
        lines.extend([
            '- Source scope: promoted from Gmail 10-year archive based on sent/replied/human-sender signals.',
            '',
            '---',
            '',
            '## Timeline',
            '',
        ])
        for item in recent:
            flags = []
            if item.sent_by_me:
                flags.append('sent_by_me')
            if item.replied_thread:
                flags.append('replied_thread')
            flag_txt = f" [{' ,'.join(flags)}]" if flags else ''
            lines.append(f"- {item.day} | {item.subject}{flag_txt} [Source: Gmail, {item.day}]({item.gmail_link})")
            if item.snippet:
                lines.append(f"  - {item.snippet[:240]}")
        atomic_write_text(path, '\n'.join(lines).rstrip() + '\n')
        created += 1
        stats[email] = {'page': str(path), 'interactions': len(items), 'sent_by_me': sent_count, 'replied_thread': replied_count}
    return created, stats


def build_admin_pages(records: list[dict]) -> tuple[int, dict]:
    stats = {}
    created = 0
    for domain in DOMAINS:
        selected = [r for r in records if is_domain_high_signal(r, domain)]
        if not selected:
            continue
        selected.sort(key=lambda r: r.get('timestamp_utc', ''), reverse=True)
        by_year = Counter(r.get('day', '')[:4] for r in selected if r.get('day'))
        senders = Counter(sender_parts(r)[0] or sender_parts(r)[1] for r in selected)
        path = PROMOTED_DIR / 'admin' / f'{domain}.md'
        lines = [
            '---',
            'type: source',
            f'title: "Email archive admin memory — {domain}"',
            f'tags: [email, promoted, admin, {domain}]',
            '---',
            '',
            f'# Email archive admin memory — {domain}',
            '',
            '## Compiled Truth',
            '',
            f'- Promoted high-signal {domain} messages: {len(selected)}',
            f"- Year distribution: {', '.join(f'{y}: {c}' for y, c in sorted(by_year.items()))}",
            f"- Common senders: {', '.join(f'{s} ({c})' for s, c in senders.most_common(10))}",
            '- Promotion rule: included when deterministic domain tags were present and the message was not only low-value promo/bot noise.',
            '',
            '---',
            '',
            '## Timeline',
            '',
        ]
        for r in selected[:150]:
            name, email = sender_parts(r)
            lines.append(f"- {r.get('day','')} | {r.get('subject','')} — {name or email} [Source: Gmail, {r.get('day','')}]({r.get('gmail_link','')})")
            if r.get('snippet'):
                lines.append(f"  - {r['snippet'][:240]}")
        atomic_write_text(path, '\n'.join(lines).rstrip() + '\n')
        created += 1
        stats[domain] = {'page': str(path), 'messages': len(selected)}
    return created, stats


def build_overview(records: list[dict], people_count: int, admin_count: int, people_stats: dict, admin_stats: dict) -> str:
    total = len(records)
    promoted_candidates = [r for r in records if is_human_high_signal(r) or any(is_domain_high_signal(r, d) for d in DOMAINS)]
    sent = sum(1 for r in records if r.get('sent_by_me'))
    replied = sum(1 for r in records if r.get('replied_thread'))
    human = sum(1 for r in records if r.get('human_sender_likely'))
    lines = [
        '---',
        'type: source',
        'title: "Email archive promotion overview"',
        'tags: [email, promoted, overview]',
        '---',
        '',
        '# Email archive promotion overview',
        '',
        '## Compiled Truth',
        '',
        f'- Raw archive messages considered: {total}',
        f'- Promotion candidate messages: {len(promoted_candidates)}',
        f'- sent_by_me messages: {sent}',
        f'- replied_thread messages: {replied}',
        f'- human_sender_likely messages: {human}',
        f'- Promoted people pages created: {people_count}',
        f'- Promoted admin pages created: {admin_count}',
        '',
        '## Promoted admin pages',
        '',
    ]
    for domain, info in sorted(admin_stats.items()):
        lines.append(f"- {domain}: {info['messages']} messages -> {info['page']}")
    lines.extend(['', '## Top promoted people pages', ''])
    for email, info in sorted(people_stats.items(), key=lambda kv: kv[1]['interactions'], reverse=True)[:30]:
        lines.append(f"- {email}: {info['interactions']} interactions -> {info['page']}")
    return '\n'.join(lines).rstrip() + '\n'


def main() -> None:
    records = load_monthly_records()
    people_count, people_stats = build_people_pages(records)
    admin_count, admin_stats = build_admin_pages(records)
    overview_path = PROMOTED_DIR / 'overview.md'
    atomic_write_text(overview_path, build_overview(records, people_count, admin_count, people_stats, admin_stats))
    summary = {
        'raw_messages_considered': len(records),
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
