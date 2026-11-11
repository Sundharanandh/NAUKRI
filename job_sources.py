"""Job-alert URL recognition and read-only public company board feeds.

No job-site login, application submission, private ATS API or API key is used.
Only explicitly configured tenants are polled on shared company board hosts.
"""
import html
import json
import re
from urllib.parse import urlparse
from urllib.request import Request, HTTPRedirectHandler, build_opener

PLATFORMS = {
    'LinkedIn': (('linkedin.com',), r'/jobs/view/[^/]+'),
    'Naukri': (('naukri.com',), r'/job-listings-[^/]+'),
    'Indeed': (('indeed.com', 'indeed.co.in'), r'/viewjob$'),
    'Foundit': (('foundit.in',), r'/job/[^/]+'),
    'Instahyre': (('instahyre.com',), r'/job-\d+(?:-|/|$)'),
    'Hirist': (('hirist.tech', 'hirist.com'), r'/j/[^/]+'),
    'Cutshort': (('cutshort.io',), r'/(?:job|a)/[^/]+'),
    'Wellfound': (('wellfound.com',), r'/jobs/\d+(?:-|/|$)'),
}
SHARED_BOARD_HOSTS = {
    'boards.greenhouse.io', 'job-boards.greenhouse.io',
    'jobs.lever.co', 'jobs.eu.lever.co', 'jobs.ashbyhq.com',
}


def host_matches(host, domain):
    return host == domain or host.endswith('.' + domain)


def platform_name(url):
    parts = urlparse(url)
    host = (parts.hostname or '').lower()
    for name, (domains, pattern) in PLATFORMS.items():
        if any(host_matches(host, domain) for domain in domains) and re.search(pattern, parts.path, re.I):
            if name == 'Indeed' and not re.search(r'(?:^|&)(?:jk|vjk)=', parts.query):
                continue
            return name
    return None


def configured_boards(cfg):
    boards = cfg.get('company_boards', [])
    if not isinstance(boards, list) or len(boards) > 20:
        raise ValueError('company_boards must be an array with at most 20 boards')
    seen = set()
    for entry in boards:
        if not isinstance(entry, dict):
            raise ValueError('Each company board must be an object')
        if entry.get('enabled', True) is False:
            continue
        provider, board = entry.get('provider'), entry.get('board', '')
        if provider not in ('greenhouse', 'lever', 'ashby'):
            raise ValueError('Unknown company board provider')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', board):
            raise ValueError('Use a company board slug, not a URL, in company_boards')
        region = entry.get('region', 'global')
        if region not in ('global', 'eu') or region == 'eu' and provider != 'lever':
            raise ValueError('Only Lever supports region=eu')
        identity = (provider, board, region)
        if identity in seen:
            continue
        seen.add(identity)
        yield entry | {'provider': provider, 'board': board, 'region': region}


def board_name(url, cfg):
    p = urlparse(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None, 443):
        return None
    path = p.path.strip('/').split('/')
    host = (p.hostname or '').lower()
    for b in configured_boards(cfg):
        if not path or path[0].casefold() != b['board'].casefold():
            continue
        if b['provider'] == 'greenhouse':
            valid = host in ('boards.greenhouse.io', 'job-boards.greenhouse.io') and len(path) == 3 and path[1] == 'jobs' and path[2].isdigit()
        elif b['provider'] == 'lever':
            expected = 'jobs.eu.lever.co' if b['region'] == 'eu' else 'jobs.lever.co'
            valid = host == expected and len(path) == 2 and bool(re.fullmatch(r'[A-Za-z0-9-]+', path[1]))
        else:
            valid = host == 'jobs.ashbyhq.com' and len(path) == 2 and bool(re.fullmatch(r'[A-Za-z0-9-]+', path[1]))
        if valid:
            return b['provider'].title() + ':' + b['board']
    return None


def source_name(url, cfg):
    return platform_name(url) or board_name(url, cfg) or 'Company careers'


def allowed_alert_url(url, cfg):
    p = urlparse(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None, 443):
        return False
    name = platform_name(url)
    if name:
        return name in cfg.get('enabled_platforms', list(PLATFORMS))
    if board_name(url, cfg):
        return True
    host = p.hostname.lower()
    # An entire shared ATS domain would include unrelated employers.
    if host in SHARED_BOARD_HOSTS:
        return False
    return any(host_matches(host, domain.strip().lower()) for domain in cfg.get('company_domains', []) if domain.strip())


def read_json(url):
    class SameHost(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if urlparse(newurl).scheme != 'https' or urlparse(newurl).hostname != urlparse(url).hostname:
                raise ValueError('Unexpected API redirect')
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    request = Request(url, headers={'Accept': 'application/json', 'User-Agent': 'PersonalJobDigest/1.0'})
    with build_opener(SameHost()).open(request, timeout=25) as response:
        body = response.read(10_000_001)
        if len(body) > 10_000_000:
            raise ValueError('Company board response too large')
        if 'json' not in response.headers.get('Content-Type', '').lower():
            raise ValueError('Company board did not return JSON')
        return json.loads(body.decode('utf-8'))


def endpoint(board):
    provider, slug = board['provider'], board['board']
    if provider == 'greenhouse':
        return f'https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true'
    if provider == 'lever':
        host = 'api.eu.lever.co' if board['region'] == 'eu' else 'api.lever.co'
        return f'https://{host}/v0/postings/{slug}?mode=json&limit=100&skip=0'
    return f'https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true'


def job_location(name, country=''):
    return {'address': {'addressLocality': name or '', 'addressCountry': country or ''}}


def normalize_posting(row, board):
    """Map genuine public fields to JobPosting. Never infer publication from updates."""
    provider, slug = board['provider'], board['board']
    result = {'@type': 'JobPosting', 'hiringOrganization': {'name': board.get('company') or slug}}
    if provider == 'greenhouse':
        ident = str(row.get('id', ''))
        if not ident.isdigit():
            raise ValueError('Missing Greenhouse job ID')
        result.update(url=f'https://job-boards.greenhouse.io/{slug}/jobs/{ident}',
                      title=row.get('title', ''), description=html.unescape(row.get('content') or ''),
                      jobLocation=job_location((row.get('location') or {}).get('name', '')),
                      datePosted=row.get('first_published') or row.get('first_published_at'))
    elif provider == 'lever':
        ident = str(row.get('id', ''))
        if not re.fullmatch(r'[A-Za-z0-9-]+', ident):
            raise ValueError('Missing Lever posting ID')
        host = 'jobs.eu.lever.co' if board['region'] == 'eu' else 'jobs.lever.co'
        categories = row.get('categories') or {}
        places = categories.get('allLocations') or [categories.get('location', '')]
        if isinstance(places, str):
            places = [places]
        sections = [row.get('descriptionPlain') or row.get('description') or '']
        for section in row.get('lists') or []:
            sections += [section.get('text', ''), section.get('content', '')]
        sections.append(row.get('additionalPlain') or row.get('additional') or '')
        result.update(url=f'https://{host}/{slug}/{ident}', title=row.get('text', ''),
                      description='\n'.join(sections), jobLocation=[job_location(x, row.get('country')) for x in places],
                      datePosted=row.get('publishedAt'), baseSalary=row.get('salaryRange'))
        # Lever createdAt is not a documented publication date. Missing dates require review.
    else:
        if row.get('isListed') is False:
            return None
        url = row.get('jobUrl', '')
        if not board_name(url, {'company_boards': [board]}):
            raise ValueError('Ashby job URL is outside the configured company board')
        address = (row.get('address') or {}).get('postalAddress') or {}
        places = [{'address': address}] if address else [job_location(row.get('location'))]
        for secondary in row.get('secondaryLocations') or []:
            address = secondary.get('address') or {}
            places.append({'address': address} if address else job_location(secondary.get('location')))
        result.update(url=url, title=row.get('title', ''),
                      description=row.get('descriptionHtml') or row.get('descriptionPlain') or '',
                      jobLocation=places, datePosted=row.get('publishedAt'),
                      baseSalary=row.get('compensation'), employmentType=row.get('employmentType'))
    return result


def collect_company_jobs(cfg):
    """Return normalized postings and per-board status; isolate provider failures."""
    jobs, statuses = [], []
    per_board = cfg.get('max_company_jobs_per_board', 300)
    if not isinstance(per_board, int) or not 1 <= per_board <= 1000:
        raise ValueError('max_company_jobs_per_board must be 1-1000')
    # Validate the complete configuration before making any requests.
    boards = list(configured_boards(cfg))
    for board in boards:
        label = board['provider'].title() + ':' + board['board']
        count = 0
        invalid = 0
        try:
            url = endpoint(board)
            while count + invalid < per_board:
                data = read_json(url)
                rows = data if board['provider'] == 'lever' else data.get('jobs')
                if not isinstance(rows, list):
                    raise ValueError('Unexpected company board schema')
                if board['provider'] != 'lever':
                    # Updated timestamps are deliberately excluded from freshness ranking.
                    from datetime import datetime, timezone
                    def published_key(row):
                        stamp = row.get('publishedAt') or row.get('first_published') or row.get('first_published_at')
                        try:
                            value = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
                            if value.tzinfo is None: value = value.replace(tzinfo=timezone.utc)
                            return value.timestamp()
                        except (ValueError, TypeError):
                            return float('-inf')
                    rows = sorted(rows, key=published_key, reverse=True)
                for row in rows[:per_board - count - invalid]:
                    try:
                        job = normalize_posting(row, board)
                        if job:
                            jobs.append(job)
                        count += 1
                    except (ValueError, TypeError, AttributeError):
                        invalid += 1
                if board['provider'] != 'lever' or len(rows) < 100:
                    break
                url = endpoint(board).replace('skip=0', 'skip=' + str(count + invalid))
            statuses.append({'source': label, 'status': 'ok' if invalid == 0 else 'partial',
                             'postings_read': count, 'invalid_postings': invalid,
                             'capped': count + invalid >= per_board})
        except Exception as exc:
            statuses.append({'source': label, 'status': 'error', 'postings_read': count,
                             'error': type(exc).__name__ + (f' {exc.code}' if isinstance(getattr(exc, 'code', None), int) else '')})
    return jobs, statuses
