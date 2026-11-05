#!/usr/bin/env python3
"""校验固定快照、源码证据及覆盖声明；不证明用例已执行。"""
import argparse
import json
import re
import sys
from pathlib import Path
from analysis_engine import VERSION, redact, relation_review_signature
from export_test_cases import _normalize_validated
from source_index import batch_blobs, git_bytes, stable, source_lines


def nonempty(value):
    return isinstance(value,str) and len(value.strip())>=8 and value.strip() not in ('已检查完成','符合预期，无影响')


def standalone_diagnostic_log_fact(fact):
    if fact.get('kind')!='unresolved_relation':return False
    call=fact.get('data',{}).get('call',{})
    if call.get('usage')!='EXPRESSION_STATEMENT' or call.get('name','').casefold() not in (
            'trace','debug','info','warn','error'):
        return False
    receiver=(call.get('receiver') or '').strip().rsplit('.',1)[-1].casefold()
    return receiver in ('log','logger')


def boundary_source_matches(condition, record, project):
    if record.get('project')!=project or record.get('side')!=condition.get('side'):
        return False
    location=condition.get('source_location')
    if location:
        return (record.get('path')==location['path'] and
                record.get('start_line',0)<=location['start_line']<=record.get('end_line',0))
    path=record.get('path','').replace('\\','/')
    if condition.get('path') and path==condition['path'].replace('\\','/'):
        return True
    for symbol in (condition.get('parent'),condition.get('symbol')):
        if not symbol or '#' not in symbol: continue
        owner,method=symbol.split('#',1); method=method.split('(',1)[0]
        if method=='<init>': method=owner.split('.')[-1].split('$')[-1]
        if (path.endswith(owner.split('$',1)[0].replace('.','/')+'.java') and
                re.search(r'\b'+re.escape(method)+r'\s*\(',record.get('snippet',''))):
            return True
    return False


def expand_boundary_batches(review, obligations, evidence, obligation_projects=None):
    """展开明确逐项关联证据的前后快照边界；不推断等价。"""
    expanded=[]
    batches=review.get('boundary_batches',[])
    if not isinstance(batches,list):
        raise ValueError('boundary_batches must be an array')
    for batch in batches:
        if not isinstance(batch,dict) or set(batch)!={'status','review_kind','conclusion','limitations','members'}:
            raise ValueError('invalid boundary batch fields')
        members=batch['members']
        if not isinstance(members,list) or len(members)!=2:
            raise ValueError('boundary batch requires two snapshot members')
        identity=None; batch_project=None; sides=set(); identifiers=set()
        for member in members:
            if not isinstance(member,dict) or set(member)!={'obligation_id','evidence_ids'}:
                raise ValueError('invalid boundary batch member')
            identifier=member['obligation_id']; item=obligations.get(identifier)
            if (not item or item['dimension']!='review_analysis_boundary' or
                    not item.get('required',True) or identifier in identifiers):
                raise ValueError('invalid or duplicate boundary batch obligation')
            identifiers.add(identifier)
            condition=item['condition']; side=condition.get('side')
            coordinates={k:v for k,v in condition.items() if k not in ('side','source_location')}
            if not coordinates.get('reason') or not any(coordinates.get(k) for k in ('parent','symbol','path')):
                raise ValueError('boundary batch needs a specific source location')
            if identity is None: identity=coordinates
            if identity!=coordinates or side not in ('baseline','result') or side in sides:
                raise ValueError('boundary batch members must match location and reason across snapshots')
            sides.add(side)
            refs=member['evidence_ids']
            if not isinstance(refs,list) or not refs or any(ref not in evidence for ref in refs):
                raise ValueError('boundary batch member needs valid evidence')
            original=[evidence[ref] for ref in item['evidence_ids'] if ref in evidence]
            projects=({obligation_projects[identifier]} if obligation_projects else {row.get('project') for row in original})
            if len(projects)!=1 or (item['evidence_ids'] and not set(item['evidence_ids']) & set(refs)):
                raise ValueError('boundary batch member must retain original evidence')
            project=next(iter(projects))
            if batch_project is None: batch_project=project
            if batch_project!=project:
                raise ValueError('boundary batch members must belong to the same project')
            if any(evidence[ref].get('project')!=project or evidence[ref].get('side')!=side for ref in refs):
                raise ValueError('boundary batch evidence must match the project and snapshot')
            if not any(boundary_source_matches(condition,evidence[ref],project) for ref in refs):
                raise ValueError('boundary batch member needs relevant same-snapshot source evidence')
            expanded.append(dict(obligation_id=identifier,evidence_ids=refs,status=batch['status'],
                review_kind=batch['review_kind'],conclusion=batch['conclusion'],limitations=batch['limitations']))
    return expanded


def expand_relation_batches(review, obligations, facts, evidence, obligation_projects=None):
    """仅合并绑定问题完全一致的关系义务；展开时保留每个调用点和证据引用。"""
    expanded=[]
    batches=review.get('relation_batches',[])
    if not isinstance(batches,list):
        raise ValueError('relation_batches must be an array')
    for batch in batches:
        if not isinstance(batch,dict) or set(batch)!={'status','review_kind','conclusion','limitations','members'}:
            raise ValueError('invalid relation batch fields')
        members=batch['members']
        if not isinstance(members,list) or len(members)<2:
            raise ValueError('relation batch requires at least two members')
        signature=None; batch_project=None; identifiers=set()
        for member in members:
            if not isinstance(member,dict) or set(member)!={'obligation_id','evidence_ids'}:
                raise ValueError('invalid relation batch member')
            identifier=member['obligation_id']; item=obligations.get(identifier)
            if not item or item['dimension']!='review_relation' or not item.get('required',True) or identifier in identifiers:
                raise ValueError('invalid or duplicate relation batch obligation')
            identifiers.add(identifier)
            source_facts=[facts[f] for f in item['fact_ids'] if f in facts]
            if len(source_facts)!=1 or source_facts[0].get('kind')!='unresolved_relation':
                raise ValueError('relation batch needs one unresolved relation fact per member')
            current=relation_review_signature(source_facts[0])
            if signature is None:signature=current
            if current!=signature:
                raise ValueError('relation batch members must have the same binding question')
            refs=member['evidence_ids']
            if not isinstance(refs,list) or not refs or any(ref not in evidence for ref in refs):
                raise ValueError('relation batch member needs valid evidence')
            if item['evidence_ids'] and not set(item['evidence_ids'])&set(refs):
                raise ValueError('relation batch member must retain original evidence')
            projects=({obligation_projects[identifier]} if obligation_projects else
                      {evidence[ref].get('project') for ref in item['evidence_ids'] if ref in evidence})
            if len(projects)!=1:
                raise ValueError('relation batch member must belong to one project')
            project=next(iter(projects))
            if batch_project is None:batch_project=project
            if project!=batch_project:
                raise ValueError('relation batch members must belong to the same project')
            sides={evidence[ref].get('side') for ref in refs}
            if any(evidence[ref].get('project')!=project for ref in refs) or sides!={'result'}:
                raise ValueError('relation batch evidence must match the result snapshot and project')
            expanded.append(dict(obligation_id=identifier,evidence_ids=refs,status=batch['status'],
                review_kind=batch['review_kind'],conclusion=batch['conclusion'],limitations=batch['limitations']))
    return expanded


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


def validate_direct_delivery(analysis, context, review, cases, verify_sources=True):
    """默认交付只校验最终用例实际引用的事实和源码证据，不要求关闭全部分析义务。"""
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
    from test_points import PURPOSES, validate_cases
    try:
        validate_cases(cases,analysis)
    except (ValueError,TypeError,KeyError) as exc:
        return errors+['invalid direct test cases: '+str(exc)],warnings
    facts={fact['id']:(project['name'],fact) for project in projects for fact in project.get('facts',[])}
    evidence={row.get('id'):row for row in context if row.get('id')}
    referenced_facts=set()
    referenced_evidence=set()
    for purpose in PURPOSES:
        for case in cases.get('purpose_cases',{}).get(purpose,[]):
            referenced_facts.update(case.get('fact_ids') or [])
            referenced_evidence.update(case.get('evidence_ids') or [])
    for identifier in sorted(referenced_facts):
        if identifier not in facts:
            errors.append('unknown referenced fact: '+identifier)
    for identifier in sorted(referenced_evidence):
        if identifier not in evidence:
            errors.append('unknown referenced evidence: '+identifier)
    allowed_evidence={identifier for fact_id in referenced_facts if fact_id in facts
                      for identifier in facts[fact_id][1].get('evidence_ids',[])}
    if not referenced_evidence <= allowed_evidence:
        errors.append('referenced evidence is not attached to the referenced facts')
    for project in projects:
        errors += ['snapshot failure: '+e for e in project.get('hard_errors',[])]
        if set(project.get('snapshots',{}))!={'baseline','result'}:
            errors.append('missing snapshots for '+project['name'])
            continue
        if verify_sources:
            records=[evidence[identifier] for identifier in sorted(referenced_evidence)
                     if identifier in evidence and evidence[identifier].get('project')==project['name']]
            if records:
                try:
                    errors+=verify_evidence(project,records)
                except (ValueError,OSError,KeyError) as exc:
                    errors.append('cannot verify referenced snapshot sources: '+str(exc))
    try:
        normalized=_normalize_validated(cases)
        if not any(normalized['purposes'].values()):
            warnings.append('No test cases were exported; confirm the selected scope truly requires none.')
    except (ValueError,TypeError,KeyError) as exc:
        errors.append('invalid cases: '+str(exc))
    warnings.append('Only evidence referenced by final direct cases was source-verified; unselected analysis facts are not coverage claims.')
    warnings.append('Static evidence does not prove test execution, runtime binding or business correctness.')
    return errors,warnings


def validate(analysis,context,review,cases,verify_sources=True):
    if cases.get('schema_version')==3:
        return validate_direct_delivery(analysis,context,review,cases,verify_sources)
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
    facts={};obligations={};obligation_projects={}
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
            metadata_only=(fact['kind']=='analysis_boundary' and fact.get('evidence_status')=='unavailable')
            if (not fact['evidence_ids'] and not metadata_only) or any(e not in evidence or evidence[e]['project']!=project['name']
                                              for e in fact['evidence_ids']):
                errors.append('missing or wrong-project fact evidence: '+fact['id'])
        for item in project['obligations']:
            expected=stable(project['name'],item['fact_ids'][0],item['dimension'],item['condition'],item['assertion'])
            if item['id']!=expected or item['id'] in obligations:
                errors.append('obligation identity mismatch: '+item['id'])
            obligations[item['id']]=item
            obligation_projects[item['id']]=project['name']
            required=item.get('required',True)
            if type(required)!=bool:
                errors.append('invalid obligation priority: '+item['id'])
            if not required:
                if item['dimension']=='review_analysis_boundary':
                    if (item.get('optional_reason')!='summarized_unexpanded_scope' or
                            item['condition'].get('review_required') is not False or
                            item['condition'].get('coverage_class')!='unexpanded_scope'):
                        errors.append('invalid summarized boundary classification: '+item['id'])
                elif item['dimension']=='review_relation':
                    source_facts=[facts[f] for f in item['fact_ids'] if f in facts]
                    if (item.get('optional_reason')!='standalone_diagnostic_log' or
                            not any(standalone_diagnostic_log_fact(f) for f in source_facts)):
                        errors.append('invalid low-priority relation classification: '+item['id'])
                else:
                    errors.append('unsupported non-required obligation: '+item['id'])
            if any(f not in facts for f in item['fact_ids']):
                errors.append('unknown obligation fact: '+item['id'])
            refs=set(e for f in item['fact_ids'] if f in facts for e in facts[f]['evidence_ids'])
            if set(item['evidence_ids'])!=refs:
                errors.append('obligation evidence mismatch: '+item['id'])
        represented={stable(f['data']) for f in project['facts'] if f['kind']=='analysis_boundary'}
        if any(stable(row) not in represented for row in project.get('boundaries',[])):
            errors.append('analysis limits or parse failures lack evidence-backed coverage records: '+project['name'])
    from test_points import PURPOSES, validate_cases
    try:
        validate_cases(cases,analysis)
    except (ValueError,TypeError,KeyError) as exc:
        return errors+['invalid test points: '+str(exc)],warnings
    groups=[cases['purpose_cases'][purpose] for purpose in PURPOSES]
    if any(groups):
        try:
            normalized=_normalize_validated(cases)
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
    try:
        batch_dispositions=expand_boundary_batches(review,obligations,evidence,obligation_projects)
        batch_dispositions+=expand_relation_batches(review,obligations,facts,evidence,obligation_projects)
    except (ValueError,TypeError,KeyError) as exc:
        return errors+['invalid boundary batch: '+str(exc)],warnings
    for disposition in review.get('dispositions',[])+batch_dispositions:
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
        if not refs or any(e not in evidence for e in refs) or (item['evidence_ids'] and not set(item['evidence_ids'])&set(refs)):
            errors.append('review evidence missing or unrelated: '+identifier);continue
        if item['dimension']=='review_analysis_boundary' and not any(
                boundary_source_matches(item['condition'],evidence[ref],obligation_projects[identifier]) for ref in refs):
            errors.append('boundary review needs relevant same-snapshot evidence: '+identifier);continue
        if disposition.get('review_kind')!='model_source_review':
            errors.append('review must preserve model source-review provenance: '+identifier);continue
        disposed.add(identifier)
    missing=sorted(i for i,o in obligations.items() if o.get('required',True) and i not in covered|disposed)
    errors+=['unhandled obligation: '+identifier for identifier in missing]
    summarized=sum(1 for o in obligations.values() if o['dimension']=='review_analysis_boundary' and not o.get('required',True))
    deferred_relations=sum(1 for o in obligations.values() if o['dimension']=='review_relation' and not o.get('required',True))
    if summarized or deferred_relations:
        warnings.append('Non-blocking items remain grouped as documented limitations: {} analysis boundaries, {} standalone diagnostic calls; they are not business behavior validations.'.format(summarized,deferred_relations))
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
    summarized=any('Non-blocking items remain grouped' in warning for warning in warnings)
    status='FAIL' if errors else ('PASS (static declarations only; grouped limitations reported)' if summarized else 'PASS (static declarations only)')
    print('COVERAGE: '+status)
    return 1 if errors else 0


if __name__=='__main__':
    sys.exit(main())
