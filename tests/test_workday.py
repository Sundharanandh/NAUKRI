import json
import os
import tempfile
import unittest
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import digest_agent as agent
import job_sources as sources
import workday_source as workday


class WorkdayTests(unittest.TestCase):
    def setUp(self):
        self.board = {'provider': 'workday', 'board': 'example', 'host': 'example.wd5.myworkdayjobs.com',
                      'site': 'ExternalCareers', 'company': 'Example'}
        self.date = datetime.now(timezone.utc).date().isoformat()
        self.info = {'title': 'Software Engineer', 'jobDescription': 'Java Spring Boot. 2+ years of experience.',
                     'location': 'Bangalore', 'country': {'descriptor': 'India'},
                     'startDate': self.date, 'postedOn': 'Posted Today', 'canApply': True}
        self.path = '/job/Bangalore/Software-Engineer_123'
        self.row = {'title': 'Software Engineer', 'locationsText': 'Bangalore', 'postedOn': 'Posted Today', 'externalPath': self.path}

    def test_tenant_and_site_validated(self):
        self.assertEqual(len(list(sources.configured_boards({'company_boards': [self.board]}))), 1)
        with self.assertRaises(ValueError):
            list(sources.configured_boards({'company_boards': [self.board | {'host': 'evil.example.com'}]}))
        with self.assertRaises(ValueError):
            list(sources.configured_boards({'company_boards': [self.board | {'site': '../Other'}]}))

    def test_multiple_sites_are_not_deduplicated(self):
        self.assertEqual(len(list(sources.configured_boards({'company_boards': [self.board, self.board | {'site': 'EarlyCareers'}]}))), 2)

    def test_allowlist_matches_only_configured_tenant_and_site(self):
        cfg = {'company_boards': [self.board]}
        url = f"https://{self.board['host']}/en-US/ExternalCareers{self.path}"
        self.assertTrue(agent.allowed(url, cfg))
        self.assertFalse(agent.allowed(url.replace('ExternalCareers', 'OtherCareers'), cfg))
        self.assertFalse(agent.allowed(url.replace('example.wd5', 'other.wd5'), cfg))

    def test_posting_start_date_corroborated_by_public_label(self):
        self.assertEqual(workday.publication_date(self.info), self.date)
        self.assertIsNone(workday.publication_date(self.info | {'postedOn': 'Unclear'}))
        old = (datetime.now(timezone.utc).date()-timedelta(days=100)).isoformat()
        self.assertIsNone(workday.publication_date(self.info | {'startDate': old}))

    def test_normalized_job_fits_required_policy(self):
        job = workday.normalize_workday(self.info, self.path, self.board)
        self.assertTrue(job['url'].startswith('https://example.wd5.myworkdayjobs.com/en-US/ExternalCareers/job/'))
        self.assertEqual(agent.classify(job, agent.DEFAULT, datetime.now(timezone.utc))[0], 'Matched')
        self.assertIsNone(workday.normalize_workday(self.info | {'canApply': False}, self.path, self.board))

    def test_search_and_detail_reads(self):
        with patch.object(workday, 'read_workday', side_effect=[{'jobPostings': [self.row], 'total': 1}, {'jobPostingInfo': self.info}]):
            jobs, status = workday.collect_workday(self.board, agent.DEFAULT, 20)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(status['details_read'], 1)

    def test_india_facet_comes_from_public_response(self):
        first = {'jobPostings': [], 'total': 100, 'facets': [{'facetParameter': 'locationCountry', 'values': [{'descriptor': 'India', 'id': 'public-india-id'}]}]}
        requests = []
        def read(board, path, payload=None):
            if payload is not None:
                requests.append(json.loads(json.dumps(payload)))
                return first if len(requests) == 1 else {'jobPostings': [], 'total': 0}
            raise AssertionError('No details expected')
        with patch.object(workday, 'read_workday', side_effect=read):
            jobs, status = workday.collect_workday(self.board, agent.DEFAULT, 20)
        self.assertTrue(status['india_filter_applied'])
        self.assertEqual(requests[1]['appliedFacets']['locationCountry'], ['public-india-id'])

    def test_total_zero_on_later_page_does_not_stop_early(self):
        page = [self.row | {'title': 'Sales'} for _ in range(20)]
        with patch.object(workday, 'read_workday', side_effect=[{'jobPostings': page, 'total': 60}, {'jobPostings': page, 'total': 0}, {'jobPostings': page, 'total': 0}]) as read:
            jobs, status = workday.collect_workday(self.board, agent.DEFAULT, 100)
        self.assertEqual(read.call_count, 3)
        self.assertEqual(status['postings_read'], 60)

    def test_nested_india_facet(self):
        first = {'jobPostings': [], 'total': 1, 'facets': [{'facetParameter': 'locationMainGroup', 'values': [{'facetParameter': 'locationCountry', 'values': [{'descriptor': 'India', 'id': 'india'}]}]}]}
        with patch.object(workday, 'read_workday', side_effect=[first, {'jobPostings': [], 'total': 0}]):
            jobs, status = workday.collect_workday(self.board, agent.DEFAULT, 20)
        self.assertTrue(status['india_filter_applied'])

    def test_old_posting_does_not_spend_detail_request(self):
        row = self.row | {'postedOn': 'Posted 30+ Days Ago'}
        with patch.object(workday, 'read_workday', return_value={'jobPostings': [row], 'total': 1}) as read:
            jobs, status = workday.collect_workday(self.board, agent.DEFAULT, 20)
        self.assertEqual(read.call_count, 1)
        self.assertFalse(jobs)

    def test_application_endpoint_never_accepts_post(self):
        with self.assertRaises(ValueError): workday.read_workday(self.board, '/apply', {})
        with self.assertRaises(ValueError): workday.read_workday(self.board, '/job/../apply')


class ReviewDigestTests(unittest.TestCase):
    def exercise(self, description, expected_review, repeat=False):
        with tempfile.TemporaryDirectory() as directory, patch.object(agent, 'ROOT', Path(directory)):
            cfg = agent.DEFAULT | {'gmail': 'test@example.invalid', 'recipient': 'test@example.invalid',
                                   'include_review_candidates': True, 'max_review_per_email': 5}
            job = {'url': 'https://example.invalid/jobs/1', 'title': 'Software Engineer',
                   'description': description, 'datePosted': datetime.now(timezone.utc).isoformat(),
                   'hiringOrganization': {'name': 'Example'}, 'jobLocation': {'address': {'addressLocality': 'Bengaluru'}}}
            smtp = MagicMock(); smtp.__enter__.return_value = smtp; smtp.send_message.return_value = {}
            with patch.dict(os.environ, {'GMAIL_APP_PASSWORD': 'fake'}), \
                 patch.object(agent, 'load_config', return_value=cfg), patch.object(agent, 'seed_legacy_history'), \
                 patch.object(agent, 'collect_company_jobs', return_value=([job], [])), \
                 patch.object(agent, 'read_alert_urls', return_value=[]), patch.object(agent, 'checkpoint'), \
                 patch.object(agent.smtplib, 'SMTP_SSL', return_value=smtp):
                agent.run(Namespace(preview=False, input=None))
                summary = json.loads((Path(directory)/'run_summary.json').read_text())
                self.assertEqual(summary['new_review_candidates_for_email'], expected_review)
                if expected_review:
                    message = smtp.send_message.call_args.args[0]
                    self.assertIn('1 to review', message['Subject'])
                    self.assertIn('NEEDS REVIEW', message.get_body(preferencelist=('plain',)).get_content())
                if repeat:
                    agent.run(Namespace(preview=False, input=None))
                    summary = json.loads((Path(directory)/'run_summary.json').read_text())
                    self.assertEqual(summary['new_review_candidates_for_email'], 0)

    def test_unknown_experience_labelled_and_not_repeated(self):
        self.exercise('Java Spring Boot development', 1, repeat=True)

    def test_known_ineligible_range_not_sent_for_review(self):
        self.exercise('Java Spring Boot. 4-6 years experience.', 0)

    def test_missing_core_skill_not_sent_for_review(self):
        self.exercise('Python Django development', 0)


if __name__ == '__main__':
    unittest.main()
