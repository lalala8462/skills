#!/usr/bin/env python3
"""当前四类测试用例契约。"""
import json

PURPOSES=('acceptance','core_regression','related_regression','boundary')
PURPOSE_NAMES=('需求验收','既有核心流程回归','关联回归','业务规则与边界验证')


def _text(value,label):
    if not isinstance(value,str) or not value.strip():
        raise ValueError(label+' requires a nonempty string')
    return value.strip()


def _strings(values,label):
    if not isinstance(values,list):
        raise ValueError(label+' requires an array')
    return sorted({_text(value,label) for value in values})


def _fields(item,allowed,required,label):
    if not isinstance(item,dict) or set(item)-set(allowed) or not set(required)<=set(item):
        raise ValueError(label+' has missing or unsupported fields')


def validate_cases(data,analysis=None):
    """只接受当前 schema_version=3 的四类直接用例。"""
    _fields(data,('schema_version','analysis_id','purpose_cases'),
            ('schema_version','analysis_id','purpose_cases'),'test cases')
    if data.get('schema_version')!=3:
        raise ValueError('test cases require schema_version=3')
    analysis_id=_text(data.get('analysis_id'),'analysis_id')
    if analysis is not None and analysis.get('analysis_id')!=analysis_id:
        raise ValueError('test cases belong to a different analysis')
    groups=data.get('purpose_cases')
    if not isinstance(groups,dict) or set(groups)!=set(PURPOSES):
        raise ValueError('purpose_cases requires exactly four purpose arrays')
    known_facts={}
    if analysis is not None:
        known_facts={fact['id']:fact for project in analysis.get('projects',[]) for fact in project.get('facts',[])}
    seen=set();result=[]
    for purpose in PURPOSES:
        if not isinstance(groups[purpose],list):
            raise ValueError('purpose case group must be an array')
        for case in groups[purpose]:
            fields=('case_id','title','preconditions','steps','expected','observable_evidence','fact_ids','evidence_ids')
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
            fact_ids=_strings(case['fact_ids'],'fact_ids')
            evidence_ids=_strings(case['evidence_ids'],'evidence_ids')
            if analysis is not None:
                unknown=[identifier for identifier in fact_ids if identifier not in known_facts]
                if unknown:
                    raise ValueError('unknown case fact: '+unknown[0])
                allowed={identifier for fact_id in fact_ids
                         for identifier in known_facts[fact_id].get('evidence_ids',[])}
                if fact_ids and allowed and not evidence_ids:
                    raise ValueError('case with source facts requires referenced evidence: '+case_id)
                if not set(evidence_ids)<=allowed:
                    raise ValueError('case evidence must belong to referenced facts: '+case_id)
            result.append(case)
    return result


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
