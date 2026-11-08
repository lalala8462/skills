#!/usr/bin/env python3
"""一次调用完成固定修订取证、静态事实与一层关系分析。"""
import argparse
import copy
import json
import sys
from pathlib import Path
from analysis_engine import COMMON_RULES, VERSION, ProjectAnalysis, clean, redact
from collect_git import collect, read_blob
from config_changes import extract_config_changes
from repo_access import prepare_repo, repository_name, decode_git_path
from source_index import DEFAULT_LIMITS, JavaParser, build_index, git_bytes, stable, source_lines


# 明确拒绝超限，而不是静默修改用户输入。默认值仍由 DEFAULT_LIMITS 定义。
MAX_LIMITS=dict(files=20000,file_bytes=4194304,total_bytes=536870912,
                depth=3,methods=500,context_bytes=67108864)


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
            elif key in ('config_annotations','aliases'):
                if not isinstance(value,list) or any(not isinstance(v,str) or not v for v in value):
                    raise ValueError('List rules must be nonempty strings')
                rules[key]=sorted(set(rules[key]+value))
    limits=dict(DEFAULT_LIMITS,**rules['limits'])
    if set(limits)!=set(DEFAULT_LIMITS) or any(type(v)!=int or v<1 for v in limits.values()):
        raise ValueError('Unknown limits or nonpositive limit')
    exceeding=[key for key in DEFAULT_LIMITS if limits[key]>MAX_LIMITS[key]]
    if exceeding:
        raise ValueError('limits exceed supported maximum: '+', '.join(
            '{}={} > {}'.format(key,limits[key],MAX_LIMITS[key]) for key in exceeding))
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


def compact_git_evidence(evidence):
    """保留快审需要的 Git 元数据，移除 analysis.json 中的大段 diff 文本。"""
    data=clean(evidence)
    for item in data.get('selection',[]):
        item.pop('diff',None)
    net=data.get('net_effect') or {}
    net.pop('net_diff',None)
    return data


def analyze_project(spec,rules,limits,parser,cache):
    validate_project(spec)
    root,default_name=prepare_repo(spec['repo'])
    name=spec.get('name',default_name)
    selection=dict(name=name,path=str(root),isolated_no_checkout=True)
    if spec.get('revisions'):
        selection['revisions']=[revision(v) for v in spec['revisions']]
    else:
        selection['range']=spec['range']
    evidence=collect(selection,capture_config=True,persist_snapshot=True)
    effect=evidence['net_effect']
    config_pairs=effect.pop('config_sources',[])
    skipped=effect.pop('config_skipped',[])
    base=dict(name=name,repository=spec['repo'],analysis_clone=str(root),git=compact_git_evidence(evidence),
              analyzer_version=VERSION)
    if effect['status']=='review_required':
        base.update(snapshots={},change_seeds=[],facts=[],relations=[],hard_errors=[effect['reason']],stats={})
        return base,[],None
    before=build_index(root,effect['baseline_tree'],limits,parser,cache)
    after=build_index(root,effect['result_tree'],limits,parser,cache)
    changed_paths=set(effect.get('changed_files',[]))
    changed_java_paths={path for path in changed_paths if path.lower().endswith('.java')}
    changed_xml_paths={path for path in changed_paths if path.lower().endswith('.xml')}
    engine=ProjectAnalysis(name,root,before,after,rules,limits)
    result,context=engine.run()
    configs,config_errors=extract_config_changes(config_pairs)
    result.update(base)
    scope_errors=result.pop('scope_errors',[])
    evidence_omissions=result.pop('critical_evidence_omissions',[])
    changed_source_issues={row['path'] for index in (before,after) for row in index['issues']
                           if row['path'] in changed_java_paths}
    changed_xml_issues={row['path'] for index in (before,after) for row in index['issues']
                        if row['path'] in changed_xml_paths}
    limit_errors=[]
    limit_reasons={'file_count_limit','file_size_limit','total_size_limit'}
    for side,index in (('baseline',before),('result',after)):
        source_skipped=[row for row in index['issues'] if row.get('code') in limit_reasons]
        if source_skipped:
            sample=', '.join('{} ({})'.format(row['path'],row['reason']) for row in source_skipped[:3])
            limit_errors.append('source indexing limit exceeded on {}: {} file(s); examples: {}'.format(
                side,len(source_skipped),sample))
    result['hard_errors']=sorted(set(
        [row['reason'] for index in (before,after) for row in index['issues']
         if row.get('code')=='missing_source_blob']
        +['changed Java source could not be fully indexed: '+path for path in changed_source_issues]
        +['changed XML source could not be fully indexed: '+path for path in changed_xml_issues]
        +['critical changed evidence exceeded analysis context limits: '+marker for marker in evidence_omissions]
        +limit_errors+scope_errors))
    relevant=configs+[r for r in config_errors+skipped if r.get('review_required',True)
                      and r['path'].lower().endswith(('.properties','.yaml','.yml','.xml'))]
    if relevant:
        tree_files={}
        for side,tree in result['snapshots'].items():
            mapping={}
            for record in git_bytes(root,'ls-tree','-r','-z',tree).split(b'\0'):
                if not record:continue
                meta,path=record.split(b'\t',1);parts=meta.decode('ascii').split()
                if parts[1]=='blob':mapping[decode_git_path(path)]=parts[2]
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
            fact=dict(id=identifier,kind=kind,classification='source_fact' if 'key' in candidate else 'review_boundary',data=data,evidence_ids=sorted(refs))
            if identifier not in engine.fact_ids:engine.facts.append(fact);engine.fact_ids.add(identifier)
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
    analysis['analysis_id']=stable(VERSION,rules,limits,[(p['name'],p['repository'],p['snapshots']) for p in projects])
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
