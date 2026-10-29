#!/usr/bin/env python3
"""编译结构化测试点；确定性去重与归属，不推测自然语言的业务等价。"""
import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from coverage_constraints import check_source_coverage

VERSION = '1.3.0'
PURPOSES = ('acceptance', 'core_regression', 'related_regression', 'boundary')
PURPOSE_NAMES = ('需求验收', '既有核心流程回归', '关联回归', '业务规则与边界验证')
RULE_KINDS = {'behavior', 'source', 'selection', 'configuration', 'contract',
              'state', 'amount', 'idempotency', 'boundary'}
ASSERTIONS = {'equals', 'not_equals', 'source_is', 'in', 'not_in', 'exists',
              'absent', 'unchanged', 'count', 'state'}
UNARY = {'missing', 'is_null', 'is_empty', 'nonempty', 'exists'}
BINARY = {'eq', 'ne', 'gt', 'ge', 'lt', 'le', 'in', 'not_in', 'sequence'}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def identity(prefix, value):
    return prefix + hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()[:24]


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + ' requires a nonempty string')
    return value.strip()


def unique(values):
    return [json.loads(k) for k in sorted({canonical(v) for v in values})]


def strings(values, label, required=False):
    if not isinstance(values, list) or (required and not values):
        raise ValueError(label + ' requires an array' + (' with values' if required else ''))
    return sorted({text(v, label) for v in values})


def fields(item, allowed, required, label):
    if not isinstance(item, dict) or set(item) - set(allowed) or not set(required) <= set(item):
        raise ValueError(label + ' has missing or unsupported fields')


def conditions(value):
    if not isinstance(value, list):
        raise ValueError('conditions must be an array (empty means unconditional)')
    result = []
    for item in value:
        fields(item, ('subject', 'operator', 'value', 'other'), ('subject', 'operator'), 'condition')
        row = {'subject': text(item['subject'], 'condition.subject'), 'operator': item['operator']}
        op = row['operator']
        if op in UNARY:
            if 'value' in item or 'other' in item:
                raise ValueError('unary condition cannot contain value/other')
        elif op in BINARY:
            if ('value' in item) == ('other' in item):
                raise ValueError('binary condition requires exactly one value or other role')
            if 'other' in item:
                if op not in ('eq', 'ne', 'gt', 'ge', 'lt', 'le'):
                    raise ValueError('other role is unsupported for ' + op)
                row['other'] = text(item['other'], 'condition.other')
                if op in ('eq', 'ne') and row['other'] < row['subject']:
                    row['subject'], row['other'] = row['other'], row['subject']
            else:
                val = copy.deepcopy(item['value'])
                if op in ('in', 'not_in', 'sequence'):
                    if not isinstance(val, list) or not val:
                        raise ValueError(op + ' requires a nonempty array')
                    if op != 'sequence':
                        val = unique(val)
                row['value'] = val
        else:
            raise ValueError('unknown condition operator: ' + str(op))
        result.append(row)
    return unique(result)


def expectation(value):
    fields(value, ('kind', 'value'), ('kind', 'value'), 'expected constraint')
    if value['kind'] not in ASSERTIONS:
        raise ValueError('unknown assertion kind: ' + str(value['kind']))
    result = copy.deepcopy(value)
    if result['kind'] in ('in', 'not_in'):
        if not isinstance(result['value'], list) or not result['value']:
            raise ValueError('set assertion requires a nonempty array')
        result['value'] = unique(result['value'])
    canonical(result)
    return result


def entry(value):
    fields(value, ('kind', 'target'), ('kind', 'target'), 'entry')
    if value['kind'] not in ('http', 'method', 'event', 'job', 'rpc', 'ui'):
        raise ValueError('unsupported entry kind')
    return {'kind': value['kind'], 'target': text(value['target'], 'entry.target')}


def registry(rows, label):
    if not isinstance(rows, list):
        raise ValueError(label + ' requires an array')
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(label + ' entries must be objects')
        key = text(row.get('id'), label + '.id')
        if key in result:
            raise ValueError('duplicate ' + label + ' id: ' + key)
        result[key] = copy.deepcopy(row)
        result[key]['id'] = key
    return result


def _check_data(values, predicates):
    """仅校验数据组明确提供的角色；未提供的角色由测试准备步骤承担。"""
    for p in predicates:
        subject, op = p['subject'], p['operator']
        if subject not in values:
            continue
        actual = values[subject]
        if op == 'missing':
            raise ValueError('data group contradicts missing role: ' + subject)
        if op in ('exists', 'is_null', 'is_empty', 'nonempty'):
            ok = (op == 'exists' or (op == 'is_null' and actual is None)
                  or (op == 'is_empty' and actual in ('', [], {}))
                  or (op == 'nonempty' and actual is not None and actual not in ('', [], {})))
        else:
            if 'other' in p and p['other'] not in values:
                continue
            expected = values[p['other']] if 'other' in p else p['value']
            if op in ('eq', 'ne', 'sequence'):
                equal = canonical(actual) == canonical(expected)
                ok = not equal if op == 'ne' else equal
            elif op in ('in', 'not_in'):
                included = canonical(actual) in {canonical(v) for v in expected}
                ok = included if op == 'in' else not included
            else:
                if type(actual) not in (int, float) or type(expected) not in (int, float):
                    raise ValueError('numeric comparison needs numeric values: ' + subject)
                ok = {'gt': actual > expected, 'ge': actual >= expected,
                      'lt': actual < expected, 'le': actual <= expected}[op]
        if not ok:
            raise ValueError('data group contradicts condition: ' + subject)


def compile_plan(raw, analysis=None):
    """相同结构输入产生相同目录；不读取模型标题或实例编号来决定身份。"""
    fields(raw, ('version', 'analysis_id', 'requirements', 'rules', 'impacts', 'equivalences', 'candidates'),
           ('version', 'analysis_id', 'requirements', 'rules', 'impacts', 'candidates'), 'test point plan')
    if type(raw['version']) != int or raw['version'] != 1:
        raise ValueError('unsupported test point plan version')
    plan = copy.deepcopy(raw)
    analysis_id = text(plan['analysis_id'], 'analysis_id')
    rules = registry(plan['rules'], 'rule')
    requirements = registry(plan['requirements'], 'requirement')
    impacts = registry(plan['impacts'], 'impact')
    candidates = registry(plan['candidates'], 'candidate')
    equivalents = registry(plan.get('equivalences', []), 'equivalence')
    projects, facts, obligations = {}, {}, {}
    if analysis is not None:
        from analysis_engine import VERSION as ANALYZER_VERSION
        if type(analysis.get('schema_version'))!=int or analysis['schema_version']!=1 or analysis.get('analyzer_version')!=ANALYZER_VERSION:
            raise ValueError('analysis must be regenerated with the current analyzer')
        if analysis.get('analysis_id') != analysis_id:
            raise ValueError('test points belong to a different analysis')
        for project in analysis['projects']:
            name = project['name']; projects[name] = project
            facts.update((f['id'], (name, f)) for f in project['facts'])
            obligations.update((o['id'], (name, o)) for o in project['obligations'])

    def refs(row, project, required=False):
        row['fact_ids'] = strings(row.get('fact_ids', []), 'fact_ids', required)
        row['evidence_ids'] = strings(row.get('evidence_ids', []), 'evidence_ids', required)
        if analysis is not None:
            if project not in projects or any(f not in facts or facts[f][0] != project for f in row['fact_ids']):
                raise ValueError('unknown or wrong-project source fact')
            known = {e for f in row['fact_ids'] for e in facts[f][1]['evidence_ids']}
            if not set(row['evidence_ids']) <= known or (required and not known):
                raise ValueError('evidence must belong to the cited source facts')

    rule_keys = {}
    for key, rule in rules.items():
        fields(rule, ('id', 'project', 'kind', 'subject', 'operation', 'core', 'fact_ids', 'evidence_ids'),
               ('id', 'project', 'kind', 'subject', 'operation'), 'rule')
        if rule['kind'] not in RULE_KINDS or type(rule.get('core', False)) != bool:
            raise ValueError('invalid rule kind or core flag')
        for field in ('project', 'subject', 'operation'):
            rule[field] = text(rule[field], 'rule.' + field)
        rule['core'] = rule.get('core', False)
        refs(rule, rule['project'])
        if rule['fact_ids'] and not rule['evidence_ids']:
            raise ValueError('source-backed rule requires evidence')
        rule_keys[key] = {k: rule[k] for k in ('project', 'kind', 'subject', 'operation')}
        if analysis is not None and rule['project'] not in projects:
            raise ValueError('rule project is outside the explicit analysis')
    for req in requirements.values():
        fields(req, ('id', 'source', 'rule_ids'), ('id', 'source', 'rule_ids'), 'requirement')
        source = req['source']
        fields(source, ('kind', 'reference', 'statement'), ('kind', 'reference', 'statement'), 'requirement.source')
        if source['kind'] not in ('user_input', 'protocol', 'confirmed_constraint', 'reviewed_change'):
            raise ValueError('source code or commit message alone cannot establish acceptance requirements')
        for field in ('reference', 'statement'):
            source[field] = text(source[field], 'requirement.source.' + field)
        req['rule_ids'] = strings(req['rule_ids'], 'requirement.rule_ids', True)
        if any(r not in rules for r in req['rule_ids']):
            raise ValueError('unknown requirement rule')
        if source['kind'] == 'reviewed_change':
            if analysis is None:
                raise ValueError('reviewed change requires current analysis')
            if any(not rules[r]['fact_ids'] or not rules[r]['evidence_ids'] for r in req['rule_ids']):
                raise ValueError('reviewed change requires evidenced rules')
    for impact in impacts.values():
        fields(impact, ('id', 'project', 'rule_ids', 'fact_ids', 'evidence_ids', 'description'),
               ('id', 'project', 'rule_ids', 'description'), 'impact')
        impact['description'] = text(impact['description'], 'impact.description')
        impact['rule_ids'] = strings(impact['rule_ids'], 'impact.rule_ids', True)
        if any(r not in rules or rules[r]['project'] != impact['project'] for r in impact['rule_ids']):
            raise ValueError('impact has unknown or wrong-project rule')
        refs(impact, impact['project'], True)

    equivalence_map = {}
    for eq in equivalents.values():
        fields(eq, ('id', 'rule_id', 'entry', 'alternatives', 'canonical', 'fact_ids', 'evidence_ids', 'reason'),
               ('id', 'rule_id', 'entry', 'alternatives', 'canonical', 'reason'), 'equivalence')
        rule = rules.get(eq['rule_id'])
        if rule is None:
            raise ValueError('unknown equivalence rule')
        eq['entry'] = entry(eq['entry']); eq['canonical'] = conditions(eq['canonical'])
        eq['reason'] = text(eq['reason'], 'equivalence.reason')
        refs(eq, rule['project'], True)
        if not isinstance(eq['alternatives'], list) or len(eq['alternatives']) < 2:
            raise ValueError('equivalence requires at least two explicit alternatives')
        eq['alternatives'] = unique([conditions(c) for c in eq['alternatives']])
        if eq['canonical'] not in eq['alternatives']:
            raise ValueError('canonical conditions must be an explicit alternative')
        scope = canonical([rule_keys[eq['rule_id']], eq['entry']])
        for alternative in eq['alternatives']:
            k = (scope, canonical(alternative))
            target = canonical(eq['canonical'])
            if k in equivalence_map and equivalence_map[k] != target:
                raise ValueError('conflicting equivalence declarations')
            equivalence_map[k] = target
    for (scope, _), target in equivalence_map.items():
        if equivalence_map.get((scope, target), target) != target:
            raise ValueError('chained or cyclic equivalence declarations are unsupported')

    points, candidate_map, acceptance_seen = {}, {}, set()
    for key, candidate in candidates.items():
        fields(candidate, ('id', 'rule_id', 'entry', 'conditions', 'observation', 'expected', 'purposes',
                           'obligation_ids', 'data_groups', 'source_bindings'),
               ('id', 'rule_id', 'entry', 'conditions', 'observation', 'expected', 'purposes', 'obligation_ids'), 'candidate')
        rule = rules.get(candidate['rule_id'])
        if rule is None:
            raise ValueError('unknown candidate rule')
        candidate['entry'] = entry(candidate['entry'])
        candidate['conditions'] = conditions(candidate['conditions'])
        fixed_values={p['subject']:p['value'] for p in candidate['conditions'] if p['operator']=='eq' and 'value' in p}
        _check_data(fixed_values,candidate['conditions'])
        fields(candidate['observation'], ('surface', 'target'), ('surface', 'target'), 'observation')
        candidate['observation'] = {k: text(v, 'observation.' + k) for k, v in candidate['observation'].items()}
        candidate['expected'] = expectation(candidate['expected'])
        candidate['obligation_ids'] = strings(candidate['obligation_ids'], 'obligation_ids')
        if analysis is not None and any(o not in obligations or obligations[o][0] != rule['project']
                                        for o in candidate['obligation_ids']):
            raise ValueError('unknown or wrong-project coverage obligation')
        if analysis is not None and any(not set(rule['fact_ids']) & set(obligations[o][1]['fact_ids'])
                                        for o in candidate['obligation_ids']):
            raise ValueError('coverage obligation is unrelated to the registered rule facts')
        if not isinstance(candidate['purposes'], list) or not candidate['purposes']:
            raise ValueError('candidate needs qualified purposes')
        for basis in candidate['purposes']:
            fields(basis, ('kind', 'requirement_id', 'impact_id'), ('kind',), 'purpose basis')
            purpose = basis['kind']
            if purpose == 'acceptance':
                req = requirements.get(basis.get('requirement_id'))
                if not req or candidate['rule_id'] not in req['rule_ids'] or 'impact_id' in basis:
                    raise ValueError('acceptance requires a matching requirement')
                acceptance_seen.add((req['id'], candidate['rule_id']))
            elif purpose in ('core_regression', 'related_regression'):
                impact = impacts.get(basis.get('impact_id'))
                if not impact or candidate['rule_id'] not in impact['rule_ids'] or 'requirement_id' in basis:
                    raise ValueError('regression requires a matching evidenced impact')
                if purpose == 'core_regression' and not rule['core']:
                    raise ValueError('core regression requires a registered core rule')
            elif purpose == 'boundary':
                if not candidate['obligation_ids'] or set(basis) != {'kind'}:
                    raise ValueError('boundary requires extracted coverage obligations')
            else:
                raise ValueError('unknown test purpose: ' + str(purpose))
        candidate['purposes'] = unique(candidate['purposes'])
        if not rule['fact_ids'] and not any(b['kind'] == 'acceptance' for b in candidate['purposes']):
            raise ValueError('non-acceptance rule requires source facts')
        scope = canonical([rule_keys[candidate['rule_id']], candidate['entry']])
        effective = json.loads(equivalence_map.get((scope, canonical(candidate['conditions'])), canonical(candidate['conditions'])))
        if analysis is not None:
            items=[obligations[o][1] for o in candidate['obligation_ids']]
            source_facts={k:v[1] for k,v in facts.items() if v[0]==rule['project']}
            check_source_coverage(candidate,items,source_facts)
            check_source_coverage(dict(candidate,conditions=effective),items,source_facts)
        signature = [rule_keys[candidate['rule_id']], candidate['entry'], effective,
                     candidate['observation'], candidate['expected']['kind']]
        identifier = identity('TP-', signature)
        candidate_map[key] = identifier
        if identifier not in points:
            points[identifier] = dict(id=identifier, rule=rule_keys[candidate['rule_id']], entry=candidate['entry'],
                conditions=effective, observation=candidate['observation'], expected=candidate['expected'],
                candidate_ids=[], rule_ids=[], purposes=[], obligation_ids=[], condition_variants=[], data_groups=[])
        point = points[identifier]
        if canonical(point['expected']) != canonical(candidate['expected']):
            raise ValueError('conflicting expected constraints for ' + identifier + ': ' + key)
        point['candidate_ids'].append(key); point['rule_ids'].append(candidate['rule_id'])
        point['purposes'] += candidate['purposes']; point['obligation_ids'] += candidate['obligation_ids']
        point['condition_variants'].append(candidate['conditions'])
        data_groups = candidate.get('data_groups', [])
        if not isinstance(data_groups, list) or any(not isinstance(v, dict) for v in data_groups):
            raise ValueError('data_groups must be an array of role/value objects')
        candidate['data_groups'] = unique(data_groups)
        for values in candidate['data_groups']:
            _check_data(values, candidate['conditions'])
            group = {'values': values, 'conditions': candidate['conditions']}
            point['data_groups'].append(dict(id=identity('DATA-', group), **group))
    for req in requirements.values():
        for rule_id in req['rule_ids']:
            if (req['id'], rule_id) not in acceptance_seen:
                raise ValueError('requirement lacks acceptance coverage: ' + req['id'] + '/' + rule_id)
    for point in points.values():
        for field in ('candidate_ids', 'rule_ids', 'purposes', 'obligation_ids', 'condition_variants', 'data_groups'):
            point[field] = unique(point[field])
        point['purpose'] = min((b['kind'] for b in point['purposes']), key=PURPOSES.index)
    for name, rows in (('rules', rules), ('requirements', requirements), ('impacts', impacts),
                       ('equivalences', equivalents), ('candidates', candidates)):
        plan[name] = [rows[k] for k in sorted(rows)]
    result = dict(schema_version=1, compiler_version=VERSION, analysis_id=analysis_id, plan=plan,
                  points=sorted(points.values(), key=lambda p: p['id']),
                  candidate_map=dict(sorted(candidate_map.items())))
    # 目录与输入计划不能共享可变对象，否则篡改单个断言可能同时改变重算依据。
    return json.loads(canonical(result))


def verify_catalog(catalog, analysis=None):
    if not isinstance(catalog, dict) or catalog.get('compiler_version') != VERSION:
        raise ValueError('unsupported test point catalog/compiler version')
    expected = compile_plan(catalog.get('plan'), analysis)
    if canonical(catalog) != canonical(expected):
        raise ValueError('test point catalog differs from deterministic compilation')
    return expected


def validate_cases(data, analysis=None):
    """校验唯一承载、全部数据组、结构断言及原始覆盖要求的保留。"""
    fields(data, ('schema_version','test_point_catalog','purpose_cases'),
           ('schema_version','test_point_catalog','purpose_cases'), 'current four-purpose cases')
    if type(data['schema_version'])!=int or data['schema_version'] != 2:
        raise ValueError('current four-purpose cases require schema_version=2')
    catalog = verify_catalog(data.get('test_point_catalog'), analysis)
    groups = data.get('purpose_cases')
    if not isinstance(groups, dict) or set(groups) != set(PURPOSES):
        raise ValueError('purpose_cases requires exactly four purpose arrays')
    points = {p['id']: p for p in catalog['points']}
    seen, result = set(), []
    for purpose in PURPOSES:
        if not isinstance(groups[purpose], list):
            raise ValueError('purpose case group must be an array')
        for case in groups[purpose]:
            case_fields=('case_id','title','preconditions','steps','expected','observable_evidence','coverage_links','test_point_links')
            fields(case,case_fields,case_fields,'case')
            links = case.get('test_point_links')
            if not isinstance(links, list) or not links:
                raise ValueError('case requires test_point_links')
            observations = case.get('observable_evidence', [])
            if not isinstance(observations, list):
                raise ValueError('observable_evidence must be an array')
            coverage_links = case.get('coverage_links', [])
            if not isinstance(coverage_links, list) or any(not isinstance(l, dict) for l in coverage_links):
                raise ValueError('coverage_links must be an array of objects')
            for coverage in coverage_links:
                coverage_fields=('obligation_id','condition','assertion','fact_ids','evidence_ids','observable_evidence_indices')
                fields(coverage,coverage_fields,coverage_fields,'coverage link')
            linked_obligations = {l.get('obligation_id') for l in coverage_links}
            expected_obligations = set()
            for link in links:
                fields(link, ('point_id', 'conditions', 'expected', 'data_group_ids', 'observable_evidence_indices'),
                       ('point_id', 'conditions', 'expected', 'data_group_ids', 'observable_evidence_indices'), 'test point link')
                point = points.get(link['point_id'])
                if not point or point['id'] in seen:
                    raise ValueError('unknown or duplicate test point: ' + str(link['point_id']))
                if point['purpose'] != purpose:
                    raise ValueError('test point is stored under the wrong purpose: ' + point['id'])
                if canonical(link['conditions']) != canonical(point['conditions']) or canonical(link['expected']) != canonical(point['expected']):
                    raise ValueError('test point condition/expected mismatch: ' + point['id'])
                expected_data = {g['id'] for g in point['data_groups']}
                actual_data = strings(link['data_group_ids'], 'data_group_ids')
                if set(actual_data) != expected_data:
                    raise ValueError('missing or unknown test data groups: ' + point['id'])
                indices = link['observable_evidence_indices']
                if not isinstance(indices, list) or not indices or any(type(i) != int or i < 0 or i >= len(observations) for i in indices):
                    raise ValueError('test point requires observable evidence indices')
                for i in indices:
                    observed = observations[i]
                    if (not isinstance(observed, dict) or any(observed.get(k) != v for k, v in point['observation'].items())
                            or canonical(observed.get('constraint')) != canonical(point['expected'])):
                        raise ValueError('observable structured constraint mismatch: ' + point['id'])
                if not set(point['obligation_ids']) <= linked_obligations:
                    raise ValueError('merged test point lost source coverage links: ' + point['id'])
                for obligation in point['obligation_ids']:
                    coverage=next(c for c in coverage_links if c.get('obligation_id')==obligation)
                    if analysis is not None and not set(indices)&set(coverage.get('observable_evidence_indices',[])):
                        raise ValueError('source coverage must observe the corresponding test point: '+obligation)
                expected_obligations.update(point['obligation_ids'])
                seen.add(point['id'])
            if linked_obligations != expected_obligations or len(coverage_links) != len(linked_obligations):
                raise ValueError('coverage links must match the case test points without duplicates')
            result.append(case)
    if seen != set(points):
        raise ValueError('uncovered test points: ' + ', '.join(sorted(set(points) - seen)))
    return result


def render_condition(row):
    names = {'eq': '等于', 'ne': '不等于', 'gt': '大于', 'ge': '大于等于', 'lt': '小于', 'le': '小于等于',
             'in': '属于集合', 'not_in': '不属于集合', 'sequence': '事件顺序为', 'missing': '缺省',
             'is_null': '为 null', 'is_empty': '为空值', 'nonempty': '非空', 'exists': '存在'}
    operand = row.get('other', canonical(row['value']) if 'value' in row else '')
    return '{} {} {}'.format(row['subject'], names[row['operator']], operand).strip()


def case_material(data, case):
    """把固定条件、全部数据组和结构断言写入可读用例，不输出内部编号。"""
    points = {p['id']: p for p in data['test_point_catalog']['points']}
    selected = [points[l['point_id']] for l in case['test_point_links']]
    variants = unique([c for p in selected for c in p['condition_variants']])
    triggers = ['；'.join(render_condition(c) for c in variant) if variant else '无附加条件' for variant in variants]
    datasets = unique([g for p in selected for g in p['data_groups']])
    preparation = '固定触发条件：\n' + '\n'.join('{}. {}'.format(n, c) for n, c in enumerate(triggers, 1))
    if datasets:
        preparation += '\n测试数据（逐组执行）：\n' + '\n'.join(
            '{}. {}；触发条件：{}'.format(n, '；'.join('{}={}'.format(k, canonical(v)) for k, v in sorted(g['values'].items())),
                                      '；'.join(render_condition(c) for c in g['conditions']) or '无附加条件')
            for n, g in enumerate(datasets, 1))
    names = {'equals': '等于', 'not_equals': '不等于', 'source_is': '来源为', 'in': '属于集合',
             'not_in': '不属于集合', 'exists': '存在性为', 'absent': '缺省约束为',
             'unchanged': '保持', 'count': '数量为', 'state': '状态为'}
    assertions = unique(['{}｜{}：{} {}'.format(p['observation']['surface'], p['observation']['target'],
        names[p['expected']['kind']], canonical(p['expected']['value'])) for p in selected])
    return preparation, '固定观察断言：\n' + '\n'.join(assertions)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, help='结构化测试点计划 JSON')
    parser.add_argument('--analysis', required=True, help='固定 analysis.json')
    parser.add_argument('--output', required=True, help='新建测试点目录 JSON')
    args = parser.parse_args(argv)
    try:
        raw = json.loads(Path(args.input).read_text(encoding='utf-8-sig'))
        analysis = json.loads(Path(args.analysis).read_text(encoding='utf-8-sig'))
        catalog = compile_plan(raw, analysis)
        with Path(args.output).open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(catalog, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
        print('TEST POINTS: {} candidates -> {} unique points'.format(len(raw['candidates']), len(catalog['points'])))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
