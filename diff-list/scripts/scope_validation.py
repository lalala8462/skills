#!/usr/bin/env python3
"""校验最终用例只引用当前快审范围内的事实和证据。"""
import json
from pathlib import Path
from case_contract import PURPOSES, validate_cases


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def validate_scope_data(analysis,review,cases):
    if not review.get('fast10_mode'):
        raise ValueError('scope validation requires a fast10 review')
    validate_cases(cases,analysis)
    allowed_facts=set()
    allowed_evidence=set()
    for project in review.get('projects',[]):
        facts=project.get('facts') or []
        allowed_facts.update(row.get('id') for row in facts if row.get('id'))
        allowed_evidence.update(identifier for row in facts for identifier in row.get('evidence_ids',[]))
        allowed_evidence.update(project.get('normal_flow_review',{}).get('evidence_ids') or [])
        allowed_evidence.update(identifier for relation in project.get('direct_relations',[])
                                for identifier in relation.get('evidence_ids',[]))
    if not allowed_facts:
        raise ValueError('fast10 review has no selected facts')
    for purpose in PURPOSES:
        for case in cases['purpose_cases'][purpose]:
            if not set(case.get('fact_ids') or [])<=allowed_facts:
                raise ValueError('case references facts outside fast10 selection: '+case.get('case_id',''))
            if not set(case.get('evidence_ids') or [])<=allowed_evidence:
                raise ValueError('case references evidence outside fast10 selection: '+case.get('case_id',''))
    return True


def validate_scope_files(analysis_path,review_path,cases_path):
    return validate_scope_data(_read(analysis_path),_read(review_path),_read(cases_path))
