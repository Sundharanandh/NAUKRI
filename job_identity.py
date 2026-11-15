"""Deduplicate proven provider IDs, never merely similar job titles."""
import hashlib
import re
from urllib.parse import urlsplit, parse_qs


def job_identity(url):
    p = urlsplit(url)
    host = (p.hostname or '').lower()
    parts = p.path.strip('/').split('/')
    if 'greenhouse.io' in host and len(parts) >= 3 and parts[-2] == 'jobs' and parts[-1].isdigit():
        region = 'eu' if '.eu.' in host else 'global'
        return f'greenhouse:{region}:{parts[0].lower()}:{parts[-1]}'
    if host.endswith('.myworkdayjobs.com'):
        found = re.search(r'_([^/_]+)$', p.path)
        if found: return f'workday:{host}:{found.group(1)}'
    if host == 'jobs.smartrecruiters.com' and len(parts) == 2:
        found = re.match(r'\d+', parts[1])
        if found: return f'smartrecruiters:{parts[0].lower()}:{found.group()}'
    if host in ('jobs.lever.co', 'jobs.eu.lever.co') and len(parts) == 2:
        return f'lever:{host}:{parts[0].lower()}:{parts[1]}'
    if host == 'jobs.ashbyhq.com' and len(parts) == 2:
        return f'ashby:{parts[0].lower()}:{parts[1]}'
    if host == 'linkedin.com' or host.endswith('.linkedin.com'):
        found = re.search(r'/jobs/view/(?:[^/]*-)?(\d+)', p.path)
        if found: return 'linkedin:' + found.group(1)
    if host == 'naukri.com' or host.endswith('.naukri.com'):
        found = re.search(r'-(\d{6,})$', p.path.rstrip('/'))
        if found: return 'naukri:' + found.group(1)
    if host == 'indeed.com' or host.endswith('.indeed.com') or host.endswith('.indeed.co.in'):
        query = parse_qs(p.query)
        ident = query.get('jk', query.get('vjk', []))
        if ident: return 'indeed:' + ident[0]
    return url


def fingerprint_for_url(url):
    return hashlib.sha256(job_identity(url).encode()).hexdigest()
