#!/usr/bin/env python3
"""校验当前分析身份，以及最终用例实际引用的源码事实和证据。"""
import argparse
import json
import re
import sys
from pathlib import Path

from analysis_engine import VERSION, redact
from case_contract import PURPOSES, validate_cases
from source_index import batch_blobs, git_bytes, stable, source_lines


def verify_evidence(project,records):
    errors=[]
    repo=project['analysis_clone']
    snapshots=project['snapshots']
    trees={}
    for side,tree in snapshots.items():
        if not isinstance(tree,str) or not re.fullmatch(r'[0-9a-f]{40,64}',tree):
            errors.append('invalid snapshot tree: '+side)
            continue
        actual=git_bytes(repo,'rev-parse',tree+'^{tree}').decode('ascii').strip()
        if actual!=tree:
            errors.append('snapshot tree mismatch: '+side)
            continue
        files={}
        for row in git_bytes(repo,'ls-tree','-r','-z',tree).split(b'\0'):
            if not row:
                continue
            meta,path=row.split(b'\t',1)
            parts=meta.decode('ascii').split()
            if parts[1]=='blob':
                files[path.decode('utf-8')]=parts[2]
        trees[side]=files
    for record in records:
        side=record.get('side')
        if (side not in trees or record.get('tree')!=snapshots.get(side)
                or trees[side].get(record.get('path'))!=record.get('blob')):
            errors.append('evidence snapshot/blob mismatch: '+str(record.get('id')))
    if errors:
        return errors
    blobs=batch_blobs(repo,sorted(set(row['blob'] for row in records)))
    for record in records:
        identifier=record.get('id','')
        start,end=record.get('start_line'),record.get('end_line')
        if type(start)!=int or type(end)!=int or start<1 or end<start:
            errors.append('invalid evidence span: '+identifier)
            continue
        try:
            lines=source_lines(blobs[record['blob']].decode('utf-8-sig'))
        except UnicodeError:
            errors.append('evidence is not UTF-8: '+identifier)
            continue
        snippet=redact(''.join(lines[start-1:end]))
        if end>len(lines) or record.get('snippet')!=snippet or record.get('digest')!=stable(snippet):
            errors.append('evidence content mismatch: '+identifier)
        expected=stable(project['name'],record['tree'],record['path'],record['blob'],start,end)
        if identifier!=expected:
            errors.append('evidence id mismatch: '+identifier)
    return errors


def validate(analysis,context,review,cases,verify_sources=True):
    errors=[];warnings=[]
    if analysis.get('schema_version')!=1 or analysis.get('analyzer_version')!=VERSION:
        return ['unsupported analysis schema/analyzer version'],warnings
    projects=analysis.get('projects',[])
    if not projects or len({project.get('name') for project in projects})!=len(projects):
        return ['missing or duplicate projects'],warnings
    expected=stable(VERSION,analysis.get('rules',{}),
                    [(project['name'],project['repository'],project['snapshots']) for project in projects])
    if analysis.get('analysis_id')!=expected:
        errors.append('analysis snapshot identity mismatch')
    if review.get('analysis_id')!=analysis.get('analysis_id') or not review.get('fast10_mode'):
        errors.append('review belongs to a different analysis or is not a fast10 review')
    try:
        validate_cases(cases,analysis)
    except (ValueError,TypeError,KeyError) as exc:
        return errors+['invalid test cases: '+str(exc)],warnings

    facts={}
    for project in projects:
        errors += ['snapshot failure: '+message for message in project.get('hard_errors',[])]
        if set(project.get('snapshots',{}))!={'baseline','result'}:
            errors.append('missing snapshots for '+project.get('name',''))
        for fact in project.get('facts',[]):
            identifier=fact.get('id')
            if identifier in facts:
                errors.append('duplicate fact id: '+str(identifier))
                continue
            expected_fact=stable(project['name'],fact['kind'],fact['data'],fact.get('evidence_ids',[]))
            if identifier!=expected_fact:
                errors.append('fact identity mismatch: '+str(identifier))
            facts[identifier]=(project['name'],fact)

    evidence={}
    for row in context:
        identifier=row.get('id')
        if not identifier:
            continue
        if identifier in evidence:
            errors.append('duplicate evidence id: '+identifier)
        evidence[identifier]=row

    referenced_facts=set()
    referenced_evidence=set()
    for purpose in PURPOSES:
        for case in cases['purpose_cases'][purpose]:
            referenced_facts.update(case.get('fact_ids') or [])
            referenced_evidence.update(case.get('evidence_ids') or [])

    for identifier in sorted(referenced_facts):
        if identifier not in facts:
            errors.append('unknown referenced fact: '+identifier)
            continue
        project_name,fact=facts[identifier]
        for ref in set(fact.get('evidence_ids',[])) & referenced_evidence:
            record=evidence.get(ref)
            if record and record.get('project')!=project_name:
                errors.append('referenced evidence project mismatch: '+ref)

    for identifier in sorted(referenced_evidence):
        if identifier not in evidence:
            errors.append('unknown referenced evidence: '+identifier)

    allowed={ref for fact_id in referenced_facts if fact_id in facts
             for ref in facts[fact_id][1].get('evidence_ids',[])}
    if not referenced_evidence<=allowed:
        errors.append('referenced evidence is not attached to the referenced facts')

    if verify_sources:
        for project in projects:
            records=[evidence[identifier] for identifier in sorted(referenced_evidence)
                     if identifier in evidence and evidence[identifier].get('project')==project['name']]
            if not records:
                continue
            try:
                errors += verify_evidence(project,records)
            except (ValueError,OSError,KeyError) as exc:
                errors.append('cannot verify referenced snapshot sources: '+str(exc))

    warnings.append('Static evidence does not prove test execution, runtime binding or business correctness.')
    return errors,warnings


def validate_files(analysis_path,review_path,cases_path):
    analysis_path=Path(analysis_path)
    analysis=json.loads(analysis_path.read_text(encoding='utf-8-sig'))
    context_path=(analysis_path.parent/analysis['context_file']).resolve()
    if context_path.parent!=analysis_path.parent.resolve():
        raise ValueError('context file must stay beside analysis.json')
    context=[json.loads(line) for line in context_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    review=json.loads(Path(review_path).read_text(encoding='utf-8-sig'))
    cases=json.loads(Path(cases_path).read_text(encoding='utf-8-sig'))
    return validate(analysis,context,review,cases)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis',required=True)
    parser.add_argument('--review',required=True)
    parser.add_argument('--cases',required=True)
    args=parser.parse_args(argv)
    try:
        errors,warnings=validate_files(args.analysis,args.review,args.cases)
    except (OSError,ValueError,KeyError,TypeError,IndexError) as exc:
        print('ERROR: '+str(exc))
        return 2
    for error in errors:
        print('ERROR: '+error)
    for warning in warnings:
        print('NOTE: '+warning)
    print('VALIDATION: '+('FAIL' if errors else 'PASS (static declarations only)'))
    return 1 if errors else 0


if __name__=='__main__':
    sys.exit(main())
