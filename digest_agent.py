"""Review-only job digests with persistent history and an Excel workbook."""
import argparse, email, hashlib, html, imaplib, json, logging
import os, re, smtplib, sqlite3, ssl, sys, subprocess
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from urllib.request import Request
from job_sources import (PLATFORMS, allowed_alert_url, collect_company_jobs, configured_boards, source_name)

CODE_DIR = Path(__file__).resolve().parent
ROOT = Path(os.environ.get('JOB_MAILER_STATE_DIR', str(CODE_DIR / 'local_state')))
UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT = {'gmail': '', 'recipient': '',
 'mailbox': 'JobAlerts', 'experience': 2.5, 'max_age_days': 7,
 'cities': ['Bengaluru','Bangalore','Chennai','Hyderabad','Secunderabad','Mumbai'],
 'required_skills': {'Java': r'\bjava\b', 'Spring Boot': r'\bspring\s*boot\b',
 }, 'preferred_skills': {'Microservices': r'\bmicroservices?\b', 'Angular': r'\bangular\b',
 'AWS': r'\baws\b|amazon web services'}, 'max_per_email': 10,
 'max_messages': 150, 'max_pages_per_run': 75,
 'company_domains': [], 'company_boards': [], 'max_company_jobs_per_board': 300,
 'email_alerts_enabled': True, 'enabled_platforms': list(PLATFORMS), 'expected_lpa': 10}

class Page(HTMLParser):
    def __init__(self):
        super().__init__(); self.links=[]; self.scripts=[]; self.capture=False; self.buf=[]; self.words=[]
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag=='a' and a.get('href'): self.links.append(html.unescape(a['href']))
        if tag=='script' and a.get('type','').lower()=='application/ld+json': self.capture=True; self.buf=[]
    def handle_data(self, data):
        if self.capture: self.buf.append(data)
        else: self.words.append(data)
    def handle_endtag(self, tag):
        if tag=='script' and self.capture:
            self.scripts.append(''.join(self.buf)); self.capture=False

def plain(s):
    p=Page(); p.feed(s or ''); return re.sub(r'\s+', ' ', ' '.join(p.words)).strip()

def canonical(url):
    p=urlparse(html.unescape(url))
    host=(p.hostname or '').lower()
    if p.scheme!='https' or not host or p.username or p.password: return None
    if p.port not in (None,443): return None
    q=parse_qs(p.query)
    path = p.path.rstrip('/')
    if host in ('boards.greenhouse.io', 'job-boards.greenhouse.io'):
        host = 'job-boards.greenhouse.io'
    if host in ('boards.eu.greenhouse.io', 'job-boards.eu.greenhouse.io'):
        host = 'job-boards.eu.greenhouse.io'
    for domain, official in [('foundit.in', 'www.foundit.in'), ('instahyre.com', 'www.instahyre.com'), ('hirist.tech', 'www.hirist.tech'), ('cutshort.io', 'cutshort.io'), ('wellfound.com', 'wellfound.com')]:
        if host in (domain, 'www.' + domain): host = official
    if host == 'linkedin.com' or host.endswith('.linkedin.com'):
        identity = re.search(r'/jobs/view/(?:[^/]*-)?(\d+)(?:/|$)', path)
        if identity:
            host, path = 'www.linkedin.com', '/jobs/view/' + identity.group(1)
    if (host == 'indeed.com' or host.endswith('.indeed.com') or host == 'indeed.co.in' or host.endswith('.indeed.co.in')) and ('jk' in q or 'vjk' in q):
        path = '/viewjob'
    keep={k:q[k][0] for k in ('jk','vjk','jobId','jobid','gh_jid','id') if k in q}
    if 'vjk' in keep and 'jk' not in keep: keep['jk']=keep.pop('vjk')
    return urlunparse(('https',host,path,'',urlencode(keep),''))

def allowed(url,cfg):
    return allowed_alert_url(url, cfg)

def jobs_in(value):
    if isinstance(value,list):
        for x in value: yield from jobs_in(x)
    elif isinstance(value,dict):
        kind=value.get('@type',[])
        if kind=='JobPosting' or isinstance(kind,list) and 'JobPosting' in kind: yield value
        for v in value.values():
            if isinstance(v,(dict,list)): yield from jobs_in(v)

def fetch_job(url):
    # Follow redirects only within the same hostname. Never submit forms or authenticate.
    import urllib.request
    host=urlparse(url).hostname
    class SameHost(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,req,fp,code,msg,headers,newurl):
            if urlparse(newurl).hostname!=host or urlparse(newurl).scheme!='https':
                raise ValueError('Cross-host redirect requires review')
            return super().redirect_request(req,fp,code,msg,headers,newurl)
    opener=urllib.request.build_opener(SameHost())
    req=Request(url,headers={'User-Agent':'PersonalJobAlertReader/1.0','Accept':'text/html'})
    with opener.open(req,timeout=20) as r:
        data=r.read(2_000_001)
        if len(data)>2_000_000: raise ValueError('Page too large')
        if 'html' not in r.headers.get('Content-Type',''): raise ValueError('Not an HTML page')
        body=data.decode('utf-8',errors='replace')
    p=Page(); p.feed(body)
    found=[]
    for script in p.scripts:
        try: found.extend(jobs_in(json.loads(script)))
        except (ValueError,TypeError): continue
    # A multi-job page is ambiguous: never treat it as one particular job.
    unique={json.dumps(j,sort_keys=True):j for j in found}
    if len(unique)!=1: raise ValueError('No unambiguous structured JobPosting; manual review required')
    return next(iter(unique.values()))

def dateparse(s):
    if not s: return None
    try:
        d=datetime.fromisoformat(str(s).replace('Z','+00:00'))
        return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)
    except ValueError: return None

def company(j):
    x=j.get('hiringOrganization',{})
    return x.get('name','') if isinstance(x,dict) else str(x)

def locations(j):
    values=j.get('jobLocation',[])
    if isinstance(values,dict): values=[values]
    parts=[]
    for x in values:
        if not isinstance(x,dict): continue
        a=x.get('address',{})
        if isinstance(a,str): parts.append(a)
        elif isinstance(a,dict): parts.extend(str(a.get(k,'')) for k in ['addressLocality','addressRegion','addressCountry'])
    return ', '.join(p for p in parts if p)

def classify(j,cfg,now):
    title=plain(j.get('title','')); desc=plain(j.get('description','')); loc=locations(j)
    posted=dateparse(j.get('datePosted')); expires=dateparse(j.get('validThrough'))
    review=[]; rejected=[]
    if not re.search(r'\b(developer|engineer|engineering)\b', title, re.I): rejected.append('Not a developer/engineer role')
    if re.search(r'\b(qa|sdet|support|trainer|recruiter|sales|intern(?:ship)?|devops|administrator|test(?:er|ing)?|data\s+engineer)\b',title,re.I): rejected.append('Role outside Java development target')
    if re.search(r'\b(senior|sr\.?|lead|staff|principal|architect|manager)\b',title,re.I): rejected.append('Seniority outside target')
    for name,pattern in cfg['required_skills'].items():
        if not re.search(pattern,desc+' '+title,re.I): review.append('Skill not confirmed: '+name)
    remote_india = cfg.get('allow_remote_india', True) and bool(re.search(r'\bremote\b', loc, re.I) and re.search(r'\bindia\b', loc, re.I))
    if not remote_india and not any(re.search(r'\b'+re.escape(c)+r'\b',loc,re.I) for c in cfg['cities']): review.append('Preferred city not confirmed; remote eligibility requires review')
    entries = j.get('jobLocation', [])
    entries = [entries] if isinstance(entries, dict) else entries
    countries = []
    for entry in entries:
        address = entry.get('address', {}) if isinstance(entry, dict) else {}
        country = address.get('addressCountry', '') if isinstance(address, dict) else ''
        if isinstance(country, dict): country = country.get('name', country.get('identifier', ''))
        if country: countries.append(str(country).strip().lower())
    if countries and not any(c in ('in', 'ind', 'india', 'bharat') for c in countries):
        rejected.append('Explicit job country is outside India')
    if not posted: review.append('Posting date unavailable')
    elif posted<now-timedelta(days=cfg['max_age_days']) or posted>now+timedelta(days=1): rejected.append('Posting outside recent window')
    if expires and expires<now: rejected.append('Posting expired')
    # Conservative explicit year ranges. Single numbers, months and conflicting ranges need review.
    exp=plain(str(j.get('experienceRequirements','')))+' '+desc
    ranges=re.findall(r'(?<![\d.])(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)\s*(?:years?|yrs?|year\(s\))\b',exp,re.I)
    minimums = re.findall(r'(?<![\d.])(\d+(?:\.\d+)?)\s*\+\s*(?:years?|yrs?|year\(s\))\s+(?:of\s+)?(?:relevant\s+|professional\s+|hands-on\s+)?experience', exp, re.I)
    minimums += re.findall(r'\b(?:at least|minimum(?: of)?|a minimum of)\s+(\d+(?:\.\d+)?)\s*(?:years?|yrs?|year\(s\))', exp, re.I)
    structured = j.get('experienceRequirements')
    if isinstance(structured, dict) and isinstance(structured.get('monthsOfExperience'), (int, float)):
        minimums.append(str(structured['monthsOfExperience'] / 12))
    eligible_range = ranges and all(float(a)<=cfg['experience']<=float(b) for a,b in ranges)
    eligible_minimum = minimums and all(float(value)<=cfg['experience'] for value in minimums)
    if ranges and not eligible_range: rejected.append('Experience range excludes 2.5 years')
    elif minimums and not eligible_minimum: rejected.append('Minimum experience exceeds 2.5 years')
    elif not eligible_range and not eligible_minimum: review.append('2.5-year eligibility not unambiguously confirmed')
    if not company(j): review.append('Company unavailable')
    salary=j.get('baseSalary')
    salary_note=json.dumps(salary,ensure_ascii=False) if salary else f"Not disclosed; INR {cfg['expected_lpa']} LPA negotiable, review before applying"
    # Salary is a negotiable preference, not a hard rejection threshold.
    preferences = [name for name, pattern in cfg.get('preferred_skills', {}).items() if re.search(pattern, desc+' '+title, re.I)]
    notes = rejected + review
    if not notes: notes = ['Required skills confirmed; preferences found: ' + (', '.join(preferences) or 'none specified')]
    return ('Excluded' if rejected else 'Review' if review else 'Matched'), '; '.join(notes), title,loc,posted,salary_note

def read_alert_urls(cfg,password):
    urls=set(); since=(datetime.now(UTC)-timedelta(days=cfg['max_age_days'])).strftime('%d-%b-%Y')
    with imaplib.IMAP4_SSL('imap.gmail.com', timeout=30) as m:
        m.login(cfg['gmail'],password)
        status,_=m.select('"'+cfg['mailbox'].replace('"','')+'"',readonly=True)
        if status!='OK': raise RuntimeError('Create the Gmail label JobAlerts and route job alerts into it')
        status,data=m.search(None,'SINCE',since)
        if status!='OK': raise RuntimeError('Gmail search failed')
        for msgid in data[0].split()[-cfg['max_messages']:]:
            status,parts=m.fetch(msgid,'(BODY.PEEK[])')
            if status!='OK': continue
            raw=next((x[1] for x in parts if isinstance(x,tuple)),None)
            if not raw: continue
            msg=email.message_from_bytes(raw)
            for part in msg.walk():
                if part.get_content_type() not in ('text/html','text/plain'): continue
                payload=part.get_payload(decode=True)
                if not payload: continue
                text=payload.decode(part.get_content_charset() or 'utf-8',errors='replace')
                p=Page(); p.feed(text)
                candidates=p.links+re.findall(r'https://[^\s<>"\x27]+',text)
                for candidate in candidates:
                    # Unwrap only known URL parameters; do not follow arbitrary redirect links.
                    q=parse_qs(urlparse(html.unescape(candidate)).query)
                    candidates2=[candidate]+[v[0] for k,v in q.items() if k.lower() in ('url','dest','destination','redirect')]
                    for u in candidates2:
                        try:
                            u=canonical(u)
                            if u and allowed(u,cfg): urls.add(u)
                        except ValueError: continue
    return sorted(urls)

def connect():
    db=sqlite3.connect(ROOT/'jobs.sqlite3')
    db.execute('CREATE TABLE IF NOT EXISTS jobs(url TEXT PRIMARY KEY, title TEXT, company TEXT, location TEXT, posted TEXT, state TEXT, reason TEXT, salary TEXT, first_seen TEXT, mailed TEXT, fingerprint TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY, created TEXT, state TEXT, urls TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS checks(url TEXT PRIMARY KEY, checked TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)')
    return db

def export(db,cfg=None):
    from openpyxl import Workbook,load_workbook
    from openpyxl.styles import Font
    path=ROOT/'Job_Tracker.xlsx'; notes={}
    if path.exists():
        old=load_workbook(path)
        for ws in old:
            if ws.title not in ('Matches','Needs review','Excluded'): continue
            for row in ws.iter_rows(min_row=2,values_only=True):
                if row[0]: notes[row[0]]=list(row[-3:])
        old.close()
    wb=Workbook(); wb.remove(wb.active)
    for name,states in [('Matches',('Matched',)),('Needs review',('Review',)),('Excluded',('Excluded',))]:
        ws=wb.create_sheet(name); ws.append(['Job URL','Title','Company','Location','Posted UTC','Matching result','Reason','Salary information','First seen UTC','Email attempt / sent UTC','Application status','Applied date','My notes'])
        for row in db.execute('SELECT url,title,company,location,posted,state,reason,salary,first_seen,mailed FROM jobs WHERE state IN ('+','.join('?'*len(states))+') ORDER BY posted DESC,first_seen DESC',states):
            values=list(row)+notes.get(row[0],['Not applied','',''])
            # Prevent spreadsheet formula injection from external job descriptions.
            ws.append(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v for v in values])
        ws.freeze_panes='A2'; ws.auto_filter.ref=ws.dimensions
        for c in ws[1]: c.font=Font(bold=True)
        for col in 'ABCDEFGHIJKLM': ws.column_dimensions[col].width=25
        ws.column_dimensions['A'].width=65; ws.column_dimensions['G'].width=65
    ws=wb.create_sheet('Delivery log'); ws.append(['Digest ID','Created UTC','Delivery status','Job URLs'])
    for row in db.execute('SELECT * FROM deliveries ORDER BY created DESC'): ws.append(list(row))
    status_path = ROOT / 'run_summary.json'
    if status_path.exists():
        summary = json.loads(status_path.read_text(encoding='utf-8'))
        sheet = wb.create_sheet('Source status')
        sheet.append(['Source', 'Status', 'Postings / links read', 'Invalid postings', 'Error type', 'Capped'])
        for row in summary.get('source_status', []):
            sheet.append([row.get('source'), row.get('status'), row.get('postings_read', 0), row.get('invalid_postings', 0), row.get('error', ''), row.get('capped', False)])
        sheet.freeze_panes = 'A2'
        for column in 'ABCDEF': sheet.column_dimensions[column].width = 28
    if cfg and cfg.get('company_catalog_file'):
        catalog_path = (CODE_DIR / cfg['company_catalog_file']).resolve()
        if CODE_DIR.resolve() not in catalog_path.parents: raise ValueError('Company catalog must be inside the repository')
        catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
        enabled = {(b['provider'], b['board'].casefold()): b for b in configured_boards(cfg)}
        sheet = wb.create_sheet('Company coverage')
        sheet.append(['Company', 'Careers website', 'Collection', 'Provider', 'Board', 'Details'])
        for item in catalog['companies']:
            feed = item.get('feed') or {}
            active = (feed.get('provider'), str(feed.get('board', '')).casefold()) in enabled
            state = 'Monitored via public feed' if active else 'Not monitored automatically'
            feeds = ([feed] if feed else []) + item.get('additional_feeds', [])
            boards = '; '.join(str(b.get('board', '')) + ('/' + b['site'] if b.get('site') else '') for b in feeds)
            sheet.append([item['company'], item.get('careers_url'), state, feed.get('provider'), boards, item.get('reason', '')])
        sheet.freeze_panes = 'A2'; sheet.auto_filter.ref = sheet.dimensions
        for column in 'ABCDEF': sheet.column_dimensions[column].width = 35
        sheet.column_dimensions['B'].width = 70; sheet.column_dimensions['F'].width = 90
    temp=ROOT/'Job_Tracker.pending.xlsx'; wb.save(temp)
    try: os.replace(temp,path)
    except PermissionError: logging.warning('Close Excel. Updated copy saved as Job_Tracker.pending.xlsx; next run will retry.')

def digest(jobs,cfg,source_status=None,review_jobs=None,review_reasons=None):
    review_jobs = review_jobs or []
    review_reasons = review_reasons or {}
    msg=EmailMessage(); msg['From']=cfg['gmail']; msg['To']=cfg['recipient']
    msg['Subject']=f'Java jobs: {len(jobs)} new matches — '+datetime.now(IST).strftime('%d %b %Y %I:%M %p IST')
    if review_jobs:
        msg.replace_header('Subject', f'Java jobs: {len(jobs)} confirmed, {len(review_jobs)} to review — ' + datetime.now(IST).strftime('%d %b %Y %I:%M %p IST'))
    lines=['New, previously unemailed jobs, newest posting first.','Automated text checks only: review the complete posting before applying.','Salary is negotiable and not a hard filter. No applications were submitted.','']
    for url,title,org,loc,posted,salary in jobs:
        lines.extend([f'{title} | {org}',f'Source: {source_name(url, cfg)}',f'{loc} | Posted: {posted}',f'Salary: {salary}',url,''])
    if not jobs: lines.append('No new confirmed matches this run. Check the Excel Needs review sheet. Older links will not be repeated.')
    if review_jobs:
        lines += ['', 'NEEDS REVIEW — these are not confirmed matches.', 'Java/Spring Boot and a preferred city were found. Check the missing date or experience details before applying.', '']
        for url,title,org,loc,posted,salary in review_jobs:
            lines.extend([f'{title} | {org}', f'{loc} | Posted: {posted or "Not confirmed"}', 'Check: ' + review_reasons.get(url, 'Eligibility requires review'), url, ''])
    failures = [item for item in (source_status or []) if item['status'] != 'ok']
    if failures:
        lines.append('Some sources were incomplete or unavailable. See the Excel Source status sheet:')
        lines.extend(item['source'] + ': ' + item['status'] + (' (' + item['error'] + ')' if item.get('error') else '') for item in failures)
    lines.append('The current Excel tracker is attached. Keep your editable local workbook for application notes.')
    msg.set_content('\n'.join(lines))
    return msg

def load_config():
    config_path = Path(os.environ.get('JOB_MAILER_CONFIG', str(CODE_DIR / 'digest_config.json')))
    cfg = json.loads(config_path.read_text(encoding='utf-8'))
    cfg['gmail'] = os.environ.get('NOTIFY_EMAIL', cfg.get('gmail', '')).strip()
    cfg['recipient'] = os.environ.get('DIGEST_EMAIL_TO', cfg.get('recipient', '')).strip() or cfg['gmail']
    cfg['mailbox'] = os.environ.get('JOB_ALERT_LABEL', cfg.get('mailbox', 'JobAlerts'))
    if not cfg['gmail'] or not cfg['recipient']:
        raise RuntimeError('Set NOTIFY_EMAIL and optionally DIGEST_EMAIL_TO in Actions secrets/variables.')
    review_cap = cfg.get('max_review_per_email', 5)
    if not isinstance(review_cap, int) or not 0 <= review_cap <= 50: raise ValueError('max_review_per_email must be 0-50')
    list(configured_boards(cfg))  # Reject invalid tenant configuration before reading mail.
    unknown = set(cfg.get('enabled_platforms', PLATFORMS)) - set(PLATFORMS)
    if unknown: raise ValueError('Unknown enabled_platforms name')
    if not 1 <= cfg['max_per_email'] <= 50:
        raise ValueError('max_per_email must be 1-50')
    return cfg


def checkpoint():
    # GitHub runners are ephemeral. Push the email reservation BEFORE attempting SMTP.
    # A cancelled run after SMTP acceptance must not email the same links next time.
    if os.environ.get('JOB_MAILER_CHECKPOINT') == '1':
        subprocess.run([sys.executable, str(CODE_DIR / 'persist_digest_state.py'), 'save'], check=True)


def seed_legacy_history(db):
    legacy = CODE_DIR / 'docs' / 'jobs_data.json'
    if not legacy.exists():
        return
    data = json.loads(legacy.read_text(encoding='utf-8'))
    for row in data.get('applied_jobs', []) + data.get('manual_jobs', []):
        url = canonical(row.get('url', ''))
        if not url:
            continue
        db.execute('INSERT OR IGNORE INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   (url, row.get('title', ''), row.get('company', ''), row.get('location', ''),
                    '', 'Excluded', 'Already present in legacy application/dashboard history',
                    row.get('salary', ''), '', 'Legacy history', 'legacy:' + url))
    db.commit()


def run(args):
    ROOT.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    password = os.environ.get('GMAIL_APP_PASSWORD', '').replace(' ', '')
    if not password and not getattr(args, 'input', None) and (not args.preview or cfg.get('email_alerts_enabled', True)):
        raise RuntimeError('Set GMAIL_APP_PASSWORD as a GitHub Actions secret, never in source code.')
    from contextlib import closing
    with closing(connect()) as db:
        _run_with_db(args, cfg, password, db)


def _run_with_db(args, cfg, password, db):
    seed_legacy_history(db); now=datetime.now(UTC); fetched=0; page_requests=0
    input_jobs = json.loads(Path(args.input).read_text(encoding='utf-8')) if getattr(args, 'input', None) else None
    policy = {key: cfg.get(key) for key in ('required_skills', 'preferred_skills', 'experience', 'cities', 'max_age_days', 'allow_remote_india')}
    policy['version'] = 3
    policy_key = hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()
    prior_policy = db.execute("SELECT value FROM settings WHERE key='matching_policy'").fetchone()
    reassess_excluded = not prior_policy or prior_policy[0] != policy_key
    db.execute("INSERT INTO settings VALUES('matching_policy', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (policy_key,))
    db.commit()
    source_status = []
    if input_jobs is not None:
        by_url = {canonical(j['url']): j for j in input_jobs}
        if None in by_url: raise ValueError('Input contains an invalid HTTPS job URL')
        alert_urls = list(by_url)
        email_urls = []
    else:
        board_jobs, source_status = collect_company_jobs(cfg)
        by_url = {canonical(j['url']): j for j in board_jobs}
        email_urls = []
        if cfg.get('email_alerts_enabled', True):
            try:
                email_urls = read_alert_urls(cfg, password)
                source_status.append({'source': 'Gmail alerts', 'status': 'ok', 'postings_read': len(email_urls)})
            except Exception as exc:
                source_status.append({'source': 'Gmail alerts', 'status': 'error', 'error': type(exc).__name__})
        alert_urls = sorted(set(email_urls) | set(by_url))
    checked=dict(db.execute('SELECT url,checked FROM checks'))
    def candidate_priority(url):
        posted = dateparse(by_url.get(url, {}).get('datePosted'))
        return (checked.get(url, ''), -posted.timestamp() if posted else float('inf'))
    alert_urls.sort(key=candidate_priority)
    refreshed=set()
    for url in alert_urls:
        old=db.execute('SELECT state,mailed FROM jobs WHERE url=?',(url,)).fetchone()
        if old and (old[1] or old[0]=='Excluded' and not reassess_excluded): continue
        if url not in by_url:
            if page_requests >= cfg['max_pages_per_run']: continue
            page_requests += 1
        fetched+=1
        refreshed.add(url)
        db.execute('INSERT INTO checks VALUES(?,?) ON CONFLICT(url) DO UPDATE SET checked=excluded.checked',(url,now.isoformat()))
        try:
            j=by_url[url] if url in by_url else fetch_job(url); state,reason,title,loc,posted,salary=classify(j,cfg,now); org=company(j)
            stamp=posted.isoformat() if posted else ''
            fp=hashlib.sha256('|'.join([org.casefold(),title.casefold(),loc.casefold(),stamp[:10]]).encode()).hexdigest() if org and title else url
        except Exception as e:
            state,reason,title,loc,stamp,salary,org,fp='Review',type(e).__name__+': public page unavailable or structured details missing','','','','','',url
        db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET title=excluded.title,company=excluded.company,location=excluded.location,posted=excluded.posted,state=excluded.state,reason=excluded.reason,salary=excluded.salary,fingerprint=excluded.fingerprint', (url,title,org,loc,stamp,state,reason,salary,now.isoformat(),'',fp)); db.commit()
    # Refresh age at sending time, including cached postings.
    rows=db.execute("SELECT url,title,company,location,posted,salary,fingerprint FROM jobs WHERE state='Matched' AND mailed='' ORDER BY posted DESC").fetchall()
    jobs=[]; fingerprints={r[0] for r in db.execute("SELECT fingerprint FROM jobs WHERE mailed<>''")}
    for row in rows:
        if row[0] not in refreshed: continue  # Revalidate expiry and page availability on this run.
        posted=dateparse(row[4])
        if not posted or posted<now-timedelta(days=cfg['max_age_days']): continue
        if row[6] in fingerprints: continue
        fingerprints.add(row[6]); jobs.append(row[:6])
        if len(jobs)>=cfg['max_per_email']: break
    review_jobs, review_reasons = [], {}
    if cfg.get('include_review_candidates', False):
        cap = min(cfg.get('max_review_per_email', 5), max(0, cfg['max_per_email']-len(jobs)))
        candidates = db.execute("SELECT url,title,company,location,posted,salary,fingerprint,reason FROM jobs WHERE state='Review' AND mailed='' AND company NOT IN ('', 'Unknown', 'None') AND reason NOT LIKE '%Skill not confirmed%' AND reason NOT LIKE '%Preferred city%' ORDER BY posted DESC").fetchall()
        for row in candidates:
            if len(review_jobs) >= cap: break
            if row[0] not in refreshed or row[6] in fingerprints: continue
            fingerprints.add(row[6]); review_jobs.append(row[:6]); review_reasons[row[0]] = row[7]
    all_links = jobs + review_jobs
    counts = dict(db.execute('SELECT state,COUNT(*) FROM jobs GROUP BY state'))
    summary = {'run_utc': now.isoformat(), 'alert_links': len(alert_urls),
               'pages_checked': page_requests, 'postings_evaluated': fetched, 'new_matches_for_email': len(jobs),
               'new_review_candidates_for_email': len(review_jobs),
               'company_feeds_configured': len(list(configured_boards(cfg))),
               'tracker_totals': counts, 'preview': bool(args.preview),
               'email_alert_links': len(email_urls), 'company_board_jobs': len(by_url) if input_jobs is None else 0,
               'links_by_source': {name: sum(source_name(url, cfg) == name for url in alert_urls) for name in sorted({source_name(url, cfg) for url in alert_urls})},
               'source_status': source_status}
    (ROOT / 'run_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary))
    export(db,cfg); msg=digest(jobs,cfg,source_status,review_jobs,review_reasons)
    if args.preview:
        (ROOT/'Email_Preview.txt').write_text(msg.get_content(),encoding='utf-8'); print('Preview and Excel updated. No email sent; no jobs marked emailed.'); return
    path=ROOT/'Job_Tracker.pending.xlsx'
    if not path.exists(): path=ROOT/'Job_Tracker.xlsx'
    msg.add_attachment(path.read_bytes(),maintype='application',subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet',filename='Job_Tracker.xlsx')
    # Authenticate before recording delivery. Hold attempted links to prevent automatic repeats
    # if the connection drops after Gmail accepted a message. Check Delivery log for ambiguity.
    with smtplib.SMTP_SSL('smtp.gmail.com',465,context=ssl.create_default_context(),timeout=30) as smtp:
        smtp.login(cfg['gmail'],password)
        ident=hashlib.sha256((now.isoformat()+repr(all_links)).encode()).hexdigest()[:16]
        msg['Message-ID']=f'<{ident}@personal-job-mailer.local>'
        db.execute('INSERT INTO deliveries VALUES(?,?,?,?)',(ident,now.isoformat(),'Attempting',json.dumps([j[0] for j in all_links])))
        for job in all_links: db.execute('UPDATE jobs SET mailed=? WHERE url=?',(now.isoformat(),job[0]))
        db.commit()
        export(db,cfg)
        checkpoint()
        try:
            refused=smtp.send_message(msg)
            if refused: raise RuntimeError('Recipient refused')
        except Exception:
            db.execute('UPDATE deliveries SET state=? WHERE id=?',('Uncertain — check Gmail Sent before retrying',ident)); db.commit(); export(db,cfg); raise RuntimeError('Delivery uncertain. Attempted links held to prevent duplicates; check Gmail Sent and Excel Delivery log.') from None
        db.execute('UPDATE deliveries SET state=? WHERE id=?',('Accepted by Gmail SMTP',ident)); db.commit()
    export(db,cfg); checkpoint(); print(f'Gmail accepted digest with {len(jobs)} confirmed and {len(review_jobs)} review jobs. Excel updated.')

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--preview',action='store_true'); parser.add_argument('--input',help='Optional structured JobPosting JSON file for offline preview'); args=parser.parse_args()
    if args.input and not args.preview: parser.error('--input is only supported with --preview')
    ROOT.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=ROOT/'job_mailer.log',level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    # OS lock is released if the process exits; concurrent Task Scheduler/manual runs cannot overlap.
    lock=open(ROOT/'run.lock','a+b')
    try:
        if sys.platform=='win32':
            import msvcrt
            lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0); msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        print('Another run is active. Exiting.'); sys.exit(0)
    try: run(args)
    except Exception as e:
        # Log exception type only: URLs and server responses can contain private information.
        logging.error('Run failed: %s',type(e).__name__); print('Run failed:',type(e).__name__,str(e) if isinstance(e,(ValueError,RuntimeError)) else 'Check Actions secrets, JobAlerts label, network access and workbook permissions.'); sys.exit(1)
