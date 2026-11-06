#!/usr/bin/env python3
"""当前四类测试用例契约。"""
PURPOSES=('acceptance','core_regression','related_regression','boundary')
PURPOSE_NAMES=('需求验收','既有核心流程回归','关联回归','业务规则与边界验证')
SCHEMA_VERSION=4
REVIEW_SCHEMA_VERSION=2


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
            if not set(fact_ids)<=catalog:
                raise ValueError('review unit references facts outside fact_catalog: '+identifier)
            index[identifier]=dict(project=project.get('name'),unit=unit,fact_ids=fact_ids)
    if not index:
        raise ValueError('review contains no review units')
    return index


def validate_cases(data,analysis=None,review=None):
    """只接受当前 schema_version=4、基于 review_unit_ids 的四类直接用例。"""
    _fields(data,('schema_version','analysis_id','purpose_cases'),
            ('schema_version','analysis_id','purpose_cases'),'test cases')
    if data.get('schema_version')!=SCHEMA_VERSION:
        raise ValueError('test cases require schema_version={}'.format(SCHEMA_VERSION))
    analysis_id=_text(data.get('analysis_id'),'analysis_id')
    if analysis is not None and analysis.get('analysis_id')!=analysis_id:
        raise ValueError('test cases belong to a different analysis')
    if review is not None and review.get('analysis_id')!=analysis_id:
        raise ValueError('test cases belong to a different review')
    units=review_unit_index(review) if review is not None else None
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
            if units is not None:
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
