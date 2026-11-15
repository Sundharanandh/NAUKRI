"""Explain scheduling, current-run counts and why links were not selected."""
from collections import Counter
from datetime import datetime, timezone, timedelta
import os

IST = timezone(timedelta(hours=5, minutes=30))
SLOTS = {'30 3 * * *': '09:00 IST', '30 8 * * *': '14:00 IST', '30 13 * * *': '19:00 IST',
         '09:00': '09:00 IST', '14:00': '14:00 IST', '19:00': '19:00 IST'}
REASONS = {
    'skills': ('Skill not confirmed:',),
    'location': ('Preferred city not confirmed', 'Explicit job country is outside India'),
    'experience': ('Experience range excludes', 'Minimum experience exceeds', 'eligibility not unambiguously confirmed'),
    'date': ('Posting outside recent window', 'Posting date unavailable', 'Posting expired'),
    'role': ('Not a developer', 'Role outside', 'Seniority outside'),
}


def run_context(started):
    event = os.environ.get('GITHUB_EVENT_NAME', 'local')
    slot = os.environ.get('DIGEST_SCHEDULE_SLOT', '')
    target = SLOTS.get(slot, 'Manual / local run' if event != 'schedule' else '09:00, 14:00, 19:00 IST (legacy combined schedule)')
    return {'trigger': event, 'scheduled_target': target,
            'runner_started_utc': os.environ.get('DIGEST_RUN_STARTED_UTC', started.isoformat()),
            'collection_started_utc': started.isoformat(),
            'report_generated_utc': datetime.now(timezone.utc).isoformat(),
            'run_url': 'https://github.com/'+os.environ['GITHUB_REPOSITORY']+'/actions/runs/'+os.environ['GITHUB_RUN_ID'] if os.environ.get('GITHUB_RUN_ID') and os.environ.get('GITHUB_REPOSITORY') else ''}


def current_counts(db, urls, refreshed):
    states = Counter()
    reasons = Counter()
    already_sent = 0
    for url, state, reason, mailed in db.execute('SELECT url,state,reason,mailed FROM jobs'):
        if url not in urls: continue
        states[state] += 1
        if mailed: already_sent += 1
        if not mailed and state != 'Matched':
            for category, words in REASONS.items():
                if any(word in (reason or '') for word in words): reasons[category] += 1
    return {'unique_collected': len(urls), 'checked': len(refreshed), 'already_emailed': already_sent,
            'current_states': dict(states), 'filter_reasons': dict(reasons)}


def ist(stamp):
    try:
        value = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if value.tzinfo is None: value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(IST).strftime('%d %b %Y %I:%M %p IST')
    except (ValueError, TypeError, AttributeError): return 'Not available'


def summary_lines(summary):
    context = summary.get('run_context', {})
    counts = summary.get('current_run', {})
    status = summary.get('source_status', [])
    failed = sum(r.get('status') == 'error' for r in status)
    incomplete = sum(bool(r.get('status') == 'partial' or r.get('capped') or r.get('detail_cap_reached')) for r in status)
    lines = ['RUN SUMMARY',
             'Daily target slot: '+context.get('scheduled_target', 'Not available'),
             'Runner started: '+ist(context.get('runner_started_utc')),
             'Report generated: '+ist(context.get('report_generated_utc')),
             'Trigger: '+context.get('trigger', 'Not available'),
             'GitHub scheduled runs are best effort; target times are not guaranteed email arrival times.',
             '', f"Unique job records collected: {counts.get('unique_collected', 0):,}",
             f"Rechecked this run: {counts.get('checked', 0):,}",
             f"Previously emailed records skipped: {counts.get('already_emailed', 0):,}",
             f"New confirmed links selected: {summary.get('new_matches_for_email', 0)}",
             f"New labelled review links selected: {summary.get('new_review_candidates_for_email', 0)}",
             f'Sources reporting errors: {failed}; partial or capped sources: {incomplete}']
    if counts.get('unique_collected', 0) == 0:
        lines.append('No job records were collected. This does not establish that no suitable vacancies exist.')
    elif summary.get('new_matches_for_email', 0) == 0:
        lines.append('Zero confirmed links means no new collected record passed every configured check; it does not mean zero jobs were searched.')
    reasons = counts.get('filter_reasons', {})
    if reasons:
        lines += ['Checks not passed or not confirmed (a job can appear in several counts):',
                  ', '.join(f'{key}: {value:,}' for key,value in reasons.items())]
    if context.get('run_url'): lines.append('Run details: '+context['run_url'])
    return lines


def add_summary_sheet(workbook, summary):
    sheet = workbook.create_sheet('Run summary', 0)
    sheet.append(['Metric', 'Value'])
    counts = summary.get('current_run', {})
    context = summary.get('run_context', {})
    entries = [('Target slot', context.get('scheduled_target')), ('Runner started (IST)', ist(context.get('runner_started_utc'))),
               ('Report generated (IST)', ist(context.get('report_generated_utc'))),
               ('Unique job records collected', counts.get('unique_collected', 0)), ('Rechecked this run', counts.get('checked', 0)),
               ('Previously emailed records skipped', counts.get('already_emailed', 0)),
               ('New confirmed links selected', summary.get('new_matches_for_email', 0)),
               ('New review links selected', summary.get('new_review_candidates_for_email', 0))]
    entries += [('Current records: '+name, value) for name,value in counts.get('current_states', {}).items()]
    entries += [('Filter not passed/confirmed: '+name, value) for name,value in counts.get('filter_reasons', {}).items()]
    entries += [('Note', 'Filter counts overlap. Other job sheets retain historical rows; their totals differ from current-run counts.'),
                ('Scheduling', 'Best-effort GitHub trigger; an external scheduler can improve triggering, but runner queues and email delivery can still delay arrival.'), ('Run URL', context.get('run_url'))]
    for row in entries: sheet.append(row)
    sheet.column_dimensions['A'].width = 44
    sheet.column_dimensions['B'].width = 100
    sheet.freeze_panes = 'A2'
