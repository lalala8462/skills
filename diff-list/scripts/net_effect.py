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


def compose(repo, selected, capture_java=False, max_java_files=80, max_java_bytes=300000):
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
        if capture_java:
            result['java_sources'] = []
            result['java_skipped'] = []
            candidates = [name for name in result['changed_files'] if name.endswith('.java')]
            for name in candidates[max_java_files:]:
                result['java_skipped'].append({'path': name, 'reason': 'file count limit'})
            rename = git('diff', '--cached', '--name-status', '--find-renames', baseline)
            rename_map = {}
            if rename.returncode == 0:
                for line in rename.stdout.decode('utf-8', 'replace').splitlines():
                    columns = line.split('\t')
                    if len(columns) == 3 and columns[0].startswith('R'):
                        rename_map[columns[2]] = columns[1]
            for name in candidates[:max_java_files]:
                before = git('show', baseline + ':' + rename_map.get(name, name))
                after = git('show', ':' + name)
                if before.returncode != 0 and after.returncode != 0:
                    result['java_skipped'].append({'path': name, 'reason': 'neither version exists'})
                    continue
                before_data = before.stdout if before.returncode == 0 else b''
                after_data = after.stdout if after.returncode == 0 else b''
                if max(len(before_data), len(after_data)) > max_java_bytes:
                    result['java_skipped'].append({'path': name, 'reason': 'file size limit'})
                    continue
                try:
                    before_text = before_data.decode('utf-8')
                    after_text = after_data.decode('utf-8')
                except UnicodeDecodeError:
                    result['java_skipped'].append({'path': name, 'reason': 'non-UTF-8 source'})
                    continue
                result['java_sources'].append({'path': name, 'before': before_text,
                                               'after': after_text})
        return result
