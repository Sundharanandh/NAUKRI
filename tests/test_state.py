"""Use real local Git remotes to test history survival without touching GitHub."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import persist_digest_state as state


class PersistentStateTests(unittest.TestCase):
    def test_state_roundtrip_and_unchanged_push(self):
        with tempfile.TemporaryDirectory() as directory:
            remote = Path(directory) / 'remote.git'
            subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
            first = Path(directory) / 'first'
            original = state.git

            def local_git(*args, **kwargs):
                if args[:3] == ('remote', 'add', 'origin'):
                    args = (*args[:3], str(remote))
                return original(*args, **kwargs)

            env = {'GITHUB_REPOSITORY': 'test/example', 'GITHUB_TOKEN': 'test-placeholder',
                   'JOB_MAILER_STATE_DIR': str(first)}
            with patch.dict(os.environ, env), patch.object(state, 'git', side_effect=local_git):
                state.restore()
                (first / 'jobs.sqlite3').write_bytes(b'test-job-history')
                (first / 'Job_Tracker.xlsx').write_bytes(b'test-workbook')
                state.save()
                state.save()  # No new commit must still permit push/recovery.
                second = Path(directory) / 'second'
                os.environ['JOB_MAILER_STATE_DIR'] = str(second)
                state.restore()
                self.assertEqual((second / 'jobs.sqlite3').read_bytes(), b'test-job-history')
                self.assertEqual((second / 'Job_Tracker.xlsx').read_bytes(), b'test-workbook')

    def test_remote_failure_never_means_empty_history(self):
        with tempfile.TemporaryDirectory() as directory:
            original = state.git

            def unreachable(*args, **kwargs):
                if args[:3] == ('remote', 'add', 'origin'):
                    args = (*args[:3], str(Path(directory) / 'missing.git'))
                return original(*args, **kwargs)

            with patch.dict(os.environ, {'JOB_MAILER_STATE_DIR': str(Path(directory) / 'state'),
                                         'GITHUB_REPOSITORY': 'test/example', 'GITHUB_TOKEN': 'test'}), \
                 patch.object(state, 'git', side_effect=unreachable):
                with self.assertRaisesRegex(RuntimeError, 'Cannot read state branch'):
                    state.restore()

    def test_refuse_save_without_restored_state(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {'JOB_MAILER_STATE_DIR': directory}):
            with self.assertRaises(RuntimeError):
                state.save()
