#!/usr/bin/env python3
"""当前四类测试用例契约。"""
PURPOSES=('acceptance','core_regression','related_regression','boundary')
PURPOSE_NAMES=('需求验收','既有核心流程回归','关联回归','业务规则与边界验证')
SCHEMA_VERSION=5
REVIEW_SCHEMA_VERSION=4
PRIMARY_CHANGE_KINDS=frozenset(('method_change','field_change','class_binding_change',
    'configuration','configuration_file_change','configuration_parse_boundary',
    'argument_source_change','mapper_statement_change'))


def is_direct_primary_change_fact(fact):
    """仅判断固定结构事实类型和显式变化标记，不推断业务重要性。"""
    if fact.get('kind')=='configuration':
        return (fact.get('data') or {}).get('change') in ('added','removed')
    return fact.get('kind') in PRIMARY_CHANGE_KINDS


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
    """只接受当前 schema_version=5；unit_assertions 为每项显式主事实及断言索引。"""
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
    primary_fact_ids={fact['id'] for project in analysis.get('projects',[])
                      for fact in project.get('facts',[])
                      if is_direct_primary_change_fact(fact)}
    groups=data.get('purpose_cases')
    if not isinstance(groups,dict) or set(groups)!=set(PURPOSES):
        raise ValueError('purpose_cases requires exactly four purpose arrays')
    seen=set();result=[]
    for purpose in PURPOSES:
        if not isinstance(groups[purpose],list):
            raise ValueError('purpose case group must be an array')
        for case in groups[purpose]:
            fields=('case_id','title','preconditions','steps','expected','observable_evidence','review_unit_ids','unit_assertions')
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
            links=case['unit_assertions']
            if not isinstance(links,list) or not links:
                raise ValueError('unit_assertions requires a nonempty array')
            linked=set()
            for link in links:
                link_fields=('unit_id','direct_fact_ids','observation_indexes')
                _fields(link,link_fields,link_fields,'unit assertion')
                unit_id=_text(link['unit_id'],'unit_assertions.unit_id')
                if unit_id not in units or unit_id not in unit_ids or unit_id in linked:
                    raise ValueError('unit_assertions unit_id must be unique and belong to review_unit_ids: '+unit_id)
                linked.add(unit_id)
                linked_facts=_strings(link['direct_fact_ids'],'unit_assertions.direct_fact_ids',True)
                if not set(linked_facts)<=set(units[unit_id]['direct_fact_ids']) or not set(linked_facts)<=primary_fact_ids:
                    raise ValueError('unit_assertions must reference direct primary facts of the unit: '+unit_id)
                indexes=link['observation_indexes']
                if (not isinstance(indexes,list) or not indexes or
                        any(type(index)!=int or index<0 or index>=len(observations) for index in indexes) or
                        len(set(indexes))!=len(indexes)):
                    raise ValueError('unit_assertions observation_indexes must reference observable_evidence positions')
            if linked!=set(unit_ids):
                raise ValueError('every referenced review unit requires its own unit_assertions mapping')
            result.append(case)
    return result


def resolve_case_sources(data,analysis,review):
    """按 Agent 明确绑定的 unit_assertions 取证，不能自动把 unit 全部事实算作测试依据。"""
    validate_cases(data,analysis,review)
    known_facts={fact['id']:fact for project in analysis.get('projects',[])
                 for fact in project.get('facts',[])}
    units=review_unit_index(review)
    result={}
    for purpose in PURPOSES:
        for case in data['purpose_cases'][purpose]:
            fact_ids=sorted({fid for link in case['unit_assertions']
                             for fid in link['direct_fact_ids']})
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


def navigation_facet_index(review):
    """校验首轮 navigation_facets；它必须是完整 review_facets 的严格子集/同构引用。"""
    units=review_unit_index(review)
    full=review_facet_index(review)
    navigation={}
    for project in review.get('projects') or []:
        for facet in project.get('navigation_facets') or []:
            if not isinstance(facet,dict):
                raise ValueError('navigation facet must be an object')
            identifier=_text(facet.get('id'),'navigation_facet.id')
            if identifier in navigation:
                raise ValueError('duplicate navigation facet: '+identifier)
            if identifier not in full:
                raise ValueError('navigation facet must reference review_facets: '+identifier)
            unit_ids=_strings(facet.get('unit_ids'),'navigation_facet.unit_ids',True)
            base=full[identifier]
            if (base['project']!=project.get('name') or base['type']!=facet.get('type')
                    or base['anchor']!=facet.get('anchor') or base['unit_ids']!=unit_ids):
                raise ValueError('navigation facet differs from full facet: '+identifier)
            priority=facet.get('navigation_priority')
            if type(priority)!=int or priority<0:
                raise ValueError('navigation facet requires nonnegative navigation_priority: '+identifier)
            reason=_text(facet.get('navigation_reason'),'navigation_facet.navigation_reason')
            priority_kind=facet.get('priority_kind')
            if priority_kind!='reading_order_heuristic':
                raise ValueError('navigation facet priority is not a risk score: '+identifier)
            navigation[identifier]=dict(base,navigation_priority=priority,
                                        navigation_reason=reason,priority_kind=priority_kind)
    expected_membership={unit_id:set() for unit_id in units}
    for facet_id,facet in navigation.items():
        for unit_id in facet['unit_ids']:
            expected_membership[unit_id].add(facet_id)
    for unit_id,row in units.items():
        actual=set(_strings(row['unit'].get('navigation_facet_ids') or [],
                            'review_unit.navigation_facet_ids'))
        if actual!=expected_membership[unit_id]:
            raise ValueError('review unit navigation facet membership mismatch: '+unit_id)
    return navigation


def coverage_matrix(data,analysis,review):
    """统计测试用例对复核单元与 facet 的引用关系，不衡量业务覆盖率。"""
    validate_cases(data,analysis,review)
    units=review_unit_index(review)
    facets=review_facet_index(review)
    navigation_facets=navigation_facet_index(review)
    cases_by_unit={unit_id:[] for unit_id in units}
    links_by_unit={unit_id:[] for unit_id in units}
    purposes_by_unit={unit_id:set() for unit_id in units}
    for purpose in PURPOSES:
        for case in data['purpose_cases'][purpose]:
            case_id=case['case_id']
            for link in case['unit_assertions']:
                unit_id=link['unit_id']
                cases_by_unit[unit_id].append(case_id)
                links_by_unit[unit_id].append(dict(
                    case_id=case_id,direct_fact_ids=sorted(link['direct_fact_ids']),
                    observation_indexes=sorted(link['observation_indexes'])))
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
            navigation_facet_ids=sorted(unit.get('navigation_facet_ids') or []),
            change_summary=unit.get('change_summary') or [],
            direct_fact_groups=unit.get('direct_fact_groups') or {},
            context_fact_groups=unit.get('context_fact_groups') or {},
            referenced_by_cases=bool(case_ids),
            has_explicit_primary_assertion_link=bool(links_by_unit[unit_id]),
            primary_assertion_links=sorted(links_by_unit[unit_id],key=lambda x:x['case_id']),
            case_ids=case_ids,
            purposes=sorted(purposes_by_unit[unit_id])))

    facet_rows=[]
    for facet_id,facet in sorted(facets.items()):
        referenced_units=[unit_id for unit_id in facet['unit_ids'] if cases_by_unit[unit_id]]
        unreferenced_units=[unit_id for unit_id in facet['unit_ids'] if not cases_by_unit[unit_id]]
        case_ids=sorted({case_id for unit_id in facet['unit_ids'] for case_id in cases_by_unit[unit_id]})
        facet_rows.append(dict(
            facet_id=facet_id,
            project=facet['project'],
            type=facet['type'],
            anchor=facet['anchor'],
            unit_ids=facet['unit_ids'],
            referenced_unit_ids=referenced_units,
            unreferenced_unit_ids=unreferenced_units,
            case_ids=case_ids))

    unreferenced=[row['unit_id'] for row in unit_rows if not row['referenced_by_cases']]
    unreferenced_method=[row['unit_id'] for row in unit_rows
                      if not row['referenced_by_cases'] and row['scope']=='method']
    unreferenced_project=[row['unit_id'] for row in unit_rows
                       if not row['referenced_by_cases'] and row['scope']=='project']
    partially=[row['facet_id'] for row in facet_rows if row['referenced_unit_ids'] and row['unreferenced_unit_ids']]
    unreferenced_facets=[row['facet_id'] for row in facet_rows if not row['referenced_unit_ids']]
    fully=[row['facet_id'] for row in facet_rows if row['referenced_unit_ids'] and not row['unreferenced_unit_ids']]
    pending_review=[]
    for row in unit_rows:
        if row['has_explicit_primary_assertion_link']:
            continue
        high_value=[]
        for facet_id in row['navigation_facet_ids']:
            facet=navigation_facets[facet_id]
            high_value.append(dict(
                facet_id=facet_id,
                type=facet['type'],
                anchor=facet['anchor'],
                navigation_priority=facet['navigation_priority'],
                priority_kind=facet['priority_kind'],
                navigation_reason=facet['navigation_reason']))
        high_value.sort(key=lambda item:(item['navigation_priority'],item['type'],item['anchor']))
        candidate_case_ids=sorted({
            case_id
            for facet_id in row['navigation_facet_ids']
            for unit_id in navigation_facets[facet_id]['unit_ids']
            if unit_id!=row['unit_id']
            for case_id in cases_by_unit[unit_id]
        })
        related_referenced_unit_ids=sorted({
            unit_id
            for facet_id in row['navigation_facet_ids']
            for unit_id in navigation_facets[facet_id]['unit_ids']
            if unit_id!=row['unit_id'] and cases_by_unit[unit_id]
        })
        pending_review.append(dict(
            unit_id=row['unit_id'],
            project=row['project'],
            scope=row['scope'],
            symbol=row['symbol'],
            direct_change=row['change_summary'],
            high_value_facets=high_value,
            candidate_case_ids=candidate_case_ids,
            related_referenced_unit_ids=related_referenced_unit_ids,
            all_facet_ids=row['facet_ids'],
            decision='待 Agent 判断：补充用例或为当前用例明确绑定该 unit 的主事实与可观察断言；脚本不判断业务覆盖'))
    return dict(
        coverage_schema_version=5,
        analysis_id=data['analysis_id'],
        review_schema_version=REVIEW_SCHEMA_VERSION,
        summary=dict(
            unit_count=len(unit_rows),
            referenced_unit_count=len(unit_rows)-len(unreferenced),
            unreferenced_unit_count=len(unreferenced),
            unreferenced_method_unit_count=len(unreferenced_method),
            unreferenced_project_unit_count=len(unreferenced_project),
            facet_count=len(facet_rows),
            navigation_facet_count=len(navigation_facets),
            pending_review_count=len(pending_review),
            referenced_facet_count=len(facet_rows)-len(unreferenced_facets),
            all_units_referenced_facet_count=len(fully),
            partially_referenced_facet_count=len(partially),
            unreferenced_facet_count=len(unreferenced_facets)),
        unreferenced_unit_ids=unreferenced,
        unreferenced_method_unit_ids=unreferenced_method,
        unreferenced_project_unit_ids=unreferenced_project,
        partially_referenced_facet_ids=partially,
        unreferenced_facet_ids=unreferenced_facets,
        pending_review=pending_review,
        unit_case_references=unit_rows,
        facet_case_references=facet_rows,
        note='仅统计显式 unit_assertions 的主事实 ID 与可观察断言位置的关联，不证明分支或业务规则覆盖；pending_review 表示缺少可追溯用例关联的复核单元。')
