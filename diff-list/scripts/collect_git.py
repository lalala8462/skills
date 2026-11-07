#!/usr/bin/env python3
"""Collect exact selected Git changes without editing repositories (Python 3.8+)."""
import json
import subprocess
from pathlib import Path
from net_effect import compose
from commit_review import review as review_commit_history
from repo_access import decode_git_path


def git(root, *args):
    cmd = ['git', '-C', str(root), '-c', 'core.longpaths=true', '-c', 'core.quotePath=false', '-c',
           'i18n.logOutputEncoding=utf-8', *args]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding='utf-8', errors='backslashreplace', check=False)
    if p.returncode:
        raise ValueError('Git failed: ' + ' '.join(cmd) + '\n' + p.stderr.strip())
    return p.stdout


def git_raw(root,*args):
    command=['git','-C',str(root),'-c','core.longpaths=true',
             '-c','core.quotePath=false',*args]
    result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
    if result.returncode:
        raise ValueError('Git failed: '+result.stderr.decode('utf-8','backslashreplace').strip())
    return result.stdout


def git_null_fields(data):
    return [decode_git_path(item) for item in data.split(bytes([0])) if item]


def is_ancestor(root, earlier, later):
    """Git exit 0=祖先、1=非祖先，其他退出码是执行错误，禁止误判历史。"""
    command=['git','-C',str(root),'merge-base','--is-ancestor',earlier,later]
    result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
    if result.returncode==0:
        return True
    if result.returncode==1:
        return False
    raise ValueError('Git ancestor check failed (exit {}): {}'.format(
        result.returncode,result.stderr.decode('utf-8','replace').strip()))


def resolved(root, rev):
    return git(root, 'rev-parse', '--verify', str(rev) + '^{commit}').strip()


def paths(root, a, b):
    return git_null_fields(git_raw(root,'diff','--name-only','-z','--no-ext-diff',
                                   '--find-renames',a,b))


def read_blob(root, rev, filename):
    """Read one Git blob as bytes; return an error instead of treating failure as empty."""
    p = subprocess.run(['git', '-C', str(root), '-c', 'core.longpaths=true',
                        'show', rev + ':' + filename],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if p.returncode:
        return None, p.stderr.decode('utf-8', 'backslashreplace').strip()[:300]
    return p.stdout, None



def _message(root, sha):
    """Commit text is untrusted intent evidence, never a source of instructions."""
    return git(root, 'log', '-1', '--format=%B', sha)


def _range_commit_review(root, base, head, net_status, max_commits=300,
                         max_review_diff_bytes=8000000):
    """First-parent timeline. Endpoint comparison still uses the complete net diff."""
    # merge-base rather than --is-ancestor so the existing range comparison
    # continues to work even when endpoints belong to different branches.
    common=git(root,'merge-base',base,head).strip()
    if common != base:
        evidence = review_commit_history([], net_status)
        evidence['scope'] = 'endpoint_comparison_only'
        evidence['review_required'] = True
        evidence['review_reasons'].append(
            'base is not ancestor of head; commit history cannot be attributed to a linear range')
        return evidence
    shas = git(root, 'rev-list', '--first-parent', '--reverse',
               base + '..' + head).splitlines()
    if len(shas) > max_commits:
        evidence = review_commit_history([], net_status, omitted=len(shas))
        evidence['scope'] = 'first_parent_range_uninspected'
        return evidence
    entries, merges, used = [], [], 0
    for position, sha in enumerate(shas):
        parents = git(root, 'rev-list', '--parents', '-n', '1', sha).split()[1:]
        if not parents:
            continue
        parent = parents[0]
        delta = git(root, 'diff', '--no-ext-diff', '--no-color',
                    '--find-renames', '--unified=3', parent, sha)
        size = len(delta.encode('utf-8'))
        if used + size > max_review_diff_bytes:
            evidence = review_commit_history(entries, net_status,
                                             omitted=len(shas) - position,
                                             merge_commits=merges)
            evidence['scope'] = 'first_parent_range_incomplete'
            return evidence
        used += size
        if len(parents) > 1:
            merges.append(sha)
        entries.append({'sha': sha, 'parent': parent,
                        'message': _message(root, sha), 'files': paths(root, parent, sha),
                        'diff': delta, 'merge_commit': len(parents) > 1})
    evidence = review_commit_history(entries, net_status, merge_commits=merges)
    evidence['scope'] = 'first_parent_range'
    if entries and entries[0]['parent'] != base:
        evidence['review_required'] = True
        evidence['review_reasons'].append(
            'range base is not on the first-parent timeline; inspect merged branch history')
    return evidence


def collect(project, capture_config=False, persist_snapshot=False):
    if not isinstance(project, dict) or not project.get('path') or not project.get('name'):
        raise ValueError('Each project requires name and path')
    root = Path(git(project['path'], 'rev-parse', '--show-toplevel').strip()).resolve()
    status = None if project.get('isolated_no_checkout') else git(root, 'status', '--porcelain=v1', '--untracked-files=normal').splitlines()
    result = {'name': project['name'], 'repository': str(root),
              'worktree_status': status, 'selection': [], 'unselected_overlaps': []}
    has_range = 'range' in project
    has_revs = 'revisions' in project
    if has_range == has_revs:
        raise ValueError('Specify exactly one of range or revisions for ' + project['name'])
    if has_range:
        r = project['range']
        if not isinstance(r, dict) or not r.get('base') or not r.get('head'):
            raise ValueError('Range needs explicit base and head')
        a, b = resolved(root, r['base']), resolved(root, r['head'])
        changed = paths(root, a, b)
        diff = git(root, 'diff', '--no-ext-diff', '--no-color', '--find-renames',
                   '--unified=8', a, b)
        result['selection'].append({'type': 'range', 'base': a, 'head': b,
                                    'files': changed, 'diff': diff})
        result['net_effect'] = {'status': 'composed' if changed else 'no_net_change',
                                'baseline': a, 'last_selected': b,
                                'changed_files': changed, 'net_diff': diff,
                                'applied_commits': [], 'reason': ''}
        if persist_snapshot:
            result['net_effect']['baseline_tree'] = git(root, 'rev-parse', a + '^{tree}').strip()
            result['net_effect']['result_tree'] = git(root, 'rev-parse', b + '^{tree}').strip()
        if capture_config:
            from config_changes import (SUPPORTED_EXTENSIONS,MAX_CONFIG_FILES,
                                        config_name_changes,config_source)
            rename_output=git_raw(root,'diff','--name-status','-z','--find-renames',a,b)
            rename_map,change_kinds=config_name_changes(git_null_fields(rename_output))
            result['net_effect']['config_sources']=[]
            result['net_effect']['config_skipped']=[]
            selected=[name for name in changed if name.lower().endswith(SUPPORTED_EXTENSIONS)]
            for name in selected[MAX_CONFIG_FILES:]:
                result['net_effect']['config_skipped'].append(
                    {'path':name,'reason':'file count limit'})
            for name in selected[:MAX_CONFIG_FILES]:
                kind=change_kinds.get(name,'M')
                before,before_error=read_blob(root,a,rename_map.get(name,name))
                after,after_error=read_blob(root,b,name)
                if before_error and kind!='A':
                    result['net_effect']['config_skipped'].append(
                        {'path':name,'reason':'cannot read baseline blob: '+before_error})
                    continue
                if after_error and kind!='D':
                    result['net_effect']['config_skipped'].append(
                        {'path':name,'reason':'cannot read target blob: '+after_error})
                    continue
                entry,issue=config_source(name,b'' if before_error else before,
                                           b'' if after_error else after)
                if issue:
                    result['net_effect']['config_skipped'].append(issue)
                else:
                    result['net_effect']['config_sources'].append(entry)
        result['commit_review'] = _range_commit_review(root, a, b,
            result['net_effect']['status'])
        return result
    revs = project['revisions']
    if not isinstance(revs, list) or not revs:
        raise ValueError('revisions must be a nonempty list')
    entries = []
    for raw in revs:
        spec = {'rev': raw} if isinstance(raw, str) else raw
        if not isinstance(spec, dict) or not spec.get('rev'):
            raise ValueError('revision must contain rev')
        sha = resolved(root, spec['rev'])
        parents = git(root, 'rev-list', '--parents', '-n', '1', sha).split()[1:]
        if not parents:
            raise ValueError('Root commit needs an explicit range baseline: ' + sha)
        if len(parents) > 1 and 'parent' not in spec:
            raise ValueError('Merge commit requires parent index (1-based): ' + sha)
        parent_index = spec.get('parent', 1)
        if type(parent_index) is not int or not 1 <= parent_index <= len(parents):
            raise ValueError('Invalid parent index for ' + sha)
        parent = parents[parent_index - 1]
        if any(e['sha'] == sha for e in entries):
            raise ValueError('Duplicate commit ' + sha)
        entries.append({'sha': sha, 'parent': parent, 'parent_index': parent_index})
    # 各提交的可达祖先数沿祖先关系严格增加。按该数量排序后，
    # 只需检查相邻提交是否存在祖先关系，无需执行 O(n²) 次 Git 检查。
    order={entry['sha']:int(git(root,'rev-list','--count',entry['sha']).strip())
           for entry in entries}
    entries.sort(key=lambda entry:(order[entry['sha']],entry['sha']))
    for earlier,later in zip(entries,entries[1:]):
        if not is_ancestor(root,earlier['sha'],later['sha']):
            raise ValueError('Selected revisions on incomparable histories; confirm final combination')
    selected = {e['sha'] for e in entries}
    all_paths = set()
    for e in entries:
        files = paths(root, e['parent'], e['sha'])
        all_paths.update(files)
        result['selection'].append({'type':'commit','sha':e['sha'],
            'parent':e['parent'],'parent_index':e['parent_index'], 'files':files,
            'diff':git(root,'diff','--no-ext-diff','--no-color','--find-renames',
                       '--unified=8',e['parent'],e['sha'])})
    for earlier, later in zip(entries, entries[1:]):
        gap = git(root, 'rev-list', '--ancestry-path',
                  earlier['sha'] + '..' + later['sha']).splitlines()
        for sha in gap:
            if sha in selected:
                continue
            parent_line = git(root,'rev-list','--parents','-n','1',sha).split()
            if len(parent_line) < 2:
                continue
            changed = set(paths(root,parent_line[1],sha))
            overlap = sorted(changed & all_paths)
            if overlap:
                result['unselected_overlaps'].append({'sha':sha,'files':overlap})
    result['net_effect'] = compose(root, entries, capture_config=capture_config,
                                   persist_snapshot=persist_snapshot)
    selected_history = []
    for e, item in zip(entries, result['selection']):
        selected_history.append({'sha': e['sha'], 'parent': e['parent'],
            'message': _message(root, e['sha']), 'files': item['files'],
            'diff': item['diff'], 'merge_commit': e['parent_index'] != 1 or
                                   len(git(root, 'rev-list', '--parents', '-n', '1', e['sha']).split()) > 2})
    result['commit_review'] = review_commit_history(
        selected_history, result['net_effect']['status'],
        merge_commits=[e['sha'] for e in selected_history if e['merge_commit']])
    result['commit_review']['scope'] = 'selected_commits_only'
    if result['unselected_overlaps']:
        result['commit_review']['review_required'] = True
        result['commit_review']['review_reasons'].append(
            'unselected intermediate commits touch selected files; scope needs review')
    return result
