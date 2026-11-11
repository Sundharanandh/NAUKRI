import gc
import json
import os
import tempfile
import unittest
from argparse import Namespace
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import MagicMock, patch

import digest_agent as agent
import job_sources as sources


class AlertPlatformTests(unittest.TestCase):
    LINKS = {
        'Foundit': 'https://www.foundit.in/job/java-developer-example-12345?utm_source=mail',
        'Instahyre': 'https://www.instahyre.com/job-12345-java-developer-at-example/',
        'Hirist': 'https://www.hirist.tech/j/java-developer-example-12345?jobPos=2',
        'Cutshort': 'https://cutshort.io/job/Java-Developer-Bengaluru-Example-aBc123',
        'Wellfound': 'https://wellfound.com/jobs/12345-java-developer',
    }

    def test_all_new_platforms_recognized(self):
        for name, url in self.LINKS.items():
            with self.subTest(platform=name):
                canonical = agent.canonical(url)
                self.assertTrue(agent.allowed(canonical, agent.DEFAULT))
                self.assertEqual(sources.source_name(canonical, agent.DEFAULT), name)

    def test_search_homepage_and_lookalike_links_rejected(self):
        for domain in ('foundit.in', 'instahyre.com', 'hirist.tech', 'cutshort.io', 'wellfound.com'):
            self.assertFalse(agent.allowed('https://' + domain + '/', agent.DEFAULT))
            self.assertFalse(agent.allowed('https://' + domain + '/jobs/java-jobs-in-bangalore', agent.DEFAULT))
            self.assertFalse(agent.allowed('https://' + domain + '.evil.test/job/example', agent.DEFAULT))

    def test_disabled_platform_is_not_read(self):
        cfg = agent.DEFAULT | {'enabled_platforms': ['LinkedIn']}
        self.assertFalse(agent.allowed(agent.canonical(self.LINKS['Foundit']), cfg))

    def test_hirist_legacy_domain_and_cutshort_short_link(self):
        self.assertTrue(agent.allowed('https://www.hirist.com/j/java-developer-12345', agent.DEFAULT))
        self.assertTrue(agent.allowed('https://cutshort.io/a/aBc123-nh', agent.DEFAULT))

    def test_alias_and_tracking_deduplication(self):
        self.assertEqual(agent.canonical(self.LINKS['Foundit']),
                         agent.canonical('https://foundit.in/job/java-developer-example-12345'))
        self.assertEqual(agent.canonical('https://www.cutshort.io/job/example?utm=one'),
                         agent.canonical('https://cutshort.io/job/example?utm=two'))
        self.assertEqual(agent.canonical('https://boards.greenhouse.io/example/jobs/123?utm=x'),
                         agent.canonical('https://job-boards.greenhouse.io/example/jobs/123'))

    def test_email_ingestion_reads_new_links_without_marking_seen(self):
        msg = EmailMessage()
        msg.set_content('Job alerts')
        msg.add_alternative(''.join('<a href="' + url + '">Job</a>' for url in self.LINKS.values()), subtype='html')
        imap = MagicMock()
        imap.__enter__.return_value = imap
        imap.select.return_value = ('OK', [b'1'])
        imap.search.return_value = ('OK', [b'1'])
        imap.fetch.return_value = ('OK', [(b'1', msg.as_bytes())])
        with patch.object(agent.imaplib, 'IMAP4_SSL', return_value=imap):
            urls = agent.read_alert_urls(agent.DEFAULT | {'gmail': 'test@example.invalid'}, 'fake')
        self.assertEqual(set(urls), {agent.canonical(url) for url in self.LINKS.values()})
        imap.select.assert_called_once_with('"JobAlerts"', readonly=True)
        imap.fetch.assert_called_once_with(b'1', '(BODY.PEEK[])')


class BoardConfigurationTests(unittest.TestCase):
    def test_tenant_allowlist(self):
        cfg = {'company_boards': [{'provider': 'greenhouse', 'board': 'example'}]}
        self.assertTrue(agent.allowed('https://boards.greenhouse.io/example/jobs/123', cfg))
        self.assertFalse(agent.allowed('https://boards.greenhouse.io/another/jobs/123', cfg))

    def test_shared_domain_does_not_allow_all_employers(self):
        cfg = {'company_domains': ['greenhouse.io']}
        self.assertFalse(agent.allowed('https://boards.greenhouse.io/another/jobs/123', cfg))

    def test_eu_lever_and_ashby_case(self):
        cfg = {'company_boards': [{'provider': 'lever', 'board': 'example', 'region': 'eu'},
                                 {'provider': 'ashby', 'board': 'ashby'}]}
        self.assertTrue(agent.allowed('https://jobs.eu.lever.co/example/abc-123', cfg))
        self.assertFalse(agent.allowed('https://jobs.lever.co/example/abc-123', cfg))
        self.assertTrue(agent.allowed('https://jobs.ashbyhq.com/Ashby/abc-123', cfg))

    def test_invalid_configuration_stops_before_network(self):
        with patch.object(sources, 'read_json') as fetch:
            for board in [{'provider': 'unknown', 'board': 'example'},
                          {'provider': 'lever', 'board': '../../example'},
                          {'provider': 'ashby', 'board': 'example', 'region': 'eu'}]:
                with self.subTest(board=board), self.assertRaises(ValueError):
                    sources.collect_company_jobs({'company_boards': [board]})
            fetch.assert_not_called()

    def test_disabled_and_duplicate_boards_not_polled_twice(self):
        entry = {'provider': 'lever', 'board': 'example'}
        cfg = {'company_boards': [entry, entry, entry | {'board': 'disabled', 'enabled': False}]}
        with patch.object(sources, 'read_json', return_value=[]) as fetch:
            jobs, status = sources.collect_company_jobs(cfg)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(len(status), 1)

    def test_non_https_or_credential_url_rejected(self):
        cfg = {'company_boards': [{'provider': 'ashby', 'board': 'example'}]}
        for url in ['http://jobs.ashbyhq.com/example/abc', 'https://user:pass@jobs.ashbyhq.com/example/abc']:
            self.assertFalse(agent.allowed(url, cfg))


class PublicFeedTests(unittest.TestCase):
    STAMP = '2026-11-11T09:00:00Z'

    def setUp(self):
        self.greenhouse = {'provider': 'greenhouse', 'board': 'example', 'region': 'global', 'company': 'Example'}
        self.lever = self.greenhouse | {'provider': 'lever'}
        self.ashby = self.greenhouse | {'provider': 'ashby'}
        self.description = 'Java Spring Boot Microservices Angular AWS. 2-4 years experience.'

    def gh_row(self, ident=1):
        return {'id': ident, 'title': 'Java Developer', 'content': '<p>' + self.description + '</p>',
                'first_published': self.STAMP, 'location': {'name': 'Bengaluru, India'}}

    def test_greenhouse_publication_and_description(self):
        job = sources.normalize_posting(self.gh_row(), self.greenhouse)
        self.assertEqual(job['datePosted'], self.STAMP)
        self.assertIn('Spring Boot', job['description'])
        self.assertEqual(job['url'], 'https://job-boards.greenhouse.io/example/jobs/1')

    def test_updated_date_is_not_publication(self):
        row = self.gh_row()
        del row['first_published']
        row['updated_at'] = self.STAMP
        self.assertIsNone(sources.normalize_posting(row, self.greenhouse)['datePosted'])

    def test_lever_description_sections_and_unknown_date(self):
        row = {'id': 'abc-123', 'text': 'Java Developer', 'descriptionPlain': 'Java Spring Boot',
               'lists': [{'text': 'Requirements', 'content': '<p>Microservices Angular AWS 2-4 years experience</p>'}],
               'categories': {'location': 'Bengaluru'}, 'country': 'IN', 'createdAt': 1794387600000}
        job = sources.normalize_posting(row, self.lever)
        self.assertIn('Microservices Angular AWS', job['description'])
        self.assertIsNone(job['datePosted'])
        result = agent.classify(job, agent.DEFAULT, datetime(2026, 11, 11, 12, tzinfo=timezone.utc))
        self.assertEqual(result[0], 'Review')
        self.assertIn('Posting date unavailable', result[1])

    def test_ashby_locations_date_and_listed_filter(self):
        row = {'title': 'Java Developer', 'jobUrl': 'https://jobs.ashbyhq.com/Example/abc-123',
               'descriptionPlain': self.description, 'publishedAt': self.STAMP,
               'location': 'London', 'address': {'postalAddress': {'addressLocality': 'London', 'addressCountry': 'GB'}},
               'secondaryLocations': [{'location': 'Bengaluru', 'address': {'addressLocality': 'Bengaluru', 'addressCountry': 'IN'}}]}
        job = sources.normalize_posting(row, self.ashby)
        self.assertEqual(job['datePosted'], self.STAMP)
        self.assertIn('Bengaluru', agent.locations(job))
        self.assertEqual(agent.classify(job, agent.DEFAULT, datetime(2026, 11, 11, 12, tzinfo=timezone.utc))[0], 'Matched')
        self.assertIsNone(sources.normalize_posting(row | {'isListed': False}, self.ashby))

    def test_ashby_unrelated_tenant_rejected(self):
        with self.assertRaises(ValueError):
            sources.normalize_posting({'jobUrl': 'https://jobs.ashbyhq.com/another/abc'}, self.ashby)

    def test_failed_provider_does_not_block_other_boards(self):
        cfg = {'company_boards': [self.greenhouse, self.ashby]}
        with patch.object(sources, 'read_json', side_effect=[OSError('down'), {'jobs': []}]):
            jobs, statuses = sources.collect_company_jobs(cfg)
        self.assertEqual([row['status'] for row in statuses], ['error', 'ok'])

    def test_lever_pagination(self):
        first = [{'id': f'id-{i}', 'text': 'Java Developer'} for i in range(100)]
        with patch.object(sources, 'read_json', side_effect=[first, [{'id': 'last', 'text': 'Java Developer'}]]) as fetch:
            jobs, statuses = sources.collect_company_jobs({'company_boards': [self.lever]})
        self.assertEqual(len(jobs), 101)
        self.assertIn('skip=100', fetch.call_args.args[0])
        self.assertEqual(statuses[0]['status'], 'ok')

    def test_newest_before_per_board_cap(self):
        old = self.gh_row(1) | {'first_published': '2020-01-01T00:00:00Z'}
        with patch.object(sources, 'read_json', return_value={'jobs': [old, self.gh_row(2)]}):
            jobs, statuses = sources.collect_company_jobs({'company_boards': [self.greenhouse], 'max_company_jobs_per_board': 1})
        self.assertTrue(jobs[0]['url'].endswith('/2'))
        self.assertTrue(statuses[0]['capped'])

    def test_bad_individual_record_does_not_drop_valid_job(self):
        with patch.object(sources, 'read_json', return_value={'jobs': [self.gh_row(), {'title': 'missing ID'}]}):
            jobs, statuses = sources.collect_company_jobs({'company_boards': [self.greenhouse]})
        self.assertEqual(len(jobs), 1)
        self.assertEqual(statuses[0]['invalid_postings'], 1)
        self.assertEqual(statuses[0]['status'], 'partial')


class CombinedPipelineTests(unittest.TestCase):
    def test_feed_and_email_share_history_and_excel(self):
        from openpyxl import load_workbook
        with tempfile.TemporaryDirectory() as directory, patch.object(agent, 'ROOT', Path(directory)):
            cfg = agent.DEFAULT | {'gmail': 'test@example.invalid', 'recipient': 'test@example.invalid',
                                   'company_boards': [{'provider': 'greenhouse', 'board': 'example'}]}
            job = {'url': 'https://job-boards.greenhouse.io/example/jobs/1', 'title': 'Java Developer',
                   'hiringOrganization': {'name': 'Example'}, 'description': 'Java Spring Boot Microservices Angular AWS 2-4 years experience',
                   'datePosted': datetime.now(timezone.utc).isoformat(), 'jobLocation': {'address': {'addressLocality': 'Bengaluru', 'addressCountry': 'IN'}}}
            statuses = [{'source': 'Greenhouse:example', 'status': 'ok', 'postings_read': 1}]
            with patch.dict(os.environ, {'GMAIL_APP_PASSWORD': 'fake'}), \
                 patch.object(agent, 'load_config', return_value=cfg), \
                 patch.object(agent, 'seed_legacy_history'), \
                 patch.object(agent, 'collect_company_jobs', return_value=([job], statuses)), \
                 patch.object(agent, 'read_alert_urls', return_value=[agent.canonical('https://boards.greenhouse.io/example/jobs/1')]), \
                 patch.object(agent, 'fetch_job') as fetch:
                agent.run(Namespace(preview=True, input=None))
                fetch.assert_not_called()
            summary = json.loads((Path(directory) / 'run_summary.json').read_text())
            self.assertEqual(summary['alert_links'], 1)
            self.assertEqual(summary['new_matches_for_email'], 1)
            workbook = load_workbook(Path(directory) / 'Job_Tracker.xlsx')
            self.assertEqual(workbook['Matches'].max_row, 2)
            self.assertIn('Source status', workbook.sheetnames)
            workbook.close()
            gc.collect()

    def test_partial_source_failure_visible_in_digest(self):
        cfg = agent.DEFAULT | {'gmail': 'test@example.invalid', 'recipient': 'test@example.invalid'}
        msg = agent.digest([], cfg, [{'source': 'Ashby:example', 'status': 'error', 'error': 'HTTPError'}])
        self.assertIn('Ashby:example: error', msg.get_content())
        self.assertIn('Source status', msg.get_content())


if __name__ == '__main__':
    unittest.main()
