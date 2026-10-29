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


def compose(repo, selected, capture_java=False, capture_config=False,
            max_java_files=80, max_java_bytes=300000, max_config_files=100, max_config_bytes=300000,
            persist_snapshot=False):
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
        if persist_snapshot:
            # 持久快照仅允许写入本次创建、尚未检出的分析仓库。
            if not (repo / '.git').is_dir() or any(p.name != '.git' for p in repo.iterdir()):
                raise ValueError('persistent snapshot requires an empty no-checkout analysis clone')
            target = repo
        else:
            target = Path(tempdir) / 'isolated'
            code, _, msg = _run(['git', '-c', 'core.longpaths=true', 'clone', '--quiet', '--shared',
                                 '--no-checkout', str(repo), str(target)])
            if code:
                result['reason'] = 'isolated clone failed: ' + msg
                return result
        env = os.environ.copy()
        env['GIT_OPTIONAL_LOCKS'] = '0'
        env['GIT_INDEX_FILE'] = str(Path(tempdir) / 'snapshot-index')
        def git(*args, stdin=None):
            return subprocess.run(['git', '-c', 'core.longpaths=true', '-c', 'core.quotePath=false', '-C', str(target), *args], input=stdin,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  check=False, env=env)
        read = git('read-tree', baseline)
        if read.returncode:
            result['reason'] = 'cannot load baseline into isolated index'
            return result
        for item in selected:
            patch = subprocess.run(
                ['git', '-c', 'core.longpaths=true', '-c', 'core.quotePath=false', '-C', str(repo), 'diff', '--no-ext-diff',
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
        if persist_snapshot:
            tree = git('write-tree')
            if tree.returncode:
                result.update(status='review_required', reason='cannot persist composed source tree')
                return result
            result['result_tree'] = tree.stdout.decode('ascii').strip()
            result['baseline_tree'] = git('rev-parse', baseline + '^{tree}').stdout.decode('ascii').strip()
            pin = git('update-ref', 'refs/diff-list/snapshots/' + result['result_tree'], result['result_tree'])
            if pin.returncode:
                result.update(status='review_required', reason='cannot pin composed source tree')
                return result
        rename = git('diff', '--cached', '--name-status', '--find-renames', baseline)
        rename_map, change_kinds = {}, {}
        if rename.returncode == 0:
            for line in rename.stdout.decode('utf-8', 'replace').splitlines():
                columns = line.split('\t')
                if len(columns) < 2:
                    continue
                kind = columns[0][:1]
                target_name = columns[2] if len(columns) == 3 else columns[1]
                change_kinds[target_name] = kind
                if len(columns) == 3 and kind == 'R':
                    rename_map[target_name] = columns[1]
        if capture_java:
            result['java_sources'] = []
            result['java_skipped'] = []
            candidates = [name for name in result['changed_files'] if name.endswith('.java')]
            for name in candidates[max_java_files:]:
                result['java_skipped'].append({'path': name, 'reason': 'file count limit'})
            for name in candidates[:max_java_files]:
                kind = change_kinds.get(name, 'M')
                before = git('show', baseline + ':' + rename_map.get(name, name))
                after = git('show', ':' + name)
                if before.returncode != 0 and kind != 'A':
                    result['java_skipped'].append({
                        'path': name,
                        'reason': 'cannot read baseline blob: ' + before.stderr.decode('utf-8', 'replace').strip()[:300]})
                    continue
                if after.returncode != 0 and kind != 'D':
                    result['java_skipped'].append({
                        'path': name,
                        'reason': 'cannot read target blob: ' + after.stderr.decode('utf-8', 'replace').strip()[:300]})
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
        if capture_config:
            result['config_sources'] = []
            result['config_skipped'] = []
            from config_candidates import SUPPORTED_EXTENSIONS
            names = [name for name in result['changed_files']
                     if name.lower().endswith(SUPPORTED_EXTENSIONS)]
            for name in names[max_config_files:]:
                result['config_skipped'].append({'path': name, 'reason': 'file count limit'})
            for name in names[:max_config_files]:
                kind = change_kinds.get(name, 'M')
                previous = git('show', baseline + ':' + rename_map.get(name, name))
                current = git('show', ':' + name)
                if previous.returncode != 0 and kind != 'A':
                    result['config_skipped'].append({
                        'path': name,
                        'reason': 'cannot read baseline blob: ' + previous.stderr.decode('utf-8', 'replace').strip()[:300]})
                    continue
                if current.returncode != 0 and kind != 'D':
                    result['config_skipped'].append({
                        'path': name,
                        'reason': 'cannot read target blob: ' + current.stderr.decode('utf-8', 'replace').strip()[:300]})
                    continue
                before = previous.stdout if previous.returncode == 0 else b''
                after = current.stdout if current.returncode == 0 else b''
                if max(len(before), len(after)) > max_config_bytes:
                    result['config_skipped'].append({'path': name, 'reason': 'file size limit'})
                    continue
                try:
                    result['config_sources'].append({'path': name, 'before': before.decode('utf-8'),
                                                     'after': after.decode('utf-8')})
                except UnicodeDecodeError:
                    result['config_skipped'].append({'path': name, 'reason': 'non-UTF-8 source'})
        return result
