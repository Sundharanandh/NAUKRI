import json
import os
import tempfile
import sqlite3
import unittest
from argparse import Namespace
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError

import digest_agent as agent
from digest_reporting import summary_lines, run_context
from job_identity import fingerprint_for_url
import public_http as http


class IdentityTests(unittest.TestCase):
    def test_distinct_workday_requisitions_and_aliases(self):
        base = 'https://kyndryl.wd5.myworkdayjobs.com/'
        a = base + 'en-US/External/job/Bangalore/Same-Title_R-71815'
        alias = base + 'en-US/OtherBoard/job/Pune/Changed-Title_R-71815'
        b = a.replace('71815', '71817')
        self.assertEqual(fingerprint_for_url(a), fingerprint_for_url(alias))
        self.assertNotEqual(fingerprint_for_url(a), fingerprint_for_url(b))

    def test_fresh_excluded_feed_is_rechecked_and_distinct_ids_survive(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(agent, 'ROOT', Path(directory)):
            cfg = agent.DEFAULT | {'gmail': 'test@example.invalid', 'recipient': 'test@example.invalid',
                                   'include_review_candidates': True, 'max_review_per_email': 5, 'email_alerts_enabled': False}
            job = {'url': 'https://example.wd5.myworkdayjobs.com/en-US/External/job/Bangalore/Engineer_R-1',
                   'title': 'Software Engineer', 'description': 'Python. 4-6 years experience.',
                   'datePosted': datetime.now(timezone.utc).isoformat(), 'hiringOrganization': {'name': 'Example'},
                   'jobLocation': {'address': {'addressLocality': 'Bengaluru'}}}
            feed = [job]
            with patch.object(agent, 'load_config', return_value=cfg), patch.object(agent, 'seed_legacy_history'), \
                 patch.object(agent, 'collect_company_jobs', side_effect=lambda *a, **k: (list(feed), [])), \
                 patch.object(agent, 'read_alert_urls', return_value=[]), patch.object(agent, 'checkpoint'):
                agent.run(Namespace(preview=True, input=None))
                job['description'] = 'Java Spring Boot development'
                feed.append(job | {'url': job['url'].replace('R-1', 'R-2')})
                agent.run(Namespace(preview=True, input=None))
                summary = json.loads((Path(directory)/'run_summary.json').read_text())
                self.assertEqual(summary['new_review_candidates_for_email'], 2)
                self.assertEqual(summary['current_run']['checked'], 2)
                # Migrate old fuzzy fingerprints without repeating an already emailed requisition alias.
                with closing(sqlite3.connect(Path(directory)/'jobs.sqlite3')) as db:
                    db.execute("UPDATE jobs SET mailed='previous send', fingerprint='old fuzzy hash' WHERE url=?", (job['url'],))
                    db.commit()
                alias = job | {'url': job['url'].replace('/External/', '/OtherBoard/')}
                feed[:] = [alias, feed[1]]
                agent.run(Namespace(preview=True, input=None))
                summary = json.loads((Path(directory)/'run_summary.json').read_text())
                self.assertEqual(summary['new_review_candidates_for_email'], 1)



class PublicHttpTests(unittest.TestCase):
    def response(self, raw):
        value = MagicMock()
        value.__enter__.return_value.read.return_value = raw
        return value

    def test_timeout_retried_then_success(self):
        opener = MagicMock()
        opener.open.side_effect = [TimeoutError(), self.response(b'{"jobs":[]}')]
        with patch.object(http, 'build_opener', return_value=opener), patch.object(http.time, 'sleep') as sleep:
            self.assertEqual(http.fetch_json('https://example.invalid/jobs'), {'jobs': []})
        self.assertEqual(opener.open.call_count, 2)
        sleep.assert_called_once()

    def test_final_http_code_and_invalid_json_are_clear(self):
        opener = MagicMock()
        opener.open.side_effect = HTTPError('https://example.invalid', 503, 'unavailable', {}, None)
        with patch.object(http, 'build_opener', return_value=opener), patch.object(http.time, 'sleep'):
            with self.assertRaises(http.PublicSourceError) as caught:
                http.fetch_json('https://example.invalid')
        self.assertEqual(opener.open.call_count, 3)
        self.assertEqual(http.error_info(caught.exception)['http_status'], 503)
        opener.open.side_effect = None
        opener.open.return_value = self.response(b'<html>private response</html>')
        with patch.object(http, 'build_opener', return_value=opener):
            with self.assertRaises(http.PublicSourceError) as caught:
                http.fetch_json('https://example.invalid')
        self.assertEqual(caught.exception.code, 'invalid_json')
        self.assertNotIn('private response', str(caught.exception))

    def test_redirect_cannot_change_search_method_or_origin(self):
        def opener(handler):
            from urllib.request import Request
            request = Request('https://example.invalid/jobs', data=b'{}')
            with self.assertRaises(http.PublicSourceError):
                handler.redirect_request(request, None, 302, '', {}, 'https://example.invalid/login')
            with self.assertRaises(http.PublicSourceError):
                handler.redirect_request(request, None, 307, '', {}, 'https://other.invalid/jobs')
            result = MagicMock()
            result.open.return_value = self.response(b'{}')
            return result
        with patch.object(http, 'build_opener', side_effect=opener):
            http.fetch_json('https://example.invalid/jobs', {})


class ReportingTests(unittest.TestCase):
    def test_slot_ist_and_count_meaning(self):
        with patch.dict(os.environ, {'GITHUB_EVENT_NAME': 'schedule', 'DIGEST_SCHEDULE_SLOT': '30 13 * * *',
                                     'DIGEST_RUN_STARTED_UTC': '2026-11-15T13:35:00Z'}):
            context = run_context(datetime.now(timezone.utc))
        lines = '\n'.join(summary_lines({'run_context': context, 'current_run': {'unique_collected': 3671},
                                        'source_status': [{'status': 'ok'}]}))
        self.assertIn('19:00 IST', lines)
        self.assertIn('07:05 PM IST', lines)
        self.assertIn('3,671', lines)
        self.assertIn('does not mean zero jobs', lines)
        self.assertIn('No job records were collected', '\n'.join(summary_lines({})))
