"""Persist digest history on a dedicated branch, including before SMTP delivery.

Only public job metadata and delivery history are stored, never credentials,
mailbox messages or a resume. GitHub Actions serializes runs with concurrency.
"""
import argparse
import os
import subprocess
from pathlib import Path

BRANCH = 'job-digest-state'
FILES = ('jobs.sqlite3', 'Job_Tracker.xlsx', 'run_summary.json')


def state_dir():
    value = os.environ.get('JOB_MAILER_STATE_DIR')
    if not value:
        raise RuntimeError('JOB_MAILER_STATE_DIR is required')
    return Path(value).resolve()


def git(*args, check=True):
    return subprocess.run(['git', '-C', str(state_dir()), *args], check=check,
                          capture_output=True, text=True)


def restore():
    root = state_dir()
    root.mkdir(parents=True, exist_ok=True)
    if (root / '.git').exists():
        raise RuntimeError('Restore expects a fresh directory, not a source checkout')
    repo = os.environ.get('GITHUB_REPOSITORY', '')
    if not repo or len(repo.split('/')) != 2 or not os.environ.get('GITHUB_TOKEN'):
        raise RuntimeError('GitHub repository identity and workflow token are required')
    git('init', '--initial-branch=' + BRANCH)
    git('remote', 'add', 'origin', 'https://github.com/' + repo + '.git')
    git('config', 'user.name', 'github-actions[bot]')
    git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    # The helper expands the token at execution time. It is not written to disk.
    git('config', 'credential.helper',
        '!f() { printf "%s\\n" "username=x-access-token" "password=$GITHUB_TOKEN"; }; f')
    result = git('ls-remote', '--exit-code', '--heads', 'origin', BRANCH, check=False)
    if result.returncode == 0:
        git('fetch', '--depth=1', 'origin', BRANCH)
        git('checkout', '-B', BRANCH, 'FETCH_HEAD')
        if not (root / 'jobs.sqlite3').exists():
            raise RuntimeError('Existing state branch is missing jobs.sqlite3; refusing to reset history')
        print('Restored persistent job/email history.')
    elif result.returncode == 2:
        print('First run: state branch does not exist yet.')
    else:
        # Treat network/auth failures as errors, never as an empty history.
        raise RuntimeError('Cannot read state branch; no digest will be sent')


def save():
    if not (state_dir() / '.git').exists():
        raise RuntimeError('State was not restored; refusing a nonpersistent send')
    if git('branch', '--show-current').stdout.strip() != BRANCH:
        raise RuntimeError('Unexpected state branch')
    existing = [name for name in FILES if (state_dir() / name).exists()]
    if not existing:
        print('No state files to save.')
        return
    git('add', '--', *existing)
    diff = git('diff', '--cached', '--quiet', check=False)
    if diff.returncode == 1:
        git('commit', '-m', 'Update job digest history and Excel tracker')
    elif diff.returncode != 0:
        raise RuntimeError('Cannot inspect state changes')
    # Push even with no new diff: a previous push could have failed after commit.
    git('push', 'origin', 'HEAD:refs/heads/' + BRANCH)
    print('Persistent history and workbook saved.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('restore', 'save'))
    args = parser.parse_args()
    try:
        {'restore': restore, 'save': save}[args.action]()
    except Exception as exc:
        # Avoid logging token-bearing Git responses or environment values.
        print('State operation failed:', type(exc).__name__)
        raise SystemExit(1)
