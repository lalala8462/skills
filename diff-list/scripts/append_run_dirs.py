#!/usr/bin/env python3
"""Append exact per-run clone directories to the report. Directories are never removed."""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

START = '<!-- diff-list-generated-clones:start -->'
END = '<!-- diff-list-generated-clones:end -->'
HEADER = '**本次创建的分析仓库目录（请后续手动删除）：**'


def runs_root():
    return (Path(os.getenv('LOCALAPPDATA') or tempfile.gettempdir()) / 'diff-list' / 'runs').resolve()


def checked_run_dir(clone):
    clone_path = Path(clone).resolve()
    run_dir = clone_path.parent
    if (clone_path.name != 'repo' or run_dir.parent != runs_root() or
            not run_dir.name.startswith('run-') or not (clone_path/'.git').is_dir()):
        raise ValueError('Not a diff-list isolated clone: ' + str(clone))
    return run_dir


def input_clones(source):
    payload = json.loads(Path(source).read_text(encoding='utf-8-sig'))
    if not isinstance(payload, dict):
        raise ValueError('Evidence JSON must be an object')
    if isinstance(payload.get('analysis_clone'), str):
        return [payload['analysis_clone']]
    projects = payload.get('projects')
    if isinstance(projects, list):
        return [entry.get('analysis_clone',entry.get('repository')) for entry in projects
                if isinstance(entry, dict) and isinstance(entry.get('analysis_clone',entry.get('repository')), str)]
    raise ValueError('Evidence JSON has no analysis clone: ' + str(source))


def append(report, sources, clones):
    values = list(clones)
    for source in sources:
        values.extend(input_clones(source))
    dirs = sorted({str(checked_run_dir(value)) for value in values}, key=str.casefold)
    if not dirs:
        raise ValueError('No clone directories given')
    report = Path(report)
    text = report.read_text(encoding='utf-8-sig')
    if START in text or END in text:
        raise ValueError('Clone directory list already present; do not append twice')
    if '## 6. 验证情况' not in text:
        raise ValueError('Report lacks verification section')
    block = '\n\n' + START + '\n' + HEADER + '\n'
    block += ''.join('- \x60{}\x60\n'.format(directory) for directory in dirs)
    block += END + '\n'
    with report.open('a', encoding='utf-8', newline='\n') as out:
        out.write(block)
    return dirs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report')
    parser.add_argument('--source', action='append', default=[])
    parser.add_argument('--clone', action='append', default=[])
    args = parser.parse_args(argv)
    try:
        for path in append(args.report, args.source, args.clone):
            print('Manual cleanup later: ' + path)
        return 0
    except (ValueError, OSError, UnicodeError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
