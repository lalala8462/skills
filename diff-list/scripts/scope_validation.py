#!/usr/bin/env python3
"""校验最终用例只引用当前复核范围内的 review unit。"""
import json
from pathlib import Path
from case_contract import review_facet_index,validate_cases,review_unit_index


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def validate_scope_data(analysis,review,cases):
    validate_cases(cases,analysis,review)
    known_facts={fact.get('id') for project in analysis.get('projects',[])
                 for fact in project.get('facts',[]) if fact.get('id')}
    units=review_unit_index(review)
    review_facet_index(review)
    for identifier,row in units.items():
        if not set(row['fact_ids'])<=known_facts:
            raise ValueError('review unit references facts outside current analysis: '+identifier)
    return True


def validate_scope_files(analysis_path,review_path,cases_path):
    return validate_scope_data(_read(analysis_path),_read(review_path),_read(cases_path))
