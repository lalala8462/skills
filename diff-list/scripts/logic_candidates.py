#!/usr/bin/env python3
"""Java 8 AST-based change candidates: syntax/risk hints, never verified semantics."""
import argparse
import base64
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from repo_access import prepare_repo
from collect_git import collect

PRIORITIES = {'STATE': 90, 'CONCURRENCY': 90, 'TRANSACTION': 88,
              'EXCEPTION': 78, 'RETRY': 75, 'CONFIG': 65, 'BRANCH': 60}
HINTS = {
    'STATE': '核对状态变更前置条件、终态覆盖、落库与通知是否一致',
    'CONCURRENCY': '核对重复请求、锁作用域、释放与并发执行结果',
    'TRANSACTION': '核对事务边界、回滚和部分成功后的数据一致性',
    'EXCEPTION': '核对异常路径、错误映射、未知结果和补偿动作',
    'RETRY': '核对重试次数、时序、幂等与原订单号复用',
    'CONFIG': '核对配置键、默认值、作用域及废弃/迁移情况',
    'BRANCH': '核对新增/删除的条件边界、默认分支与实际副作用',
}


def safe_detail(text):
    """Redact literal values; never use the output as ground-truth source evidence."""
    text = re.sub(r'"(?:\\.|[^"\\])*"', '"<literal>"', text)
    text = re.sub(r"'(?:\\.|[^'\\])*'", "'<literal>'", text)
    return text[:180]


def run_scan(classpath, source):
    if not source.strip():
        return [], None
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.java', delete=False) as file:
        name = file.name
        file.write(source)
    try:
        run = subprocess.run(['java', '-cp', classpath, 'JavaLogicAst', name],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             encoding='utf-8', errors='replace', check=False)
        if run.returncode != 0:
            return None, ('AST parser failed: ' + run.stderr.strip())[:350]
        records = []
        for line in run.stdout.splitlines():
            fields = line.split('\t')
            if len(fields) != 5:
                return None, 'Invalid AST record format'
            method, category, detail, fingerprint, line_number = fields
            records.append({'method': base64.b64decode(method).decode('utf-8'),
                            'category': category,
                            'detail': safe_detail(base64.b64decode(detail).decode('utf-8')),
                            'hash': fingerprint, 'line': int(line_number)})
        return records, None
    finally:
        Path(name).unlink(missing_ok=True)


def compare_records(path, before, after):
    """Multiset AST diff (stable method/category/description key, hash captures body)."""
    buckets = defaultdict(lambda: [[], []])
    for side, rows in enumerate((before, after)):
        for row in rows:
            key = (row['method'], row['category'], row['detail'])
            buckets[key][side].append(row)
    out = []
    for (method, category, detail), (old, new) in sorted(buckets.items()):
        old_counts = Counter(x['hash'] for x in old)
        new_counts = Counter(x['hash'] for x in new)
        removed = list((old_counts - new_counts).elements())
        added = list((new_counts - old_counts).elements())
        if not removed and not added:
            continue
        kind = 'modified' if removed and added else ('removed' if removed else 'added')
        score = PRIORITIES.get(category, 50)
        if category == 'BRANCH' and re.search(r'(status|state|amount|money|refund|pay|lock|retry)',
                                               detail, re.I):
            score = 82
        out.append({'path': path, 'method': method, 'category': category,
                    'change': kind, 'description': detail,
                    'priority': 'high' if score >= 75 else 'medium',
                    'priority_score': score, 'risk_hint': HINTS.get(category, ''),
                    'old_lines': [x['line'] for x in old if x['hash'] in removed][:8],
                    'new_lines': [x['line'] for x in new if x['hash'] in added][:8],
                    'actual_behavior_verified': False, 'binding_verified': False})
    return out


def extract_candidates(pairs):
    source_file = Path(__file__).resolve().parent / 'java' / 'JavaLogicAst.java'
    if not source_file.is_file():
        raise ValueError('Missing JDK 8 compatible AST source: ' + str(source_file))
    with tempfile.TemporaryDirectory(prefix='diff-list-ast-') as tmp:
        compile_run = subprocess.run(['javac', '-source', '8', '-target', '8', '-encoding', 'UTF-8',
                                      '-d', tmp, str(source_file)], capture_output=True,
                                     text=True, encoding='utf-8', errors='replace', check=False)
        if compile_run.returncode:
            raise ValueError('JDK compiler unavailable or AST compilation failed: ' + compile_run.stderr[:600])
        findings, skipped = [], []
        for pair in pairs:
            old, old_error = run_scan(tmp, pair['before'])
            new, new_error = run_scan(tmp, pair['after'])
            if old_error or new_error:
                skipped.append({'path': pair['path'], 'reason': old_error or new_error})
                continue
            findings.extend(compare_records(pair['path'], old, new))
        findings.sort(key=lambda x: (-x['priority_score'], x['path'], x['method'], x['category'], x['description']))
        return findings, skipped


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', required=True, help='GitHub HTTPS repository URL')
    p.add_argument('--rev', action='append', help='Selected commit SHA, repeat for multiple')
    p.add_argument('--base', help='Explicit range base')
    p.add_argument('--head', help='Explicit range head')
    p.add_argument('--cache-dir')
    p.add_argument('--output', required=True, help='Sensitive internal candidates JSON')
    args = p.parse_args(argv)
    try:
        if bool(args.rev) == bool(args.base or args.head):
            raise ValueError('Specify either --rev or both --base and --head')
        if not args.rev and not (args.base and args.head):
            raise ValueError('A range must include --base and --head')
        root, name = prepare_repo(args.repo, args.cache_dir)
        project = {'name': name, 'path': str(root)}
        if args.rev:
            project['revisions'] = [({'rev': v.rsplit(':', 1)[0], 'parent': int(v.rsplit(':', 1)[1])}
                                     if ':' in v and v.rsplit(':', 1)[1].isdigit() else v)
                                    for v in args.rev]
        else:
            project['range'] = {'base': args.base, 'head': args.head}
        evidence = collect(project, capture_java=True)
        effect = evidence['net_effect']
        pairs = effect.pop('java_sources', [])
        skipped = effect.pop('java_skipped', [])
        if effect['status'] == 'review_required':
            findings = []
            skipped.append({'path': '*', 'reason': 'selected commits cannot be composed; manual review required'})
        else:
            findings, errors = extract_candidates(pairs)
            skipped.extend(errors)
        payload = {'schema_version': 1, 'repository': args.repo, 'project': name,
                   'selection': [{k:v for k,v in entry.items() if k not in ('diff','files')}
                                 for entry in evidence['selection']],
                   'net_status': effect['status'], 'net_reason': effect['reason'],
                   'files_analyzed': len(pairs), 'candidates': findings,
                   'skipped': skipped, 'review_required': bool(skipped) or effect['status']=='review_required',
                   'note': 'AST syntax-only candidates, not Java type binding or verified business behavior.'}
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('x', encoding='utf-8') as out:
            json.dump(payload, out, ensure_ascii=False, indent=2)
            out.write('\n')
        print(str(target.resolve()))
        return 0
    except (ValueError, OSError, UnicodeError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
