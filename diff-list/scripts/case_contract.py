#!/usr/bin/env python3
"""当前四类测试用例契约。"""
PURPOSES=('acceptance','core_regression','related_regression','boundary')
PURPOSE_NAMES=('需求验收','既有核心流程回归','关联回归','业务规则与边界验证')
SCHEMA_VERSION=4
REVIEW_SCHEMA_VERSION=3


def empty_cases(analysis_id):
    """生成符合当前契约的空用例骨架，供 Agent 直接填充四类数组。"""
    return dict(schema_version=SCHEMA_VERSION,analysis_id=_text(analysis_id,'analysis_id'),
                purpose_cases={purpose:[] for purpose in PURPOSES})


def _text(value,label):
    if not isinstance(value,str) or not value.strip():
        raise ValueError(label+' requires a nonempty string')
    return value.strip()


def _strings(values,label,nonempty=False):
    if not isinstance(values,list) or (nonempty and not values):
        raise ValueError(label+' requires '+('a nonempty ' if nonempty else 'an ')+'array')
    return sorted({_text(value,label) for value in values})


def _fields(item,allowed,required,label):
    if not isinstance(item,dict) or set(item)-set(allowed) or not set(required)<=set(item):
        raise ValueError(label+' has missing or unsupported fields')


def review_unit_index(review):
    if not isinstance(review,dict) or review.get('review_schema_version')!=REVIEW_SCHEMA_VERSION:
        raise ValueError('review requires review_schema_version={}'.format(REVIEW_SCHEMA_VERSION))
    index={}
    for project in review.get('projects') or []:
        if not isinstance(project,dict):
            raise ValueError('review project must be an object')
        catalog={fact.get('id') for fact in project.get('fact_catalog') or [] if fact.get('id')}
        for unit in project.get('review_units') or []:
            if not isinstance(unit,dict):
                raise ValueError('review unit must be an object')
            identifier=_text(unit.get('id'),'review_unit.id')
            if identifier in index:
                raise ValueError('duplicate review unit: '+identifier)
            fact_ids=_strings(unit.get('fact_ids'),'review_unit.fact_ids',True)
            direct_fact_ids=_strings(unit.get('direct_fact_ids'),'review_unit.direct_fact_ids')
            context_fact_ids=_strings(unit.get('context_fact_ids'),'review_unit.context_fact_ids')
            if set(direct_fact_ids) & set(context_fact_ids):
                raise ValueError('review unit direct/context facts overlap: '+identifier)
            if set(direct_fact_ids) | set(context_fact_ids) != set(fact_ids):
                raise ValueError('review unit direct/context facts do not match fact_ids: '+identifier)
            if not set(fact_ids)<=catalog:
                raise ValueError('review unit references facts outside fact_catalog: '+identifier)
            summary=unit.get('change_summary')
            if not isinstance(summary,list):
                raise ValueError('review unit change_summary must be an array: '+identifier)
            summary_fact_ids=set()
            for row in summary:
                if not isinstance(row,dict) or not isinstance(row.get('fact_id'),str):
                    raise ValueError('review unit change_summary item is invalid: '+identifier)
                summary_fact_ids.add(row['fact_id'])
            if not summary_fact_ids<=set(direct_fact_ids):
                raise ValueError('review unit change_summary references non-direct facts: '+identifier)
            index[identifier]=dict(project=project.get('name'),unit=unit,fact_ids=fact_ids,
                                   direct_fact_ids=direct_fact_ids,context_fact_ids=context_fact_ids)
    if not index:
        raise ValueError('review contains no review units')
    return index


def validate_cases(data,analysis,review):
    """只接受当前 schema_version=4、基于 review_unit_ids 的四类直接用例。"""
    _fields(data,('schema_version','analysis_id','purpose_cases'),
            ('schema_version','analysis_id','purpose_cases'),'test cases')
    if data.get('schema_version')!=SCHEMA_VERSION:
        raise ValueError('test cases require schema_version={}'.format(SCHEMA_VERSION))
    analysis_id=_text(data.get('analysis_id'),'analysis_id')
    if analysis.get('analysis_id')!=analysis_id:
        raise ValueError('test cases belong to a different analysis')
    if review.get('analysis_id')!=analysis_id:
        raise ValueError('test cases belong to a different review')
    units=review_unit_index(review)
    groups=data.get('purpose_cases')
    if not isinstance(groups,dict) or set(groups)!=set(PURPOSES):
        raise ValueError('purpose_cases requires exactly four purpose arrays')
    seen=set();result=[]
    for purpose in PURPOSES:
        if not isinstance(groups[purpose],list):
            raise ValueError('purpose case group must be an array')
        for case in groups[purpose]:
            fields=('case_id','title','preconditions','steps','expected','observable_evidence','review_unit_ids')
            _fields(case,fields,fields,'case')
            case_id=_text(case['case_id'],'case_id')
            if '\n' in case_id or case_id in seen:
                raise ValueError('duplicate or multiline case_id: '+case_id)
            seen.add(case_id)
            _text(case['title'],'title');_text(case['preconditions'],'preconditions')
            if not isinstance(case['steps'],list) or not case['steps']:
                raise ValueError('steps requires a nonempty array')
            for value in case['steps']:_text(value,'steps')
            if not isinstance(case['expected'],list) or not case['expected']:
                raise ValueError('expected requires a nonempty array')
            for value in case['expected']:_text(value,'expected')
            observations=case['observable_evidence']
            if not isinstance(observations,list) or not observations:
                raise ValueError('observable_evidence requires a nonempty array')
            for item in observations:
                _fields(item,('surface','target','assertion'),('surface','target','assertion'),'observable evidence')
                _text(item['surface'],'observable_evidence.surface')
                _text(item['target'],'observable_evidence.target')
                _text(item['assertion'],'observable_evidence.assertion')
            unit_ids=_strings(case['review_unit_ids'],'review_unit_ids',True)
            unknown=[identifier for identifier in unit_ids if identifier not in units]
            if unknown:
                raise ValueError('unknown review unit: '+unknown[0])
            result.append(case)
    return result


def resolve_case_sources(data,analysis,review):
    """由 review_unit_ids 自动展开源码 fact/evidence，Agent 不再手工绑定 ID。"""
    validate_cases(data,analysis,review)
    known_facts={fact['id']:fact for project in analysis.get('projects',[])
                 for fact in project.get('facts',[])}
    units=review_unit_index(review)
    result={}
    for purpose in PURPOSES:
        for case in data['purpose_cases'][purpose]:
            fact_ids=sorted({fact_id for unit_id in case['review_unit_ids']
                             for fact_id in units[unit_id]['fact_ids']})
            unknown=[identifier for identifier in fact_ids if identifier not in known_facts]
            if unknown:
                raise ValueError('review unit references unknown analysis fact: '+unknown[0])
            evidence_ids=sorted({identifier for fact_id in fact_ids
                                 for identifier in known_facts[fact_id].get('evidence_ids',[])})
            if not fact_ids or not evidence_ids:
                raise ValueError('case review units have no source evidence: '+case['case_id'])
            result[case['case_id']]=dict(fact_ids=fact_ids,evidence_ids=evidence_ids)
    return result


def review_facet_index(review):
    """校验并索引当前 review_facets。"""
    units=review_unit_index(review)
    facets={}
    for project in review.get('projects') or []:
        for facet in project.get('review_facets') or []:
            if not isinstance(facet,dict):
                raise ValueError('review facet must be an object')
            identifier=_text(facet.get('id'),'review_facet.id')
            if identifier in facets:
                raise ValueError('duplicate review facet: '+identifier)
            unit_ids=_strings(facet.get('unit_ids'),'review_facet.unit_ids',True)
            unknown=[unit_id for unit_id in unit_ids if unit_id not in units]
            if unknown:
                raise ValueError('review facet references unknown unit: '+unknown[0])
            wrong_project=[unit_id for unit_id in unit_ids
                           if units[unit_id]['project']!=project.get('name')]
            if wrong_project:
                raise ValueError('review facet crosses projects: '+wrong_project[0])
            if facet.get('unit_count')!=len(unit_ids):
                raise ValueError('review facet unit_count mismatch: '+identifier)
            facets[identifier]=dict(
                project=project.get('name'),
                type=_text(facet.get('type'),'review_facet.type'),
                anchor=_text(facet.get('anchor'),'review_facet.anchor'),
                unit_ids=unit_ids)
    expected_membership={unit_id:set() for unit_id in units}
    for facet_id,facet in facets.items():
        for unit_id in facet['unit_ids']:
            expected_membership[unit_id].add(facet_id)
    for unit_id,row in units.items():
        actual=set(_strings(row['unit'].get('facet_ids') or [],'review_unit.facet_ids'))
        if actual!=expected_membership[unit_id]:
            raise ValueError('review unit facet membership mismatch: '+unit_id)
    return facets


def coverage_matrix(data,analysis,review):
    """输出结构覆盖矩阵；只提示未引用范围，不证明业务覆盖充分。"""
    validate_cases(data,analysis,review)
    units=review_unit_index(review)
    facets=review_facet_index(review)
    cases_by_unit={unit_id:[] for unit_id in units}
    purposes_by_unit={unit_id:set() for unit_id in units}
    for purpose in PURPOSES:
        for case in data['purpose_cases'][purpose]:
            case_id=case['case_id']
            for unit_id in case['review_unit_ids']:
                cases_by_unit[unit_id].append(case_id)
                purposes_by_unit[unit_id].add(purpose)

    unit_rows=[]
    for unit_id,row in sorted(units.items()):
        unit=row['unit']
        case_ids=sorted(set(cases_by_unit[unit_id]))
        unit_rows.append(dict(
            unit_id=unit_id,
            project=row['project'],
            scope=unit.get('scope'),
            symbol=unit.get('symbol'),
            facet_ids=sorted(unit.get('facet_ids') or []),
            change_summary=unit.get('change_summary') or [],
            direct_fact_groups=unit.get('direct_fact_groups') or {},
            context_fact_groups=unit.get('context_fact_groups') or {},
            covered=bool(case_ids),
            case_ids=case_ids,
            purposes=sorted(purposes_by_unit[unit_id])))

    facet_rows=[]
    for facet_id,facet in sorted(facets.items()):
        covered_units=[unit_id for unit_id in facet['unit_ids'] if cases_by_unit[unit_id]]
        uncovered_units=[unit_id for unit_id in facet['unit_ids'] if not cases_by_unit[unit_id]]
        case_ids=sorted({case_id for unit_id in facet['unit_ids'] for case_id in cases_by_unit[unit_id]})
        facet_rows.append(dict(
            facet_id=facet_id,
            project=facet['project'],
            type=facet['type'],
            anchor=facet['anchor'],
            unit_ids=facet['unit_ids'],
            covered_unit_ids=covered_units,
            uncovered_unit_ids=uncovered_units,
            case_ids=case_ids))

    uncovered=[row['unit_id'] for row in unit_rows if not row['covered']]
    uncovered_method=[row['unit_id'] for row in unit_rows
                      if not row['covered'] and row['scope']=='method']
    uncovered_project=[row['unit_id'] for row in unit_rows
                       if not row['covered'] and row['scope']=='project']
    partially=[row['facet_id'] for row in facet_rows if row['covered_unit_ids'] and row['uncovered_unit_ids']]
    uncovered_facets=[row['facet_id'] for row in facet_rows if not row['covered_unit_ids']]
    fully=[row['facet_id'] for row in facet_rows if row['covered_unit_ids'] and not row['uncovered_unit_ids']]
    return dict(
        coverage_schema_version=1,
        analysis_id=data['analysis_id'],
        review_schema_version=REVIEW_SCHEMA_VERSION,
        summary=dict(
            unit_count=len(unit_rows),
            covered_unit_count=len(unit_rows)-len(uncovered),
            uncovered_unit_count=len(uncovered),
            uncovered_method_unit_count=len(uncovered_method),
            uncovered_project_unit_count=len(uncovered_project),
            facet_count=len(facet_rows),
            fully_covered_facet_count=len(fully),
            partially_covered_facet_count=len(partially),
            uncovered_facet_count=len(uncovered_facets)),
        uncovered_unit_ids=uncovered,
        uncovered_method_unit_ids=uncovered_method,
        uncovered_project_unit_ids=uncovered_project,
        partially_covered_facet_ids=partially,
        uncovered_facet_ids=uncovered_facets,
        unit_coverage=unit_rows,
        facet_coverage=facet_rows,
        note='该矩阵只检查 review unit 是否被测试用例结构引用，不证明分支、业务规则或预期结果正确。')
