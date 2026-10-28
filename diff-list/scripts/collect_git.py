#!/usr/bin/env python3
"""Collect exact selected Git changes without editing repositories (Python 3.8+)."""
import argparse
import json
import subprocess
import sys
from pathlib import Path


def git(root, *args):
    cmd = ['git', '-C', str(root), '-c', 'core.quotePath=false', '-c',
           'i18n.logOutputEncoding=utf-8', *args]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding='utf-8', errors='replace', check=False)
    if p.returncode:
        raise ValueError('Git failed: ' + ' '.join(cmd) + '\n' + p.stderr.strip())
    return p.stdout


def resolved(root, rev):
    return git(root, 'rev-parse', '--verify', str(rev) + '^{commit}').strip()


def paths(root, a, b):
    return [s for s in git(root, 'diff', '--name-only', '--no-ext-diff',
                           '--find-renames', a, b).splitlines() if s]


def collect(project):
    if not isinstance(project, dict) or not project.get('path') or not project.get('name'):
        raise ValueError('Each project requires name and path')
    root = Path(git(project['path'], 'rev-parse', '--show-toplevel').strip()).resolve()
    status = git(root, 'status', '--porcelain=v1', '--untracked-files=normal').splitlines()
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
        result['selection'].append({'type': 'range', 'base': a, 'head': b,
            'files': paths(root, a, b), 'diff': git(root, 'diff', '--no-ext-diff',
                '--no-color', '--find-renames', '--unified=8', a, b)})
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
    # Determine chronology by ancestry, never by user input order or timestamp.
    for x in entries:
        for y in entries:
            if x is y:
                continue
            anc_xy = subprocess.run(['git','-C',str(root),'merge-base','--is-ancestor',
                                     x['sha'], y['sha']]).returncode == 0
            anc_yx = subprocess.run(['git','-C',str(root),'merge-base','--is-ancestor',
                                     y['sha'], x['sha']]).returncode == 0
            if not anc_xy and not anc_yx:
                raise ValueError('Selected revisions on incomparable histories; confirm final combination')
    sortable = list(entries)  # list.sort temporarily hides entries from its own key closure
    entries.sort(key=lambda e: sum(subprocess.run(
        ['git','-C',str(root),'merge-base','--is-ancestor',other['sha'],e['sha']]).returncode == 0
        for other in sortable if other is not e))
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
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', required=True, help='UTF-8 JSON with projects array')
    p.add_argument('--output', required=True, help='Local JSON evidence path (sensitive)')
    p.add_argument('--max-diff-bytes',type=int,default=20000000,
                   help='Abort if combined diffs exceed this many UTF-8 bytes')
    args = p.parse_args(argv)
    try:
        manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8-sig'))
        projects = manifest.get('projects', [])
        if not isinstance(projects, list) or not projects:
            raise ValueError('manifest.projects must be a nonempty array')
        names = [p['name'] for p in projects]
        if len(set(names)) != len(names):
            raise ValueError('Project names must be unique')
        if args.max_diff_bytes < 1:
            raise ValueError('--max-diff-bytes must be positive')
        result = {'schema_version':1,'projects':[collect(x) for x in projects],
                  'notice':'Diff may contain secrets. Internal evidence only; do not copy into test report.'}
        total = sum(len(entry['diff'].encode('utf-8')) for pr in result['projects']
                    for entry in pr['selection'])
        if total > args.max_diff_bytes:
            raise ValueError('Diff size '+str(total)+' exceeds --max-diff-bytes; narrow selection or raise limit')
        out = Path(args.output)
        out.parent.mkdir(parents=True,exist_ok=True)
        if out.exists():
            raise ValueError('Refuse to overwrite evidence: ' + str(out))
        out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(str(out.resolve()))
        return 0
    except (ValueError,KeyError,OSError) as exc:
        print('ERROR: '+str(exc),file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

