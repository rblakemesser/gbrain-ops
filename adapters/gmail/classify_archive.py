#!/usr/bin/env python3
import json
import os
import re
from collections import Counter
from pathlib import Path
from tempfile import NamedTemporaryFile

ROOT = Path(os.environ.get('GBRAIN_OPS_GMAIL_ROOT', Path.home() / '.local/share/gbrain-ops/gmail')).expanduser()
MESSAGES_DIR = ROOT / 'data' / 'messages'
SUMMARY_PATH = ROOT / 'data' / 'classification-summary.json'

PROMOTIONAL_PATTERNS = [
    'sale', 'off ', '% off', 'limited time', 'shop now', 'spring savings',
    'summer savings', 'mother\'s day', 'father\'s day', 'black friday',
    'cyber monday', 'gift sale', 'promotion', 'promo', 'coupon', 'deal of the day',
]
TRANSACTIONAL_PATTERNS = [
    'receipt', 'invoice', 'order', 'your order', 'payment', 'paid', 'statement',
    'billing', 'subscription', 'renewal', 'confirmation', 'confirmed', 'security alert',
    'verification', 'code', 'password reset', 'shipment', 'shipped', 'track order',
]
RECEIPT_PATTERNS = [
    'receipt', 'invoice', 'order #', 'order ', 'payment received', 'thanks for your order',
    'thanks for paying', 'purchase', 'statement',
]
TRAVEL_PATTERNS = [
    'flight', 'hotel', 'reservation', 'itinerary', 'boarding', 'airbnb', 'vrbo',
    'check-in', 'trip', 'expedia', 'airlines', 'rental car', 'delta', 'united', 'southwest',
]
FINANCE_PATTERNS = [
    'bank', 'credit card', 'statement', 'insurance payment', 'paypal', 'irs', 'tax',
    'invoice', 'bill', 'mortgage', 'loan', 'brokerage', 'refund',
]
HEALTH_PATTERNS = [
    'doctor', 'medical', 'mychart', 'appointment', 'medicare', 'dental', 'pharmacy',
    'health', 'clinic', 'hospital', 'prescription',
]
HOUSING_PATTERNS = [
    'tour', 'lease', 'apartment', 'mortgage', 'inspection', 'contractor', 'hoa',
    'house', 'home', 'property', 'realtor', 'real estate', 'valley view',
]
RECRUITING_PATTERNS = [
    'hiring', 'interview', 'candidate', 'recruiter', 'recruiting', 'job opportunity',
    'application', 'resume', 'cv', 'role is wrapping up',
]
WORK_NOTIFICATION_PATTERNS = [
    'github', 'circleci', 'build', 'pull request', 'workflow canceled', 'testflight',
    'copilot', 'dependabot', 'ci', 'deploy',
]
BOT_PATTERNS = [
    'no-reply', 'noreply', 'notifications@', 'mailer-daemon', 'postmaster', 'donotreply',
    '[bot]', 'via groups.io', 'github', 'circleci', 'testflight',
]
HUMAN_HINT_PATTERNS = [
    '@gmail.com', '@icloud.com', '@me.com', '@yahoo.com', '@outlook.com', '@hotmail.com',
]
ORDER_SENDER_PATTERNS = [
    'amazon', 'shopify', 'paypal', 'stripe', 'usps', 'ups', 'fedex', 'doordash', 'hims',
]


def atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile('w', delete=False, dir=str(path.parent), encoding='utf-8') as tmp:
        json.dump(data, tmp, indent=2, ensure_ascii=False)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def text_blob(record: dict) -> str:
    return ' '.join([
        record.get('from', ''),
        record.get('to', ''),
        record.get('cc', ''),
        record.get('subject', ''),
        record.get('snippet', ''),
        ' '.join(record.get('labelIds') or []),
    ]).lower()


def matches_any(text: str, patterns: list[str]) -> bool:
    return any(p in text for p in patterns)


def sender_email(record: dict) -> str:
    sender = record.get('from', '')
    m = re.search(r'<([^>]+)>', sender)
    if m:
        return m.group(1).lower()
    return sender.lower()


def sender_name(record: dict) -> str:
    sender = record.get('from', '')
    if '<' in sender:
        return sender.split('<', 1)[0].strip().strip('"').lower()
    return sender.lower()


def human_sender_likely(record: dict, text: str) -> bool:
    email = sender_email(record)
    name = sender_name(record)
    if not email:
        return False
    if matches_any(email + ' ' + name, BOT_PATTERNS):
        return False
    if any(x in email for x in HUMAN_HINT_PATTERNS):
        return True
    if record.get('is_sent'):
        return True
    if '.' in name and len(name.split()) >= 2 and '@' in email and 'no-reply' not in email and 'noreply' not in email:
        return True
    if 'CATEGORY_PERSONAL' in (record.get('labelIds') or []) and not matches_any(text, ORDER_SENDER_PATTERNS):
        return True
    return False


def classify_record(record: dict, replied_threads: set[str]) -> dict:
    text = text_blob(record)
    labels = record.get('labelIds') or []
    sent_by_me = bool(record.get('is_sent'))
    promotional = 'CATEGORY_PROMOTIONS' in labels or matches_any(text, PROMOTIONAL_PATTERNS)
    transactional = 'CATEGORY_UPDATES' in labels or matches_any(text, TRANSACTIONAL_PATTERNS)
    receipt = matches_any(text, RECEIPT_PATTERNS) or (transactional and matches_any(text, ORDER_SENDER_PATTERNS))
    travel = matches_any(text, TRAVEL_PATTERNS)
    finance = matches_any(text, FINANCE_PATTERNS)
    health = matches_any(text, HEALTH_PATTERNS)
    housing = matches_any(text, HOUSING_PATTERNS)
    recruiting = matches_any(text, RECRUITING_PATTERNS)
    work_notification = matches_any(text, WORK_NOTIFICATION_PATTERNS)
    bot_notification = matches_any(text, BOT_PATTERNS)
    human = human_sender_likely(record, text)
    replied_thread = record.get('threadId', '') in replied_threads and not sent_by_me

    classes = []
    if sent_by_me:
        classes.append('sent_by_me')
    if replied_thread:
        classes.append('replied_thread')
    if human:
        classes.append('human_sender_likely')
    if promotional:
        classes.append('promotional')
    if transactional:
        classes.append('transactional')
    if receipt:
        classes.append('receipt')
    if travel:
        classes.append('travel')
    if finance:
        classes.append('finance')
    if health:
        classes.append('health')
    if housing:
        classes.append('housing')
    if recruiting:
        classes.append('recruiting')
    if work_notification:
        classes.append('work_notification')
    if bot_notification:
        classes.append('bot_notification')

    record['sent_by_me'] = sent_by_me
    record['replied_thread'] = replied_thread
    record['human_sender_likely'] = human
    record['promotional'] = promotional
    record['transactional'] = transactional
    record['receipt'] = receipt
    record['travel'] = travel
    record['finance'] = finance
    record['health'] = health
    record['housing'] = housing
    record['recruiting'] = recruiting
    record['work_notification'] = work_notification
    record['bot_notification'] = bot_notification
    record['classification_tags'] = classes
    return record


def main() -> None:
    monthly_files = sorted(MESSAGES_DIR.glob('????-??.json'))
    replied_threads = set()
    for path in monthly_files:
        rows = json.loads(path.read_text())
        for r in rows:
            if r.get('is_sent'):
                replied_threads.add(r.get('threadId', ''))

    summary = {
        'files_processed': 0,
        'messages_processed': 0,
        'classification_counts': Counter(),
        'months': {},
    }

    for path in monthly_files:
        rows = json.loads(path.read_text())
        new_rows = []
        month_counter = Counter()
        for row in rows:
            row = classify_record(row, replied_threads)
            for tag in row['classification_tags']:
                summary['classification_counts'][tag] += 1
                month_counter[tag] += 1
            new_rows.append(row)
        atomic_write_json(path, new_rows)
        summary['files_processed'] += 1
        summary['messages_processed'] += len(new_rows)
        summary['months'][path.stem] = {
            'messages': len(new_rows),
            'counts': dict(month_counter),
        }
        print(f'classified {path.stem}: {len(new_rows)} messages')

    summary['classification_counts'] = dict(summary['classification_counts'])
    atomic_write_json(SUMMARY_PATH, summary)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
