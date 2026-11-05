#!/usr/bin/env python3
"""Validate a selected fast-review case set without requiring full coverage closure."""
import json
from pathlib import Path


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def validate_scope_data(analysis, review, cases):
    """Check case structure and ensure every linked source item belongs to fast10 scope."""
    if not review.get('fast10_mode'):
        raise ValueError('scope-only validation requires a fast10 review')
    from test_points import PURPOSES, validate_cases
    validate_cases(cases, analysis)

    analysis_projects = {p.get('name'): p for p in analysis.get('projects', [])}
    allowed_obligations = set()
    allowed_facts = set()
    allowed_evidence = set()
    scope_found = False
    for reviewed in review.get('projects', []):
        normal = reviewed.get('normal_flow_review') or {}
        groups = normal.get('groups') or []
        fact_ids = set(normal.get('supporting_fact_ids') or [])
        fact_ids.update(normal.get('visible_fact_ids') or [])
        if not groups and not fact_ids:
            continue
        scope_found = True
        allowed_evidence.update(normal.get('evidence_ids') or [])
        for group in groups:
            fact_ids.update(group.get('fact_ids') or [])
            allowed_obligations.update(group.get('supporting_obligation_ids') or [])
            allowed_evidence.update(group.get('evidence_ids') or [])
        fact_ids.update(row.get('id') for row in reviewed.get('facts', []) if row.get('id'))

        source = analysis_projects.get(reviewed.get('name'))
        if source is None:
            raise ValueError('fast10 review project is missing from analysis: ' + str(reviewed.get('name')))
        facts = {f.get('id'): f for f in source.get('facts', [])}
        obligations = source.get('obligations', [])
        allowed_facts.update(fact_ids)
        for obligation in obligations:
            linked_facts = set(obligation.get('fact_ids') or [])
            if linked_facts & fact_ids:
                allowed_obligations.add(obligation.get('id'))
                allowed_facts.update(linked_facts)
                allowed_evidence.update(obligation.get('evidence_ids') or [])
        for fact_id in list(allowed_facts):
            fact = facts.get(fact_id)
            if fact:
                allowed_evidence.update(fact.get('evidence_ids') or [])

    if not scope_found:
        raise ValueError('fast10 review has no selected normal-flow scope')

    if cases.get('schema_version')==3:
        for purpose in PURPOSES:
            for case in cases.get('purpose_cases', {}).get(purpose, []):
                if not set(case.get('fact_ids') or []) <= allowed_facts:
                    raise ValueError('scope case references facts outside fast10 selection: ' + str(case.get('case_id')))
                if not set(case.get('evidence_ids') or []) <= allowed_evidence:
                    raise ValueError('scope case references evidence outside fast10 selection: ' + str(case.get('case_id')))
        return True

    for purpose in PURPOSES:
        for case in cases.get('purpose_cases', {}).get(purpose, []):
            for link in case.get('coverage_links', []):
                obligation_id = link.get('obligation_id')
                if obligation_id not in allowed_obligations:
                    raise ValueError('scope case references an obligation outside fast10 selection: ' + str(obligation_id))
                if not set(link.get('fact_ids') or []) <= allowed_facts:
                    raise ValueError('scope case references facts outside fast10 selection: ' + str(obligation_id))
                if not set(link.get('evidence_ids') or []) <= allowed_evidence:
                    raise ValueError('scope case references evidence outside fast10 selection: ' + str(obligation_id))
    return True


def validate_scope_files(analysis_path, review_path, cases_path):
    return validate_scope_data(_read(analysis_path), _read(review_path), _read(cases_path))
