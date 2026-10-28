#!/usr/bin/env python3
"""Java 8 AST-based change candidates: syntax/risk hints, never verified semantics."""
import argparse
import base64
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from repo_access import prepare_repo
from collect_git import collect
from config_candidates import extract_config_candidates

PRIORITIES = {'STATE': 90, 'CONCURRENCY': 90, 'TRANSACTION': 88,
              'EXCEPTION': 78, 'RETRY': 75, 'CONFIG': 65, 'BRANCH': 60,
              'CALL': 62, 'EXPRESSION': 62, 'DISCARDED_CALL': 60}
HINTS = {
    'STATE': '核对状态变更前置条件、终态覆盖、落库与通知是否一致',
    'CONCURRENCY': '核对重复请求、锁作用域、释放与并发执行结果',
    'TRANSACTION': '核对事务边界、回滚和部分成功后的数据一致性',
    'EXCEPTION': '核对异常路径、错误映射、未知结果和补偿动作',
    'RETRY': '核对重试次数、时序、幂等与原订单号复用',
    'CONFIG': '核对配置键、默认值、作用域及废弃/迁移情况',
    'BRANCH': '核对新增/删除的条件边界、默认分支与实际副作用',
    'CALL': '核对目标方法、调用顺序、参数映射与实际副作用',
    'EXPRESSION': '核对计算结果、单位精度、返回值及赋值边界',
    'DISCARDED_CALL': '调用作为独立语句；核实目标方法是否返回有意义的结果或仅依赖副作用，检查嵌套及递归处理',
}


def safe_detail(text):
    """Redact literal values; never use the output as ground-truth source evidence."""
    text = re.sub(r'"(?:\\.|[^"\\])*"', '"<literal>"', text)
    text = re.sub(r"'(?:\\.|[^'\\])*'", "'<literal>'", text)
    return text[:180]


def jdk_tools():
    javac = shutil.which('javac')
    if not javac:
        raise ValueError('JDK compiler unavailable: javac not found on PATH')
    javac = Path(javac).resolve()
    java = javac.with_name('java.exe' if os.name == 'nt' else 'java')
    if not java.is_file():
        raise ValueError('Java runtime not found beside javac: ' + str(java))
    tools_jar = javac.parent.parent / 'lib' / 'tools.jar'
    return str(javac), str(java), str(tools_jar) if tools_jar.is_file() else None


def run_scan(java, classpath, source):
    if not source.strip():
        return [], None
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.java', delete=False) as file:
        name = file.name
        file.write(source)
    try:
        run = subprocess.run([java, '-cp', classpath, 'JavaLogicAst', name],
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


def align_scopes(before, after):
    """Map uniquely identifiable class/method renames; ambiguous matches remain separate."""
    def methods(rows):
        result = defaultdict(dict)
        for row in rows:
            if row['category'] == 'SCOPE' and '#' in row['method']:
                cls, method = row['method'].rsplit('#', 1)
                result[cls][method] = row['hash']
        return result
    olds, news = methods(before), methods(after)
    # Exact class names always win; only unique positive matches permit class rename.
    mapped = {n: n for n in news if n in olds}
    old_available = [k for k in olds if k not in mapped.values()]
    new_available = [k for k in news if k not in mapped]
    scores = {}
    for n in new_available:
        for o in old_available:
            common_names = len(set(news[n]) & set(olds[o]))
            common_bodies = len(set(news[n].values()) & set(olds[o].values()))
            scores[(n, o)] = 2*common_names + common_bodies
    for n in new_available:
        candidates = [(score, o) for (candidate, o), score in scores.items()
                      if candidate == n and score > 0]
        if not candidates:
            continue
        best = max(score for score, _ in candidates)
        winners = [o for score, o in candidates if score == best]
        if len(winners) != 1:
            continue
        o = winners[0]
        competition = [(score, candidate) for (candidate, old), score in scores.items()
                       if old == o and score > 0]
        if len([candidate for score, candidate in competition if score == max(x[0] for x in competition)]) == 1:
            mapped[n] = o
    # For each mapped class, match uniquely identical method bodies (method rename).
    method_map = {}
    for new_cls, old_cls in mapped.items():
        used_old = set()
        for method in news[new_cls]:
            if method in olds[old_cls]:
                method_map[new_cls + '#' + method] = old_cls + '#' + method
                used_old.add(method)
        for method, fingerprint in news[new_cls].items():
            if new_cls + '#' + method in method_map:
                continue
            matches = [name for name, body in olds[old_cls].items()
                       if name not in used_old and body == fingerprint]
            reverse = [name for name, body in news[new_cls].items()
                       if name != method and body == fingerprint]
            if len(matches) == 1 and not reverse:
                method_map[new_cls + '#' + method] = old_cls + '#' + matches[0]
                used_old.add(matches[0])
    return mapped, method_map


def compare_records(path, before, after):
    """AST multiset diff with conservative class/method rename normalization."""
    class_map, method_map = align_scopes(before, after)
    buckets = defaultdict(lambda: [[], []])
    for side, rows in enumerate((before, after)):
        for row in rows:
            if row['category'] == 'SCOPE':
                continue
            scope = row['method']
            if side == 1 and '#' in scope:
                cls, method = scope.rsplit('#', 1)
                scope = method_map.get(scope, class_map.get(cls, cls) + '#' + method)
            key = (scope, row['category'], row['detail'])
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
        if category in ('BRANCH', 'CALL', 'EXPRESSION') and re.search(
                r'(status|state|amount|money|refund|pay|paid|charge|debit|credit|balance|transfer|lock|retry|order)',
                method + ' ' + detail, re.I):
            score = max(82, score)
        out.append({'path': path, 'method': method, 'category': category,
                    'change': kind, 'description': detail,
                    'priority': 'high' if score >= 75 else 'medium',
                    'priority_score': score, 'risk_hint': HINTS.get(category, ''),
                    'old_lines': [x['line'] for x in old if x['hash'] in removed][:8],
                    'new_lines': [x['line'] for x in new if x['hash'] in added][:8],
                    'actual_behavior_verified': False, 'binding_verified': False})
    return out


def extract_candidates(pairs):
    if not pairs:
        return [], []
    source_file = Path(__file__).resolve().parent / 'java' / 'JavaLogicAst.java'
    if not source_file.is_file():
        raise ValueError('Missing JDK 8 compatible AST source: ' + str(source_file))
    javac, java, tools_jar = jdk_tools()
    with tempfile.TemporaryDirectory(prefix='diff-list-ast-') as tmp:
        classpath = os.pathsep.join([tmp, tools_jar] if tools_jar else [tmp])
        compile_run = subprocess.run([javac, '-source', '8', '-target', '8', '-encoding', 'UTF-8',
                                      '-cp', classpath, '-d', tmp, str(source_file)], capture_output=True,
                                     text=True, encoding='utf-8', errors='replace', check=False)
        if compile_run.returncode:
            raise ValueError('JDK compiler unavailable or AST compilation failed: ' + compile_run.stderr[:600])
        findings, skipped = [], []
        for pair in pairs:
            old, old_error = run_scan(java, classpath, pair['before'])
            new, new_error = run_scan(java, classpath, pair['after'])
            if old_error or new_error:
                skipped.append({'path': pair['path'], 'reason': old_error or new_error})
                continue
            findings.extend(compare_records(pair['path'], old, new))
        findings.sort(key=lambda x: (-x['priority_score'], x['path'], x['method'], x['category'], x['description']))
        return findings, skipped


def group_candidates(findings, limit=12):
    """Group change hints by source method, without asserting call bindings or behavior."""
    groups = defaultdict(list)
    for item in findings:
        if item.get('method'):
            groups[(item['path'], item['method'])].append(item)
    result = []
    for (path, method), items in sorted(groups.items()):
        categories = Counter(item['category'] for item in items)
        calls = sorted({item['description'][5:] for item in items
                        if item['category'] == 'CALL' and item['description'].startswith('call ')})
        result.append({'path': path, 'method': method, 'candidate_count': len(items),
                       'categories': dict(sorted(categories.items())),
                       'changed_call_names': calls[:limit],
                       'omitted_call_names': max(0, len(calls) - limit),
                       'binding_verified': False})
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', required=True, help='GitHub or GitLab repository URL')
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
        project = {'name': name, 'path': str(root), 'isolated_no_checkout': True}
        if args.rev:
            project['revisions'] = [({'rev': v.rsplit(':', 1)[0], 'parent': int(v.rsplit(':', 1)[1])}
                                     if ':' in v and v.rsplit(':', 1)[1].isdigit() else v)
                                    for v in args.rev]
        else:
            project['range'] = {'base': args.base, 'head': args.head}
        evidence = collect(project, capture_java=True, capture_config=True)
        effect = evidence['net_effect']
        pairs = effect.pop('java_sources', [])
        skipped = effect.pop('java_skipped', [])
        config_pairs = effect.pop('config_sources', [])
        skipped.extend(effect.pop('config_skipped', []))
        if effect['status'] == 'review_required':
            findings = []
            skipped.append({'path': '*', 'reason': 'selected commits cannot be composed; manual review required'})
        else:
            findings, errors = extract_candidates(pairs)
            skipped.extend(errors)
            config_findings, config_errors = extract_config_candidates(config_pairs)
            findings.extend(config_findings)
            findings.sort(key=lambda x: (-x['priority_score'], x['path'], x['category'], x['description']))
            skipped.extend(config_errors)
        payload = {'schema_version': 1, 'repository': args.repo, 'project': name,
                   'selection': [{k:v for k,v in entry.items() if k not in ('diff','files')}
                                 for entry in evidence['selection']],
                   'net_status': effect['status'], 'net_reason': effect['reason'],
                   'files_analyzed': len(pairs) + len(config_pairs),
                   'java_files_analyzed': len(pairs), 'config_files_analyzed': len(config_pairs),
                   'candidates': findings,
                   'method_groups': group_candidates(findings),
                   'skipped': skipped, 'review_required': bool(skipped) or effect['status']=='review_required',
                   'note': 'AST/configuration change candidates, not verified business behavior or runtime settings.'}
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
