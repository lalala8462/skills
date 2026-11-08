#!/usr/bin/env python3
"""校验当前分析身份，以及用例显式 unit_assertions 对应的源码事实和证据。"""
import argparse
import json
import re
import sys
from pathlib import Path

from analysis_engine import VERSION, redact
from case_contract import REVIEW_SCHEMA_VERSION, coverage_matrix, resolve_case_sources
from source_index import batch_blobs, git_bytes, stable, source_lines
from repo_access import decode_git_path


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
                files[decode_git_path(path)]=parts[2]
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
    if analysis.get('schema_version')!=2 or analysis.get('analyzer_version')!=VERSION:
        return ['unsupported analysis schema/analyzer version'],warnings
    projects=analysis.get('projects',[])
    if not projects or len({project.get('name') for project in projects})!=len(projects):
        return ['missing or duplicate projects'],warnings
    expected=stable(VERSION,analysis.get('rules',{}),analysis.get('limits',{}),
                    [(project['name'],project['repository'],project['snapshots']) for project in projects])
    if analysis.get('analysis_id')!=expected:
        errors.append('analysis snapshot identity mismatch')
    if (review.get('analysis_id')!=analysis.get('analysis_id')
            or review.get('review_schema_version')!=REVIEW_SCHEMA_VERSION):
        errors.append('review belongs to a different analysis or has an unsupported review schema')

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

    try:
        resolved=resolve_case_sources(cases,analysis,review)
    except (ValueError,TypeError,KeyError) as exc:
        return errors+['invalid test cases: '+str(exc)],warnings

    referenced_facts={identifier for row in resolved.values() for identifier in row['fact_ids']}
    referenced_evidence={identifier for row in resolved.values() for identifier in row['evidence_ids']}

    for identifier in sorted(referenced_facts):
        if identifier not in facts:
            errors.append('unknown resolved fact: '+identifier)
            continue
        project_name,fact=facts[identifier]
        for ref in fact.get('evidence_ids',[]):
            record=evidence.get(ref)
            if record and record.get('project')!=project_name:
                errors.append('resolved evidence project mismatch: '+ref)

    for identifier in sorted(referenced_evidence):
        if identifier not in evidence:
            errors.append('unknown resolved evidence: '+identifier)

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

    coverage=coverage_matrix(cases,analysis,review)
    summary=coverage['summary']
    warnings.append('复核单元已建立显式主事实—断言关联: {}/{}；待复核={}'.format(
        summary['referenced_unit_count'],summary['unit_count'],summary['pending_review_count']))
    if coverage.get('pending_review'):
        warnings.append('Pending review units: '+','.join(
            row['unit_id'] for row in coverage['pending_review']))
    warnings.append('facet 关联用例（至少一个 unit 被引用）: {}/{}；全部 unit 已引用={}；部分 unit 已引用={}；未引用={}'.format(
        summary['referenced_facet_count'],summary['facet_count'],
        summary['all_units_referenced_facet_count'],summary['partially_referenced_facet_count'],
        summary['unreferenced_facet_count']))
    warnings.append('这些仅是显式 ID—断言位置关联统计，不证明分支和业务规则的测试覆盖。')
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
    parser.add_argument('--coverage-output',help='可选：把结构覆盖矩阵写入新的 JSON 文件')
    args=parser.parse_args(argv)
    try:
        errors,warnings=validate_files(args.analysis,args.review,args.cases)
        if args.coverage_output:
            analysis=json.loads(Path(args.analysis).read_text(encoding='utf-8-sig'))
            review=json.loads(Path(args.review).read_text(encoding='utf-8-sig'))
            cases=json.loads(Path(args.cases).read_text(encoding='utf-8-sig'))
            target=Path(args.coverage_output)
            target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('x',encoding='utf-8',newline='\n') as stream:
                json.dump(coverage_matrix(cases,analysis,review),stream,ensure_ascii=False,indent=2,allow_nan=False)
                stream.write('\n')
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
