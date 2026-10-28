#!/usr/bin/env python3
"""Compose selected commit patches in an isolated index, never in the source repo.

A clean result proves only a mechanical patch combination, NOT runtime behavior.
Failure is a review signal; never fall back to base..last-selected diff.
"""
import os
import subprocess
import tempfile
from pathlib import Path


def _run(cmd, *, stdin=None):
    process = subprocess.run(cmd, input=stdin, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, check=False)
    return process.returncode, process.stdout, process.stderr.decode('utf-8', 'replace').strip()


def compose(repo, selected):
    """selected: ordered [{sha, parent}]. Strictly apply their patches only.

    Uses a temporary shared clone with an isolated index. No checked-out source
    files are modified. Context conflicts mean selected-only net cannot be
    established safely and are returned explicitly for human review.
    """
    if not selected:
        raise ValueError('selected must not be empty')
    repo = Path(repo).resolve()
    baseline = selected[0]['parent']
    head = selected[-1]['sha']
    result = {'status': 'review_required', 'baseline': baseline,
              'last_selected': head, 'changed_files': [], 'net_diff': '',
              'applied_commits': [], 'reason': ''}
    with tempfile.TemporaryDirectory(prefix='diff-list-net-') as tempdir:
        target = Path(tempdir) / 'isolated'
        code, _, msg = _run(['git', 'clone', '--quiet', '--shared',
                             '--no-checkout', str(repo), str(target)])
        if code:
            result['reason'] = 'isolated clone failed: ' + msg
            return result
        env = os.environ.copy()
        env['GIT_OPTIONAL_LOCKS'] = '0'
        # Run git in the temporary clone, never touching source repo index.
        def git(*args, stdin=None):
            return subprocess.run(['git', '-C', str(target), *args], input=stdin,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  check=False, env=env)
        read = git('read-tree', baseline)
        if read.returncode:
            result['reason'] = 'cannot load baseline into isolated index'
            return result
        for item in selected:
            patch = subprocess.run(
                ['git', '-C', str(repo), 'diff', '--no-ext-diff',
                 '--no-color', '--binary', '--full-index', '--find-renames',
                 item['parent'], item['sha']], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, check=False)
            if patch.returncode:
                result['reason'] = 'cannot extract patch: ' + item['sha']
                return result
            apply = git('apply', '--cached', '--binary', '--whitespace=nowarn',
                        '-', stdin=patch.stdout)
            if apply.returncode:
                result['reason'] = ('selected patch cannot be isolated at ' +
                                    item['sha'] + ': ' +
                                    apply.stderr.decode('utf-8', 'replace').strip()[:600])
                return result
            result['applied_commits'].append(item['sha'])
        diff_args = ('diff', '--cached', '--no-ext-diff', '--no-color',
                     '--binary', '--find-renames', baseline)
        diff = git(*diff_args)
        names = git('diff', '--cached', '--name-only',
                    '--no-ext-diff', '--find-renames', baseline)
        if diff.returncode or names.returncode:
            result['reason'] = 'cannot calculate net diff of isolated index'
            return result
        result['net_diff'] = diff.stdout.decode('utf-8', 'replace')
        result['changed_files'] = [line for line in
                                   names.stdout.decode('utf-8', 'replace').splitlines() if line]
        result['status'] = 'composed' if result['changed_files'] else 'no_net_change'
        return result
