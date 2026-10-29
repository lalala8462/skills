#!/usr/bin/env python3
"""校验固定快照、源码证据及覆盖声明；不证明用例已执行。"""
import argparse
import json
import re
import sys
from pathlib import Path
from analysis_engine import VERSION, redact
from export_test_cases import normalize
from source_index import batch_blobs, git_bytes, stable, source_lines


def nonempty(value):
    return isinstance(value,str) and len(value.strip())>=8 and value.strip() not in ('已检查完成','符合预期，无影响')


def verify_evidence(project, records):
    errors=[]
    repo=project['analysis_clone']
    snapshots=project['snapshots']
    trees={}
    for side,tree in snapshots.items():
        if not isinstance(tree,str) or not re.fullmatch(r'[0-9a-f]{40,64}',tree):
            errors.append('invalid snapshot tree: '+side); continue
        actual=git_bytes(repo,'rev-parse',tree+'^{tree}').decode('ascii').strip()
        if actual!=tree:
            errors.append('snapshot tree mismatch: '+side);continue
        files={}
        for row in git_bytes(repo,'ls-tree','-r','-z',tree).split(b'\0'):
            if not row:continue
            meta,path=row.split(b'\t',1);parts=meta.decode('ascii').split()
            if parts[1]=='blob':files[path.decode('utf-8')]=parts[2]
        trees[side]=files
    for record in records:
        side=record.get('side')
        if (side not in trees or record.get('tree')!=snapshots.get(side)
                or trees[side].get(record.get('path'))!=record.get('blob')):
            errors.append('evidence snapshot/blob mismatch: '+str(record.get('id')))
    if errors:
        return errors
    blobs=batch_blobs(repo,sorted(set(r['blob'] for r in records)))
    for record in records:
        identifier=record.get('id','')
        start,end=record.get('start_line'),record.get('end_line')
        if type(start)!=int or type(end)!=int or start<1 or end<start:
            errors.append('invalid evidence span: '+identifier);continue
        try:
            lines=source_lines(blobs[record['blob']].decode('utf-8-sig'))
        except UnicodeError:
            errors.append('evidence is not UTF-8: '+identifier);continue
        snippet=redact(''.join(lines[start-1:end]))
        if end>len(lines) or record.get('snippet')!=snippet or record.get('digest')!=stable(snippet):
            errors.append('evidence content mismatch: '+identifier)
        expected=stable(project['name'],record['tree'],record['path'],record['blob'],start,end)
        if identifier!=expected:
            errors.append('evidence id mismatch: '+identifier)
    return errors


def validate(analysis,context,review,cases,verify_sources=True):
    errors=[];warnings=[]
    if type(analysis.get('schema_version'))!=int or analysis['schema_version']!=1 or analysis.get('analyzer_version')!=VERSION:
        return ['unsupported analysis schema/analyzer version'],warnings
    projects=analysis.get('projects',[])
    if not projects or len({p['name'] for p in projects})!=len(projects):
        return ['missing or duplicate projects'],warnings
    expected=stable(VERSION,analysis['rules'],[(p['name'],p['repository'],p['snapshots']) for p in projects])
    if analysis.get('analysis_id')!=expected:
        errors.append('analysis snapshot identity mismatch')
    if review.get('analysis_id')!=analysis.get('analysis_id'):
        errors.append('review belongs to a different analysis')
    evidence={}
    for row in context+review.get('evidence',[]):
        if row.get('id') in evidence:
            errors.append('duplicate evidence id: '+str(row.get('id')))
        evidence[row.get('id')]=row
    owners={p['name']:p for p in projects}
    for row in evidence.values():
        if row.get('project') not in owners:
            errors.append('unknown evidence project: '+str(row.get('id')))
    facts={};obligations={}
    for project in projects:
        errors += ['snapshot failure: '+e for e in project.get('hard_errors',[])]
        if set(project.get('snapshots',{}))!={'baseline','result'}:
            errors.append('missing snapshots for '+project['name']);continue
        if verify_sources:
            try:
                errors+=verify_evidence(project,[r for r in evidence.values() if r.get('project')==project['name']])
            except (ValueError,OSError,KeyError) as exc:
                errors.append('cannot verify snapshot sources: '+str(exc))
        for fact in project['facts']:
            expected=stable(project['name'],fact['kind'],fact['data'],fact['evidence_ids'])
            if fact['id']!=expected or fact['id'] in facts:
                errors.append('fact identity mismatch: '+fact['id'])
            facts[fact['id']]=fact
            if not fact['evidence_ids'] or any(e not in evidence or evidence[e]['project']!=project['name']
                                              for e in fact['evidence_ids']):
                errors.append('missing or wrong-project fact evidence: '+fact['id'])
        for item in project['obligations']:
            expected=stable(project['name'],item['fact_ids'][0],item['dimension'],item['condition'],item['assertion'])
            if item['id']!=expected or item['id'] in obligations:
                errors.append('obligation identity mismatch: '+item['id'])
            obligations[item['id']]=item
            if any(f not in facts for f in item['fact_ids']):
                errors.append('unknown obligation fact: '+item['id'])
            refs=set(e for f in item['fact_ids'] if f in facts for e in facts[f]['evidence_ids'])
            if set(item['evidence_ids'])!=refs:
                errors.append('obligation evidence mismatch: '+item['id'])
        represented={stable(f['data']) for f in project['facts'] if f['kind']=='analysis_boundary'}
        if any(stable(row) not in represented for row in project.get('boundaries',[])):
            errors.append('analysis limits or parse failures lack evidence-backed boundary dispositions: '+project['name'])
    from test_points import PURPOSES, validate_cases
    try:
        validate_cases(cases,analysis)
    except (ValueError,TypeError,KeyError) as exc:
        return errors+['invalid test points: '+str(exc)],warnings
    groups=[cases['purpose_cases'][purpose] for purpose in PURPOSES]
    if any(groups):
        try:
            normalized=normalize(cases)
            rows=sum(normalized['purposes'].values(),[])
            surviving={r[0] for r in rows}
        except (ValueError,TypeError,KeyError) as exc:
            return errors+['invalid cases: '+str(exc)],warnings
    else:
        surviving=set()
    covered=set()
    for case in sum(groups,[]):
        if not case.get('case_id'):
            errors.append('stable case_id required in analysis-linked cases');continue
        if case['case_id'] not in surviving:
            continue
        links=case.get('coverage_links',[])
        for link in links:
            identifier=link.get('obligation_id')
            item=obligations.get(identifier)
            if not item:
                errors.append('unknown coverage obligation: '+str(identifier));continue
            if (link.get('condition')!=item['condition'] or link.get('assertion')!=item['assertion']
                    or set(link.get('fact_ids',[]))!=set(item['fact_ids'])):
                errors.append('coverage condition/assertion/facts mismatch: '+identifier);continue
            refs=link.get('evidence_ids',[])
            if not set(item['evidence_ids'])<=set(refs) or any(e not in evidence for e in refs):
                errors.append('coverage evidence mismatch: '+identifier);continue
            positions=link.get('observable_evidence_indices',[])
            observations=case.get('observable_evidence',[])
            if not positions or any(type(i)!=int or i<0 or i>=len(observations) for i in positions):
                errors.append('coverage needs actual observable evidence: '+identifier);continue
            text='\n'.join(case['expected']) if isinstance(case['expected'],list) else case['expected']
            text+='\n'+'\n'.join(observations[i]['assertion'] for i in positions)
            if any(message not in text for message in item['assertion'].get('errors',[]) if message):
                errors.append('case omits or contradicts extracted error result: '+identifier);continue
            covered.add(identifier)
    disposed=set()
    for disposition in review.get('dispositions',[]):
        identifier=disposition.get('obligation_id')
        item=obligations.get(identifier)
        if not item or identifier in disposed or identifier in covered:
            errors.append('unknown/duplicate disposition: '+str(identifier));continue
        status=disposition.get('status')
        if status not in ('not_applicable','reviewed_boundary'):
            errors.append('invalid disposition status: '+str(identifier));continue
        if status=='reviewed_boundary' and item['dimension'] not in (
                'review_relation','review_boundary','review_analysis_boundary','review_history','mapper_dynamic_conditions'):
            errors.append('source behavior cannot be replaced by a boundary review: '+identifier);continue
        if not nonempty(disposition.get('conclusion')) or not nonempty(disposition.get('limitations')):
            errors.append('review needs a concrete conclusion and limitations: '+identifier);continue
        refs=disposition.get('evidence_ids',[])
        if not refs or any(e not in evidence for e in refs) or not set(item['evidence_ids'])&set(refs):
            errors.append('review evidence missing or unrelated: '+identifier);continue
        if disposition.get('review_kind')!='model_source_review':
            errors.append('review must preserve model source-review provenance: '+identifier);continue
        disposed.add(identifier)
    missing=sorted(i for i,o in obligations.items() if o.get('required',True) and i not in covered|disposed)
    errors+=['unhandled obligation: '+identifier for identifier in missing]
    warnings.append('Static evidence and structured coverage checks do not prove test execution, runtime binding or business correctness.')
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
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--analysis',required=True);p.add_argument('--review',required=True);p.add_argument('--cases',required=True)
    args=p.parse_args(argv)
    try:
        errors,warnings=validate_files(args.analysis,args.review,args.cases)
    except (OSError,ValueError,KeyError,TypeError,IndexError) as exc:
        print('ERROR: '+str(exc));return 2
    for error in errors:print('ERROR: '+error)
    for warning in warnings:print('NOTE: '+warning)
    print('COVERAGE: '+('FAIL' if errors else 'PASS (static declarations only)'))
    return 1 if errors else 0


if __name__=='__main__':
    sys.exit(main())
