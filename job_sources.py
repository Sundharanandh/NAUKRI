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
    'boards.greenhouse.io', 'job-boards.greenhouse.io', 'boards.eu.greenhouse.io', 'job-boards.eu.greenhouse.io',
    'jobs.smartrecruiters.com', 'careers.smartrecruiters.com',
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
    if not isinstance(boards, list) or len(boards) > 200:
        raise ValueError('company_boards must be an array with at most 200 boards')
    seen = set()
    for entry in boards:
        if not isinstance(entry, dict):
            raise ValueError('Each company board must be an object')
        if entry.get('enabled', True) is False:
            continue
        provider, board = entry.get('provider'), entry.get('board', '')
        if provider not in ('greenhouse', 'lever', 'ashby', 'smartrecruiters', 'workday'):
            raise ValueError('Unknown company board provider')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', board):
            raise ValueError('Use a company board slug, not a URL, in company_boards')
        region = entry.get('region', 'global')
        if region not in ('global', 'eu') or region == 'eu' and provider != 'lever':
            raise ValueError('Only Lever supports region=eu')
        if provider == 'workday':
            if not re.fullmatch(re.escape(board) + r'\.wd\d+\.myworkdayjobs\.com', entry.get('host', '')):
                raise ValueError('Workday host must match the configured public tenant')
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', entry.get('site', '')):
                raise ValueError('Workday site slug required')
        country = entry.get('country', '')
        if country and (provider != 'smartrecruiters' or not re.fullmatch(r'[a-z]{2}', country)):
            raise ValueError('country must be a two-letter lowercase SmartRecruiters filter')
        identity = (provider, board, region, entry.get('site') if provider == 'workday' else None)
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
        if b['provider'] == 'workday':
            route = path[1:] if path and re.fullmatch(r'[a-z]{2}-[A-Z]{2}', path[0]) else path
            if host == b['host'] and len(route) >= 3 and route[0] == b['site'] and route[1] == 'job':
                return 'Workday:' + b['board'] + '/' + b['site']
            continue
        if not path or path[0].casefold() != b['board'].casefold():
            continue
        if b['provider'] == 'greenhouse':
            valid = host in ('boards.greenhouse.io', 'job-boards.greenhouse.io', 'boards.eu.greenhouse.io', 'job-boards.eu.greenhouse.io') and len(path) == 3 and path[1] == 'jobs' and path[2].isdigit()
        elif b['provider'] == 'lever':
            expected = 'jobs.eu.lever.co' if b['region'] == 'eu' else 'jobs.lever.co'
            valid = host == expected and len(path) == 2 and bool(re.fullmatch(r'[A-Za-z0-9-]+', path[1]))
        elif b['provider'] == 'smartrecruiters':
            valid = host == 'jobs.smartrecruiters.com' and len(path) == 2 and bool(re.match(r'\d+(?:-|$)', path[1]))
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
    if host in SHARED_BOARD_HOSTS or host.endswith('.myworkdayjobs.com'):
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
    if provider == 'smartrecruiters':
        return f'https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=0' + ('&country=' + board['country'] if board.get('country') else '')
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
        candidate = row.get('absolute_url', '')
        actual_url = candidate if board_name(candidate, {'company_boards': [board]}) else f'https://job-boards.greenhouse.io/{slug}/jobs/{ident}'
        result.update(url=actual_url,
                      title=row.get('title', ''), description=html.unescape(row.get('content') or ''),
                      jobLocation=job_location((row.get('location') or {}).get('name', '')),
                      datePosted=row.get('first_published') or row.get('first_published_at'))
    elif provider == 'smartrecruiters':
        ident = str(row.get('id', ''))
        if not ident.isdigit(): raise ValueError('Missing SmartRecruiters posting ID')
        sections = (row.get('jobAd') or {}).get('sections') or {}
        description = '\n'.join(part.get('text', '') for key, part in sections.items() if isinstance(part, dict) and key != 'companyDescription')
        loc = row.get('location') or {}
        candidate = row.get('postingUrl', '')
        actual_url = candidate if board_name(candidate, {'company_boards': [board]}) else f'https://jobs.smartrecruiters.com/{slug}/{ident}'
        result.update(url=actual_url, title=row.get('name', ''), description=description,
                      jobLocation=job_location(loc.get('city') or loc.get('fullLocation'), loc.get('country')),
                      datePosted=row.get('releasedDate'))
        if row.get('active') is False: return None
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


def collect_smartrecruiters(board, cfg, per_board):
    """Read public listings; fetch descriptions only for plausible target roles."""
    jobs, count, errors, offset = [], 0, 0, 0
    detail_limit = cfg.get('max_company_details_per_board', 30)
    if not isinstance(detail_limit, int) or not 1 <= detail_limit <= 100:
        raise ValueError('max_company_details_per_board must be 1-100')
    details = 0
    while count < per_board:
        data = read_json(endpoint(board).replace('offset=0', 'offset=' + str(offset)))
        rows = data.get('content')
        if not isinstance(rows, list): raise ValueError('Unexpected SmartRecruiters schema')
        for row in rows[:per_board-count]:
            count += 1
            if not isinstance(row, dict): errors += 1; continue
            title = row.get('name', '')
            location = row.get('location') or {}
            place = location.get('city', '') + ' ' + location.get('fullLocation', '')
            plausible = bool(re.search(r'\b(developer|engineer)\b', title, re.I))
            plausible = plausible and not re.search(r'\b(senior|sr\.?|lead|staff|principal|architect|manager|qa|sdet|support|devops)\b', title, re.I)
            plausible = plausible and any(re.search(r'\b'+re.escape(city)+r'\b', place, re.I) for city in cfg.get('cities', []))
            if plausible and details < detail_limit:
                details += 1
                ident = str(row.get('id', ''))
                if ident.isdigit():
                    try: row = row | read_json(f"https://api.smartrecruiters.com/v1/companies/{board['board']}/postings/{ident}")
                    except Exception: errors += 1
            try:
                job = normalize_posting(row, board)
                if job: jobs.append(job)
            except (ValueError, TypeError, AttributeError): errors += 1
        offset += len(rows)
        if not rows or offset >= data.get('totalFound', offset) or len(rows) < 100: break
    return jobs, {'source': board['provider'].title()+':'+board['board'],
                  'status': 'partial' if errors else 'ok', 'postings_read': count,
                  'invalid_postings': errors, 'details_read': details,
                  'capped': count >= per_board, 'detail_cap_reached': details >= detail_limit}


def collect_one_board(board, cfg, per_board):
    label = board['provider'].title() + ':' + board['board'] + ('/' + board['site'] if board['provider'] == 'workday' else '')
    jobs, count, invalid = [], 0, 0
    try:
        if board['provider'] == 'workday':
            from workday_source import collect_workday
            return collect_workday(board, cfg, per_board)
        if board['provider'] == 'smartrecruiters':
            return collect_smartrecruiters(board, cfg, per_board)
        url = endpoint(board)
        while count + invalid < per_board:
            data = read_json(url)
            rows = data if board['provider'] == 'lever' else data.get('jobs')
            if not isinstance(rows, list): raise ValueError('Unexpected company board schema')
            if board['provider'] != 'lever':
                from datetime import datetime, timezone
                def published_key(row):
                    if not isinstance(row, dict): return float('-inf')
                    stamp = row.get('publishedAt') or row.get('first_published') or row.get('first_published_at')
                    try:
                        value = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
                        if value.tzinfo is None: value = value.replace(tzinfo=timezone.utc)
                        return value.timestamp()
                    except (ValueError, TypeError): return float('-inf')
                rows = sorted(rows, key=published_key, reverse=True)
            for row in rows[:per_board-count-invalid]:
                try:
                    job = normalize_posting(row, board)
                    if job: jobs.append(job)
                    count += 1
                except (ValueError, TypeError, AttributeError): invalid += 1
            if board['provider'] != 'lever' or len(rows) < 100: break
            url = endpoint(board).replace('skip=0', 'skip=' + str(count+invalid))
        return jobs, {'source': label, 'status': 'partial' if invalid else 'ok',
                      'postings_read': count, 'invalid_postings': invalid, 'capped': count+invalid >= per_board}
    except Exception as exc:
        return jobs, {'source': label, 'status': 'error', 'postings_read': count,
                      'error': type(exc).__name__ + (f' {exc.code}' if isinstance(getattr(exc, 'code', None), int) else '')}


def collect_company_jobs(cfg):
    """Collect configured public feeds with bounded concurrency and isolated failures."""
    per_board = cfg.get('max_company_jobs_per_board', 300)
    workers = cfg.get('company_feed_workers', 1)
    if not isinstance(per_board, int) or not 1 <= per_board <= 1000:
        raise ValueError('max_company_jobs_per_board must be 1-1000')
    if not isinstance(workers, int) or not 1 <= workers <= 8:
        raise ValueError('company_feed_workers must be 1-8')
    boards = list(configured_boards(cfg))
    if workers == 1:
        results = [collect_one_board(board, cfg, per_board) for board in boards]
    else:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(lambda board: collect_one_board(board, cfg, per_board), boards))
    jobs, statuses = [], []
    for postings, status in results:
        jobs.extend(postings); statuses.append(status)
    return jobs, statuses
