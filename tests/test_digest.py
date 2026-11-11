import gc
import json
import os
import sqlite3
import tempfile
import unittest
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import digest_agent as m


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 11, 5, tzinfo=timezone.utc)
        self.job = {'title': 'Java Full Stack Developer',
                    'description': 'Java Spring Boot Microservices Angular AWS; 2-4 years experience',
                    'datePosted': '2026-11-10T12:00:00Z',
                    'hiringOrganization': {'name': 'Example'},
                    'jobLocation': {'address': {'addressLocality': 'Bangalore', 'addressCountry': 'IN'}}}

    def result(self):
        return m.classify(self.job, m.DEFAULT, self.now)[0]

    def test_matching_job(self):
        self.assertEqual(self.result(), 'Matched')

    def test_missing_skill_needs_review(self):
        self.job['description'] = self.job['description'].replace('AWS', '')
        self.assertEqual(self.result(), 'Review')

    def test_javascript_is_not_java(self):
        import re
        self.assertIsNone(re.search(m.DEFAULT['required_skills']['Java'], 'JavaScript developer', re.I))

    def test_experience_not_assumed(self):
        self.job['description'] = self.job['description'].replace('2-4', '4-6')
        self.assertEqual(self.result(), 'Review')

    def test_unknown_experience_needs_review(self):
        self.job['description'] = self.job['description'].replace('2-4 years experience', '')
        self.assertEqual(self.result(), 'Review')

    def test_stale_excluded(self):
        self.job['datePosted'] = '2026-10-01'
        self.assertEqual(self.result(), 'Excluded')

    def test_unknown_date_needs_review(self):
        del self.job['datePosted']
        self.assertEqual(self.result(), 'Review')

    def test_senior_excluded(self):
        self.job['title'] = 'Senior Java Developer'
        self.assertEqual(self.result(), 'Excluded')

    def test_support_role_excluded(self):
        self.job['title'] = 'Java Production Support Engineer'
        self.assertEqual(self.result(), 'Excluded')

    def test_expired_excluded(self):
        self.job['validThrough'] = '2026-11-09'
        self.assertEqual(self.result(), 'Excluded')

    def test_location_must_match(self):
        self.job['jobLocation']['address']['addressLocality'] = 'Delhi'
        self.assertEqual(self.result(), 'Review')

    def test_mumbai_is_supported(self):
        self.job['jobLocation']['address']['addressLocality'] = 'Mumbai'
        self.assertEqual(self.result(), 'Matched')

    def test_tracking_parameters_removed(self):
        self.assertEqual(m.canonical('https://www.linkedin.com/jobs/view/123/?utm_source=x'),
                         'https://www.linkedin.com/jobs/view/123')
        self.assertEqual(m.canonical('https://in.indeed.com/viewjob?vjk=abc&utm=x'),
                         'https://in.indeed.com/viewjob?jk=abc')

    def test_linkedin_slug_and_comm_links_share_identity(self):
        self.assertEqual(m.canonical('https://www.linkedin.com/comm/jobs/view/java-developer-at-example-123?trackingId=x'),
                         m.canonical('https://www.linkedin.com/jobs/view/123'))

    def test_indeed_click_link_normalized_to_job(self):
        self.assertEqual(m.canonical('https://in.indeed.com/rc/clk?jk=abc&from=alerts'),
                         'https://in.indeed.com/viewjob?jk=abc')

    def test_no_lookalike_domain(self):
        self.assertFalse(m.allowed('https://linkedin.com.evil.test/jobs/view/123', m.DEFAULT))
        self.assertIsNone(m.canonical('http://localhost/jobs/1'))

    def test_nested_job_json(self):
        self.assertEqual(len(list(m.jobs_in({'@graph': [self.job | {'@type': 'JobPosting'}]}))), 1)

    def test_no_fake_jobs_when_empty(self):
        cfg = m.DEFAULT | {'gmail': 'example@example.com', 'recipient': 'example@example.com'}
        self.assertIn('No new confirmed matches', m.digest([], cfg).get_content())


class ExcelTests(unittest.TestCase):
    def test_notes_survive_and_formula_injection_is_escaped(self):
        from openpyxl import load_workbook
        with tempfile.TemporaryDirectory() as directory, patch.object(m, 'ROOT', Path(directory)):
            db = m.connect()
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       ('https://example.com/job/1', '=HYPERLINK("evil")', 'Example', 'Bangalore',
                        '2026-11-10', 'Matched', '', 'Unknown', '2026-11-11', '', 'fp'))
            db.commit()
            m.export(db)
            path = Path(directory) / 'Job_Tracker.xlsx'
            wb = load_workbook(path)
            self.assertEqual(wb['Matches']['B2'].data_type, 's')
            wb['Matches']['K2'] = 'Applied'
            wb['Matches']['M2'] = 'My note'
            wb.save(path)
            wb.close()
            m.export(db)
            wb = load_workbook(path)
            self.assertEqual(wb['Matches']['K2'].value, 'Applied')
            self.assertEqual(wb['Matches']['M2'].value, 'My note')
            wb.close()
            db.close()


class DeliveryTests(unittest.TestCase):
    def exercise(self, preview=False, failure=None, checkpoint_failure=False):
        with tempfile.TemporaryDirectory() as directory, patch.object(m, 'ROOT', Path(directory)):
            cfg = m.DEFAULT | {'gmail': 'example@example.com', 'recipient': 'example@example.com'}
            job = {'title': 'Java Developer',
                   'description': 'Java Spring Boot Microservices Angular AWS 2-4 years experience',
                   'datePosted': datetime.now(timezone.utc).isoformat(),
                   'hiringOrganization': {'name': 'Example'},
                   'jobLocation': {'address': {'addressLocality': 'Bangalore'}}}
            smtp = MagicMock()
            smtp.__enter__.return_value = smtp
            smtp.send_message.return_value = {}
            if failure == 'send':
                smtp.send_message.side_effect = OSError('disconnected')
            if failure == 'login':
                smtp.login.side_effect = OSError('login failed')
            with patch.dict(os.environ, {'GMAIL_APP_PASSWORD': 'fake', 'JOB_MAILER_CHECKPOINT': '0'}), \
                 patch.object(m, 'load_config', return_value=cfg), \
                 patch.object(m, 'seed_legacy_history'), \
                 patch.object(m, 'read_alert_urls', return_value=['https://www.linkedin.com/jobs/view/123']), \
                 patch.object(m, 'fetch_job', return_value=job), \
                 patch.object(m.smtplib, 'SMTP_SSL', return_value=smtp), \
                 patch.object(m, 'checkpoint') as checkpoint:
                args = Namespace(preview=preview, input=None)
                if checkpoint_failure:
                    checkpoint.side_effect = RuntimeError('push failed')
                if failure or checkpoint_failure:
                    with self.assertRaises((RuntimeError, OSError)):
                        m.run(args)
                else:
                    m.run(args)
                db = sqlite3.connect(Path(directory, 'jobs.sqlite3'))
                mailed = db.execute('SELECT mailed FROM jobs').fetchone()[0]
                db.close()
                if preview or failure == 'login':
                    self.assertFalse(mailed)
                    smtp.send_message.assert_not_called()
                elif checkpoint_failure:
                    smtp.send_message.assert_not_called()
                else:
                    self.assertTrue(mailed)
                    # Simulates the next Actions process restoring the same persisted DB.
                    smtp.send_message.side_effect = None
                    m.run(args)
                    self.assertIn('0 new matches', smtp.send_message.call_args.args[0]['Subject'])
                gc.collect()

    def test_preview_does_not_consume_links(self):
        self.exercise(preview=True)

    def test_successful_delivery_is_not_repeated(self):
        self.exercise()

    def test_uncertain_delivery_is_not_repeated(self):
        self.exercise(failure='send')

    def test_auth_failure_does_not_consume_links(self):
        self.exercise(failure='login')

    def test_failed_persistence_prevents_email(self):
        self.exercise(checkpoint_failure=True)

    def test_expiry_is_rechecked(self):
        # A stale cached Matched record cannot be sent without a current refresh.
        with tempfile.TemporaryDirectory() as directory, patch.object(m, 'ROOT', Path(directory)):
            db = m.connect()
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       ('https://example.com/jobs/1', 'Java Developer', 'Example', 'Bangalore',
                        datetime.now(timezone.utc).isoformat(), 'Matched', '', '', '', '', 'fp'))
            db.commit()
            db.close()
            cfg = m.DEFAULT | {'gmail': 'example@example.com', 'recipient': 'example@example.com'}
            with patch.dict(os.environ, {'GMAIL_APP_PASSWORD': 'fake'}), \
                 patch.object(m, 'load_config', return_value=cfg), \
                 patch.object(m, 'seed_legacy_history'), \
                 patch.object(m, 'read_alert_urls', return_value=[]):
                m.run(Namespace(preview=True, input=None))
            self.assertIn('No new confirmed matches', (Path(directory) / 'Email_Preview.txt').read_text())
            gc.collect()


if __name__ == '__main__':
    unittest.main()
