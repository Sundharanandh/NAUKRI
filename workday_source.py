"""Read-only Workday public career search; never an application endpoint."""
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from urllib.request import Request, HTTPRedirectHandler, build_opener


def label(board):
    return 'Workday:' + board['board'] + '/' + board['site']


def read_workday(board, path, payload=None):
    host, tenant, site = board['host'], board['board'], board['site']
    base = f'https://{host}/wday/cxs/{tenant}/{site}'
    if payload is not None and path != '/jobs':
        raise ValueError('Only the public jobs search accepts POST')
    if path != '/jobs' and (not path.startswith('/job/') or '..' in path or '?' in path or '#' in path):
        raise ValueError('Invalid public job detail path')
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise ValueError('Unexpected Workday API redirect')
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {'Accept': 'application/json', 'Accept-Language': 'en-US', 'User-Agent': 'PersonalJobDigest/1.0'}
    if data is not None: headers['Content-Type'] = 'application/json'
    with build_opener(NoRedirect()).open(Request(base+path, data=data, headers=headers), timeout=25) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000: raise ValueError('Workday response too large')
        return json.loads(raw.decode('utf-8'))


def relative_days(text):
    text = str(text or '').lower()
    if 'today' in text: return 0
    if 'yesterday' in text: return 1
    found = re.search(r'posted\s+(\d+)(\+)?\s+days?\s+ago', text)
    if found: return int(found.group(1))
    return None


def publication_date(info):
    """Use the posting start date only when the public postedOn label corroborates it."""
    stamp = info.get('startDate')
    days = relative_days(info.get('postedOn'))
    if not stamp or days is None: return None
    try:
        value = datetime.fromisoformat(stamp).date()
        age = (datetime.now(timezone.utc).date() - value).days
        if days >= 30 and age >= 29 or days < 30 and abs(age-days) <= 1:
            return stamp
    except (ValueError, TypeError): pass
    return None


def normalize_workday(info, path, board):
    if info.get('canApply') is False: return None
    country = info.get('country') or {}
    country = country.get('descriptor', '') if isinstance(country, dict) else country
    places = [info.get('location', '')] + (info.get('additionalLocations') or [])
    locations = [{'address': {'addressLocality': place, 'addressCountry': country}} for place in places if isinstance(place, str) and place]
    return {'@type': 'JobPosting', 'url': f"https://{board['host']}/en-US/{board['site']}{path}",
            'title': info.get('title', ''), 'description': info.get('jobDescription', ''),
            'hiringOrganization': {'name': board.get('company') or board['board']},
            'jobLocation': locations, 'datePosted': publication_date(info),
            'validThrough': info.get('endDate'), 'employmentType': info.get('timeType')}


def collect_workday(board, cfg, per_board):
    jobs, rows, offset, errors, details = [], [], 0, 0, 0
    facets = {}
    # This searches the public careers UI for Java; it never authenticates or applies.
    search = {'appliedFacets': facets, 'limit': 20, 'offset': 0, 'searchText': 'Java'}
    first = read_workday(board, '/jobs', search)
    stack = list(first.get('facets') or [])
    while stack:
        facet = stack.pop()
        if not isinstance(facet, dict): continue
        values = facet.get('values') or []
        if facet.get('facetParameter') == 'locationCountry':
            india = [v['id'] for v in values if isinstance(v, dict) and str(v.get('descriptor', '')).lower() == 'india']
            if india: facets['locationCountry'] = india
        stack.extend(v for v in values if isinstance(v, dict))
    data = read_workday(board, '/jobs', search) if facets else first
    total = data.get('total', 0)
    while len(rows) < per_board:
        page = data.get('jobPostings')
        if not isinstance(page, list): raise ValueError('Unexpected Workday search schema')
        rows.extend(page[:per_board-len(rows)])
        offset += len(page)
        if not page or (total > 0 and offset >= total) or len(rows) >= per_board: break
        search['offset'] = offset
        data = read_workday(board, '/jobs', search)
    # Search ordering is relevance-based. Inspect recent metadata before spending detail requests.
    rows.sort(key=lambda r: relative_days(r.get('postedOn')) if relative_days(r.get('postedOn')) is not None else 99999)
    cap = cfg.get('max_company_details_per_board', 30)
    for row in rows:
        title, location = row.get('title', ''), row.get('locationsText', '')
        age = relative_days(row.get('postedOn'))
        if age is not None and age > cfg.get('max_age_days', 7): continue
        if not re.search(r'\b(developer|engineer|engineering)\b', title, re.I): continue
        if re.search(r'\b(senior|sr\.?|lead|staff|principal|architect|manager|qa|sdet|support|devops)\b', title, re.I): continue
        # Ambiguous multi-location summaries require details to confirm a city.
        if not any(re.search(r'\b'+re.escape(city)+r'\b', location, re.I) for city in cfg.get('cities', [])) and not re.search(r'\d+\s+locations', location, re.I): continue
        path = row.get('externalPath', '')
        if not path.startswith('/job/') or '..' in path or '?' in path or '#' in path: errors += 1; continue
        if details >= cap: break
        details += 1
        try:
            detail = read_workday(board, path)
            info = detail.get('jobPostingInfo')
            if not isinstance(info, dict): raise ValueError('Missing jobPostingInfo')
            job = normalize_workday(info, path, board)
            if job: jobs.append(job)
        except Exception: errors += 1
    return jobs, {'source': label(board), 'status': 'partial' if errors else 'ok',
                  'postings_read': len(rows), 'details_read': details, 'invalid_postings': errors,
                  'capped': len(rows) >= per_board, 'detail_cap_reached': details >= cap,
                  'india_filter_applied': bool(facets)}
