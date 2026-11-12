import json
import os
import sqlite3
import tempfile
import unittest
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import digest_agent as agent
import job_sources as sources


class UserPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 12, tzinfo=timezone.utc)
        self.job = {'title': 'Software Engineer', 'description': 'Java and Spring Boot. 2-4 years experience.',
                    'hiringOrganization': {'name': 'Example'}, 'datePosted': '2026-11-11T12:00:00Z',
                    'jobLocation': {'address': {'addressLocality': 'Bengaluru', 'addressCountry': 'IN'}}}

    def test_generic_software_title_can_match_java_description(self):
        self.assertEqual(agent.classify(self.job, agent.DEFAULT, self.now)[0], 'Matched')

    def test_optional_skills_do_not_exclude_backend_job(self):
        state, reason, *_ = agent.classify(self.job, agent.DEFAULT, self.now)
        self.assertEqual(state, 'Matched')
        self.assertIn('preferences found', reason)

    def test_spring_boot_still_required(self):
        self.job['description'] = 'Java. 2-4 years experience.'
        self.assertEqual(agent.classify(self.job, agent.DEFAULT, self.now)[0], 'Review')

    def test_explicit_two_plus_years_eligible(self):
        self.job['description'] = 'Java Spring Boot; 2+ years of professional experience.'
        self.assertEqual(agent.classify(self.job, agent.DEFAULT, self.now)[0], 'Matched')

    def test_explicit_higher_minimum_does_not_match(self):
        self.job['description'] = 'Java Spring Boot; minimum 4 years of experience.'
        self.assertNotEqual(agent.classify(self.job, agent.DEFAULT, self.now)[0], 'Matched')

    def test_common_year_parentheses_format(self):
        self.job['description'] = 'Java Spring Boot. Minimum 2 year(s) of experience is required.'
        self.assertEqual(agent.classify(self.job, agent.DEFAULT, self.now)[0], 'Matched')
        self.job['description'] = 'Java Spring Boot. Minimum 3 year(s) of experience is required.'
        self.assertEqual(agent.classify(self.job, agent.DEFAULT, self.now)[0], 'Excluded')

    def test_explicit_remote_india_allowed(self):
        self.job['jobLocation'] = {'address': {'addressLocality': 'Remote, India', 'addressCountry': 'IN'}}
        self.assertEqual(agent.classify(self.job, agent.DEFAULT, self.now)[0], 'Matched')

    def test_structured_experience_months(self):
        self.job['description'] = 'Java Spring Boot'
        self.job['experienceRequirements'] = {'monthsOfExperience': 24}
        self.assertEqual(agent.classify(self.job, agent.DEFAULT, self.now)[0], 'Matched')


class SmartRecruitersTests(unittest.TestCase):
    BOARD = {'provider': 'smartrecruiters', 'board': 'Example', 'company': 'Example'}

    def row(self, ident='123'):
        return {'id': ident, 'name': 'Software Engineer', 'releasedDate': '2026-11-11T12:00:00Z',
                'location': {'city': 'Bengaluru', 'country': 'in'}}

    def test_india_filter_and_safe_job_url(self):
        board = next(sources.configured_boards({'company_boards': [self.BOARD | {'country': 'in'}]}))
        self.assertIn('&country=in', sources.endpoint(board))
        self.assertTrue(sources.allowed_alert_url('https://jobs.smartrecruiters.com/Example/123-developer', {'company_boards': [board]}))
        self.assertFalse(sources.allowed_alert_url('https://jobs.smartrecruiters.com/Other/123-developer', {'company_boards': [board]}))

    def test_detail_fetch_for_generic_engineer_in_preferred_city(self):
        detail = self.row() | {'jobAd': {'sections': {'jobDescription': {'text': 'Java Spring Boot; 2-4 years experience.'}}}}
        with patch.object(sources, 'read_json', side_effect=[{'content': [self.row()], 'totalFound': 1}, detail]) as read:
            jobs, status = sources.collect_smartrecruiters(self.BOARD, agent.DEFAULT, 10)
        self.assertEqual(read.call_count, 2)
        self.assertIn('Spring Boot', jobs[0]['description'])
        self.assertEqual(status['details_read'], 1)
        self.assertEqual(jobs[0]['datePosted'], '2026-11-11T12:00:00Z')

    def test_non_target_location_does_not_trigger_detail_fetch(self):
        row = self.row() | {'location': {'city': 'Paris', 'country': 'fr'}}
        with patch.object(sources, 'read_json', return_value={'content': [row], 'totalFound': 1}) as read:
            jobs, status = sources.collect_smartrecruiters(self.BOARD, agent.DEFAULT, 10)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(status['details_read'], 0)

    def test_detail_failure_retains_reviewable_metadata(self):
        with patch.object(sources, 'read_json', side_effect=[{'content': [self.row()], 'totalFound': 1}, OSError('unavailable')]):
            jobs, status = sources.collect_smartrecruiters(self.BOARD, agent.DEFAULT, 10)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(status['status'], 'partial')

    def test_pagination_reads_next_page(self):
        rows = [self.row(str(i)) | {'name': 'Sales'} for i in range(100)]
        with patch.object(sources, 'read_json', side_effect=[{'content': rows, 'totalFound': 101}, {'content': [self.row('200') | {'name': 'Sales'}], 'totalFound': 101}]) as read:
            jobs, status = sources.collect_smartrecruiters(self.BOARD, agent.DEFAULT, 200)
        self.assertEqual(len(jobs), 101)
        self.assertIn('offset=100', read.call_args.args[0])

    def test_eu_greenhouse_link_preserved(self):
        board = {'provider': 'greenhouse', 'board': 'groww'}
        row = {'id': 123, 'absolute_url': 'https://job-boards.eu.greenhouse.io/groww/jobs/123'}
        self.assertEqual(sources.normalize_posting(row, board)['url'], row['absolute_url'])

    def test_parallel_feed_failure_isolated(self):
        boards = [{'provider': 'greenhouse', 'board': 'one'}, {'provider': 'greenhouse', 'board': 'two'}]
        def read(url):
            if '/one/' in url: raise OSError('offline')
            return {'jobs': []}
        with patch.object(sources, 'read_json', side_effect=read):
            jobs, status = sources.collect_company_jobs({'company_boards': boards, 'company_feed_workers': 2})
        self.assertEqual([r['status'] for r in status], ['error', 'ok'])


class CatalogAndMigrationTests(unittest.TestCase):
    def test_active_feeds_have_catalog_entries(self):
        root = Path(agent.__file__).resolve().parent
        cfg = json.loads((root / 'digest_config.json').read_text())
        catalog = json.loads((root / cfg['company_catalog_file']).read_text())
        mapped = {(r['feed']['provider'], r['feed']['board']) for r in catalog['companies'] if r.get('feed')}
        self.assertTrue(all((b['provider'], b['board']) in mapped for b in cfg['company_boards']))
        urls = [r['careers_url'].rstrip('/') for r in catalog['companies'] if r.get('careers_url')]
        self.assertEqual(len(urls), len(set(urls)))

    def test_coverage_distinguishes_unmonitored_companies(self):
        from openpyxl import load_workbook
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [{'company': 'Active', 'careers_url': 'https://example.invalid', 'feed': {'provider': 'greenhouse', 'board': 'example'}},
                    {'company': 'Manual', 'careers_url': 'https://manual.invalid', 'reason': 'Adapter needed'}]
            (root / 'catalog.json').write_text(json.dumps({'companies': rows}))
            with patch.object(agent, 'ROOT', root), patch.object(agent, 'CODE_DIR', root):
                db = agent.connect()
                agent.export(db, {'company_catalog_file': 'catalog.json', 'company_boards': [{'provider': 'greenhouse', 'board': 'example'}]})
                db.close()
                wb = load_workbook(root / 'Job_Tracker.xlsx')
                self.assertEqual(wb['Company coverage']['C2'].value, 'Monitored via public feed')
                self.assertEqual(wb['Company coverage']['C3'].value, 'Not monitored automatically')
                wb.close()

    def test_filter_change_rechecks_excluded_but_preserves_email_history(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(agent, 'ROOT', Path(directory)):
            root = Path(directory)
            now = datetime.now(timezone.utc).isoformat()
            jobs = [{'url': 'https://example.invalid/jobs/'+str(i), 'title': 'Software Engineer',
                     'description': 'Java Spring Boot; 2-4 years experience.', 'datePosted': now,
                     'hiringOrganization': {'name': 'Example'}, 'jobLocation': {'address': {'addressLocality': 'Bengaluru'}}} for i in range(2)]
            (root / 'input.json').write_text(json.dumps(jobs))
            db = agent.connect()
            for i, job in enumerate(jobs):
                db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                           (job['url'], job['title'], 'Example', 'Bengaluru', now, 'Excluded', 'Old policy', '', now, 'Already sent' if i else '', str(i)))
            db.execute("INSERT INTO settings VALUES('matching_policy', 'old')")
            db.commit(); db.close()
            cfg = agent.DEFAULT | {'gmail': 'test@example.invalid', 'recipient': 'test@example.invalid', 'max_pages_per_run': 0}
            with patch.object(agent, 'load_config', return_value=cfg), patch.object(agent, 'seed_legacy_history'):
                agent.run(Namespace(preview=True, input=str(root / 'input.json')))
            db = sqlite3.connect(root / 'jobs.sqlite3')
            self.assertEqual(db.execute('SELECT state FROM jobs WHERE url=?', (jobs[0]['url'],)).fetchone()[0], 'Matched')
            self.assertEqual(db.execute('SELECT mailed FROM jobs WHERE url=?', (jobs[1]['url'],)).fetchone()[0], 'Already sent')
            db.close()
            summary = json.loads((root / 'run_summary.json').read_text())
            self.assertEqual(summary['pages_checked'], 0)
            self.assertEqual(summary['postings_evaluated'], 1)


if __name__ == '__main__':
    unittest.main()
