#!/usr/bin/env python3
"""一次调用完成固定修订取证、关联分析与覆盖义务提取。"""
import argparse
import copy
import json
import re
import subprocess
import sys
from pathlib import Path
from analysis_engine import COMMON_RULES, VERSION, ProjectAnalysis, clean, compact, redact, test_source
from collect_git import collect, read_blob
from logic_candidates import extract_candidates
from config_candidates import extract_config_candidates
from repo_access import prepare_repo, repository_name
from source_index import DEFAULT_LIMITS, JavaParser, build_index, git_bytes, stable, source_lines


def load_rules(filename=None):
    rules = copy.deepcopy(COMMON_RULES)
    if filename:
        supplied = json.loads(Path(filename).read_text(encoding='utf-8-sig'))
        if type(supplied.get('version'))!=int or supplied.get('version') != 1 or set(supplied)-set(rules):
            raise ValueError('Unsupported rule version or unknown rule keys')
        for key,value in supplied.items():
            if key in ('config_scopes','limits'):
                if not isinstance(value,dict):
                    raise ValueError('Rule '+key+' must be an object')
                rules[key].update(value)
            elif key in ('config_annotations','aliases','relation_review_tokens'):
                if not isinstance(value,list) or any(not isinstance(v,str) or not v for v in value):
                    raise ValueError('List rules must be nonempty strings')
                rules[key]=sorted(set(rules[key]+value))
    limits=dict(DEFAULT_LIMITS,**rules['limits'])
    if set(limits)!=set(DEFAULT_LIMITS) or any(type(v)!=int or v<1 for v in limits.values()):
        raise ValueError('Unknown limits or nonpositive limit')
    limits['depth']=min(limits['depth'],1)
    limits['methods']=min(limits['methods'],100)
    if any(not isinstance(k,str) or not isinstance(v,str) for k,v in rules['config_scopes'].items()):
        raise ValueError('Config scope rules must map class names to scope strings')
    return rules,limits


def revision(value):
    if isinstance(value,dict):
        return value
    if not isinstance(value,str) or not value:
        raise ValueError('Revision must be a string or {rev,parent}')
    if ':' in value and value.rsplit(':',1)[1].isdigit():
        sha,parent=value.rsplit(':',1)
        return dict(rev=sha,parent=int(parent))
    return value


def validate_project(spec):
    if not isinstance(spec,dict) or not isinstance(spec.get('repo'),str):
        raise ValueError('Each manifest project requires repo')
    if bool(spec.get('revisions')) == bool(spec.get('range')):
        raise ValueError('Specify exactly one nonempty revisions or range')
    if 'revisions' in spec and not isinstance(spec['revisions'],list):
        raise ValueError('revisions must be an array')
    if 'range' in spec and (not isinstance(spec['range'],dict) or not spec['range'].get('base') or not spec['range'].get('head')):
        raise ValueError('range requires base and head')


HISTORY_BEHAVIOR_FIELDS = {
    'branch': ('condition', 'then', 'else', 'guards'),
    'switch': ('expression', 'cases'),
    'call': ('name', 'receiver', 'arguments', 'expression', 'guards', 'usage'),
    'construct': ('type', 'arguments', 'expression', 'guards'),
    'assignment': ('target', 'expression', 'guards'),
    'field_read': ('name', 'receiver', 'expression', 'guards'),
    'return': ('expression', 'guards'),
    'throw': ('expression', 'guards'),
    'boundary': ('reason', 'expression')
}


def _normalize_history_value(value):
    if isinstance(value, str):
        return compact(value)
    if isinstance(value, list):
        return [_normalize_history_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_history_value(item) for key, item in sorted(value.items())}
    return value


def _method_behavior_projection(method, events, symbol):
    contract_fields = ('name', 'parameters', 'return_type', 'annotations', 'modifiers', 'has_body')
    contract = ({key: _normalize_history_value(method.get(key)) for key in contract_fields}
                if method else None)
    behavior = []
    for event in events:
        kind = event.get('kind')
        if event.get('method') != symbol or kind not in HISTORY_BEHAVIOR_FIELDS:
            continue
        row = {'kind': kind}
        for key in HISTORY_BEHAVIOR_FIELDS[kind]:
            if key in event:
                row[key] = _normalize_history_value(event[key])
        behavior.append(row)
    return {'contract': contract, 'events': behavior}


def group_method_timeline(history_rows):
    """Group only AST-visible behavior-change candidates by method in commit order."""
    grouped = {}
    for index, row in enumerate(history_rows):
        if row.get('kind') != 'method_behavior_change':
            continue
        key = (row.get('project'), row.get('path'), row.get('symbol'))
        group = grouped.setdefault(key, dict(project=key[0], path=key[1], symbol=key[2], changes=[]))
        before = row.get('before') or {}
        after = row.get('after') or {}
        group['changes'].append(dict(commit=row['commit'], parent=row['parent'], history_index=index,
            changed_surfaces=row['changed_surfaces'], before_digest=before.get('behavior_digest'),
            after_digest=after.get('behavior_digest')))
    return sorted(grouped.values(), key=lambda row: (row['project'], row['path'], row['symbol']))


def history(project,evidence,root,parser,cache,limits):
    timeline=[]
    for entry in evidence.get('commit_review',{}).get('commits',[]):
        parent=entry.get('parent')
        if not parent:
            continue
        sources={}; versions={};changes={};failed_files=set()
        tokens=git_bytes(root,'diff','--name-status','--find-renames','-z',parent,entry['sha']).split(b'\0')
        position=0
        while position<len(tokens) and tokens[position]:
            kind=tokens[position].decode('ascii');position+=1
            previous=tokens[position].decode('utf-8');position+=1
            current=previous
            if kind.startswith(('R','C')):
                current=tokens[position].decode('utf-8');position+=1
            changes[current]=dict(kind=kind[0],previous=previous)
        for path in sorted(entry.get('files',[])):
            if not path.endswith('.java'):
                continue
            for label,ref in [('before',parent),('after',entry['sha'])]:
                change=changes.get(path,dict(kind='M',previous=path))
                if (label=='before' and change['kind']=='A') or (label=='after' and change['kind']=='D'):
                    versions[(path,label)]=None;continue
                read_path=change['previous'] if label=='before' else path
                data,error=read_blob(root,ref,read_path)
                if error:
                    timeline.append(dict(kind='history_boundary',commit=entry['sha'],path=read_path,side=label,reason=error))
                    versions[(path,label)]=None
                    failed_files.add(path)
                    continue
                if len(data)>limits['file_bytes']:
                    timeline.append(dict(kind='history_boundary',commit=entry['sha'],path=read_path,side=label,reason='file_size_limit'))
                    failed_files.add(path)
                    continue
                try:
                    text=data.decode('utf-8-sig')
                except UnicodeError:
                    timeline.append(dict(kind='history_boundary',commit=entry['sha'],path=read_path,side=label,reason='non_UTF8_source'))
                    failed_files.add(path)
                    continue
                oid=git_bytes(root,'rev-parse',ref+':'+read_path).decode('ascii').strip()
                versions[(path,label)]=oid
                if oid not in cache:
                    sources[oid]=text
        cache.update(parser.parse(sources))
        for path in sorted({path for path,_ in versions}):
            if path in failed_files:continue
            records=[];event_records=[]
            bad=False
            for label in ('before','after'):
                parsed=cache.get(versions.get((path,label)),{})
                if parsed.get('error'):
                    timeline.append(dict(kind='history_boundary',commit=entry['sha'],path=path,side=label,reason=parsed['error']))
                    bad=True
                events=parsed.get('events',[])
                event_records.append(events)
                records.append({r['method']:r for r in events if r['kind']=='method'})
            if bad:continue
            for symbol in sorted(set(records[0])|set(records[1])):
                a,b=records[0].get(symbol),records[1].get(symbol)
                if a is None and b is not None and test_source(path):
                    continue
                if compact(a.get('source') if a else '')!=compact(b.get('source') if b else ''):
                    before_projection=_method_behavior_projection(a,event_records[0],symbol)
                    after_projection=_method_behavior_projection(b,event_records[1],symbol)
                    if stable(before_projection)==stable(after_projection):
                        continue
                    changed_surfaces=[]
                    if before_projection['contract']!=after_projection['contract']:
                        changed_surfaces.append('method_contract')
                    for kind in HISTORY_BEHAVIOR_FIELDS:
                        before_events=[row for row in before_projection['events'] if row['kind']==kind]
                        after_events=[row for row in after_projection['events'] if row['kind']==kind]
                        if before_events!=after_events:
                            changed_surfaces.append(kind)
                    def summary(row,projection):
                        return dict(body_digest=stable(compact(row['body'])),
                                    behavior_digest=stable(projection),source=clean(row['source'])) if row else None
                    call_groups=[]
                    for label in ('before','after'):
                        events=cache.get(versions.get((path,label)),{}).get('events',[])
                        groups={}
                        for call in events:
                            if call['kind']=='call' and call.get('method')==symbol:
                                groups.setdefault((call['name'],compact(call['receiver'])),[]).append(call)
                        call_groups.append(groups)
                    changes=[]
                    for key in sorted(set(call_groups[0])&set(call_groups[1])):
                        if len(call_groups[0][key])!=len(call_groups[1][key]):continue
                        for old,new in zip(call_groups[0][key],call_groups[1][key]):
                            for i,(previous,current) in enumerate(zip(old['arguments'],new['arguments'])):
                                if compact(previous)!=compact(current):
                                    changes.append(dict(call=key[0],argument_index=i,before=previous,after=current))
                    timeline.append(dict(kind='method_behavior_change',project=project,commit=entry['sha'],
                        parent=parent,path=path,symbol=symbol,changed_surfaces=changed_surfaces,
                        before=summary(a,before_projection),after=summary(b,after_projection),argument_changes=changes))
    return timeline


def analyze_project(spec,rules,limits,parser,cache):
    validate_project(spec)
    root,default_name=prepare_repo(spec['repo'])
    name=spec.get('name',default_name)
    selection=dict(name=name,path=str(root),isolated_no_checkout=True)
    if spec.get('revisions'):
        selection['revisions']=[revision(v) for v in spec['revisions']]
    else:
        selection['range']=spec['range']
    evidence=collect(selection,capture_java=True,capture_config=True,persist_snapshot=True)
    effect=evidence['net_effect']
    pairs=effect.pop('java_sources',[]); config_pairs=effect.pop('config_sources',[])
    skipped=effect.pop('java_skipped',[])+effect.pop('config_skipped',[])
    timeline=history(name,evidence,root,parser,cache,limits)
    base=dict(name=name,repository=spec['repo'],analysis_clone=str(root),git=clean(evidence),
              history=timeline,method_timeline=group_method_timeline(timeline),analyzer_version=VERSION)
    if effect['status']=='review_required':
        base.update(snapshots={},change_seeds=[],facts=[],relations=[],hard_errors=[effect['reason']],stats={})
        return base,[],None
    before=build_index(root,effect['baseline_tree'],limits,parser,cache)
    after=build_index(root,effect['result_tree'],limits,parser,cache)
    changed_paths=set(effect.get('changed_files',[]))
    changed_java_paths={path for path in changed_paths if path.lower().endswith('.java')}
    engine=ProjectAnalysis(name,root,before,after,rules,limits)
    result,context=engine.run()
    # 测试目录的补充语法提示不作为生产业务依据；既有测试变更仍由主索引记录。
    findings,ast_errors=extract_candidates([pair for pair in pairs if not test_source(pair['path'])])
    configs,config_errors=extract_config_candidates(config_pairs)
    result.update(base)
    result['candidates']=findings+configs
    result['candidate_skipped']=skipped+ast_errors+config_errors
    scope_errors=result.pop('scope_errors',[])
    evidence_omissions=result.pop('critical_evidence_omissions',[])
    changed_source_issues={row['path'] for index in (before,after) for row in index['issues']
                           if row['path'] in changed_java_paths}
    result['hard_errors']=sorted(set(
        [row['reason'] for index in (before,after) for row in index['issues']
         if 'missing source blob' in row['reason']]
        +['changed Java source could not be fully indexed: '+path for path in changed_source_issues]
        +['critical changed evidence exceeded analysis context limits: '+marker for marker in evidence_omissions]
        +scope_errors))
    # 配置文件候选同样接入证据和覆盖闭环，不能只保留旧提示。
    relevant=configs+[r for r in config_errors+skipped if r.get('review_required',True)
                      and r['path'].lower().endswith(('.properties','.yaml','.yml','.xml'))]
    if relevant:
        tree_files={}
        for side,tree in result['snapshots'].items():
            mapping={}
            for record in git_bytes(root,'ls-tree','-r','-z',tree).split(b'\0'):
                if not record:continue
                meta,path=record.split(b'\t',1);parts=meta.decode('ascii').split()
                if parts[1]=='blob':mapping[path.decode('utf-8')]=parts[2]
            tree_files[side]=mapping
        for candidate in relevant:
            refs=[]
            for side,tree in result['snapshots'].items():
                path=candidate['path'];blob=tree_files[side].get(path)
                if not blob:continue
                source,error=read_blob(root,tree,path)
                if error:
                    result['hard_errors'].append('cannot read configuration source: '+path);continue
                try:snippet=redact(source.decode('utf-8-sig'))
                except UnicodeError:
                    result['hard_errors'].append('configuration source is not UTF-8: '+path);continue
                end=len(source_lines(snippet))
                if not end:continue
                identifier=stable(name,tree,path,blob,1,end)
                if identifier not in engine.context:
                    size=len(snippet.encode('utf-8'))
                    if engine.context_bytes+size>limits['context_bytes']:
                        result['hard_errors'].append('configuration evidence exceeds context budget: '+path);continue
                    engine.context_bytes+=size
                    engine.context[identifier]=dict(id=identifier,project=name,side=side,tree=tree,path=path,blob=blob,
                        start_line=1,end_line=end,digest=stable(snippet),snippet=snippet)
                refs.append(identifier)
            if not refs:
                result['hard_errors'].append('configuration change has no source evidence: '+candidate['path']);continue
            data=clean(candidate)
            kind='configuration_file_change' if 'key' in candidate else 'configuration_parse_boundary'
            identifier=stable(name,kind,data,sorted(refs))
            fact=dict(id=identifier,kind=kind,classification='source_fact' if 'key' in candidate else 'risk_candidate',data=data,evidence_ids=sorted(refs))
            if identifier not in engine.fact_ids:engine.facts.append(fact);engine.fact_ids.add(identifier)
    if evidence.get('commit_review',{}).get('review_required') or any(row.get('kind')=='history_boundary' for row in timeline):
        row=next(iter(engine.graphs['result'].methods.values()),None)
        if row:
            engine.fact('history_boundary',dict(reasons=evidence['commit_review'].get('review_reasons',[]),
                        omissions=[item for item in timeline if item.get('kind')=='history_boundary']),
                        [('result',row)],'risk_candidate')
    result['facts']=sorted(engine.facts,key=lambda f:f['id'])
    context=list(engine.context.values())
    return result,context,engine.graphs['result']


def cross_project_links(projects,graphs):
    result=[]
    for project,graph in zip(projects,graphs):
        if graph is None:
            continue
        for edge in project['relations']:
            if edge['side']!='result':
                continue
            declarations=[graph.methods[t] for t in edge['targets'] if t in graph.methods and not graph.methods[t]['has_body']]
            for declaration in declarations:
                contract=declaration['owner']
                for other,provider in zip(projects,graphs):
                    if other['name']==project['name'] or provider is None:
                        continue
                    targets=[]
                    for symbol,method in provider.methods.items():
                        if not method['has_body'] or method['name']!=declaration['name']:
                            continue
                        if contract not in provider.parent_owners(method['owner']):
                            continue
                        if [p['type'] for p in method['parameters']]!=[p['type'] for p in declaration['parameters']]:
                            continue
                        if method['return_type']!=declaration['return_type']:
                            continue
                        targets.append(symbol)
                    if targets:
                        result.append(dict(id=stable(project['name'],other['name'],edge['id'],targets),
                            source_project=project['name'],target_project=other['name'],source_relation=edge['id'],
                            contract=contract,targets=sorted(targets),status='convention_inferred',
                            reason='matching_source_contract_not_deployment_binding'))
    return sorted(result,key=lambda x:x['id'])


def run(specs,outdir,rules_file=None):
    rules,limits=load_rules(rules_file)
    for spec in specs:
        validate_project(spec)
    names=[s['name'] if 'name' in s else repository_name(s['repo']) for s in specs]
    if any(not isinstance(n,str) or not n for n in names) or len(set(names))!=len(names):
        raise ValueError('Project names must be unique nonempty strings')
    specs=[dict(s,name=n) for s,n in zip(specs,names)]
    outdir=Path(outdir)
    if any((outdir/name).exists() for name in ('analysis.json','context.jsonl')):
        raise ValueError('Output files already exist')
    projects,contexts,graphs=[],[],[]
    parser=JavaParser()
    try:
        cache={}
        for spec in specs:
            project,context,graph=analyze_project(spec,rules,limits,parser,cache)
            projects.append(project); contexts.extend(context); graphs.append(graph)
    finally:
        parser.close()
    analysis=dict(schema_version=2,analyzer_version=VERSION,rules=rules,limits=limits,
                  status='needs_review',context_file='context.jsonl',projects=projects,
                  cross_project_relations=cross_project_links(projects,graphs),
                  note='Static source evidence for AI review; not runtime or business validation.')
    analysis['analysis_id']=stable(VERSION,rules,[(p['name'],p['repository'],p['snapshots']) for p in projects])
    outdir.mkdir(parents=True,exist_ok=True)
    with (outdir/'context.jsonl').open('x',encoding='utf-8') as out:
        for row in sorted(contexts,key=lambda r:r['id']):
            out.write(json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n')
    with (outdir/'analysis.json').open('x',encoding='utf-8') as out:
        json.dump(analysis,out,ensure_ascii=False,indent=2);out.write('\n')
    return analysis


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',help='GitHub/GitLab HTTP(S) URL or local Git worktree path');p.add_argument('--rev',action='append');p.add_argument('--base');p.add_argument('--head')
    p.add_argument('--manifest');p.add_argument('--outdir',required=True);p.add_argument('--rules')
    args=p.parse_args(argv)
    try:
        if args.manifest:
            if args.repo or args.rev or args.base or args.head:
                raise ValueError('--manifest cannot be combined with single-project selection')
            specs=json.loads(Path(args.manifest).read_text(encoding='utf-8-sig')).get('projects')
            if not isinstance(specs,list) or not specs:
                raise ValueError('manifest requires a nonempty projects array')
        else:
            if not args.repo or bool(args.rev)==bool(args.base or args.head):
                raise ValueError('Specify --repo and revisions or a complete range')
            specs=[dict(repo=args.repo,**({'revisions':args.rev} if args.rev else {'range':dict(base=args.base,head=args.head)}))]
        analysis=run(specs,args.outdir,args.rules)
        print(str((Path(args.outdir)/'analysis.json').resolve()))
        print('Collected: projects={}, facts={}; review still required'.format(
            len(analysis['projects']),sum(len(p['facts']) for p in analysis['projects'])))
        return 2 if any(project['hard_errors'] for project in analysis['projects']) else 0
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print('ERROR: '+str(exc),file=sys.stderr);return 2


if __name__=='__main__':
    sys.exit(main())
