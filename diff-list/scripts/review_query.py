#!/usr/bin/env python3
"""Agent 按需查证入口：先索引，再分页展开事实，关键结论回查固定源码。"""
import argparse
import base64
import binascii
import hashlib
import json
import sys
from pathlib import Path

from analysis_engine import VERSION, redact
from source_index import git_bytes, source_lines
from validate_analysis import verify_evidence
from query_metrics import metrics_path, record_query
from run_delivery import RUN_SCHEMA_VERSION

PRIMARY_KINDS={
    'method_change','field_change','class_binding_change','configuration',
    'configuration_file_change','configuration_parse_boundary','argument_source_change'
}
CONDITION_KINDS={'branch','switch_branch','membership_filter'}
DB_KINDS={'mapper_statement','field_source'}
VIEW_CHOICES=('index','facts','relations','evidence','db','calls','conditions','all')
AUDIT_VIEWS=(
    'missing_seeds','unassigned_primary_facts','unindexed_facts','unindexed_relations',
    'unresolved_relations','missing_evidence','unreferenced_evidence',
    'evidence_project_mismatch','hard_errors','invalid_unit_references',
    'cross_source_missing'
)
NOTE=('结果基于确定性索引或显式匹配；分类视图不代表完整业务影响。'
      '未命中的事实、关系及源码仍保留在 analysis.json / context.jsonl。')


def _is_direct_change_fact(row):
    if row.get('kind')=='configuration':
        # 配置读取被纳入分析，并不代表配置值发生变化。
        return (row.get('data') or {}).get('change') in ('added','removed')
    return row.get('kind') in PRIMARY_KINDS


def _relation_tier(row,seeds,project):
    touched=((project,row.get('source')) in seeds or any(
        (project,symbol) in seeds for symbol in row.get('targets') or []))
    return 'change_touched_boundary' if touched else 'supporting_context'


def _contains(value,needle):
    if isinstance(value,str):
        return needle in value.casefold()
    if isinstance(value,dict):
        return any(_contains(key,needle) or _contains(item,needle) for key,item in value.items())
    if isinstance(value,(list,tuple,set)):
        return any(_contains(item,needle) for item in value)
    return False


def _load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _load_context(path):
    rows=[]
    with Path(path).open('r',encoding='utf-8-sig') as stream:
        for number,line in enumerate(stream,1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError as exc:
                    raise ValueError('context.jsonl 第 {} 行不是合法 JSON: {}'.format(number,exc))
    return rows


def _unique_rows(rows,label):
    result={}
    for row in rows:
        identifier=row.get('id')
        if not isinstance(identifier,str) or not identifier:
            raise ValueError(label+' 缺少 id')
        if identifier in result:
            raise ValueError(label+' id 重复: '+identifier)
        result[identifier]=row
    return result


def _indexes(analysis,review,context):
    if analysis.get('schema_version')!=2 or analysis.get('analyzer_version')!=VERSION:
        raise ValueError('analysis schema/analyzer version mismatch')
    if review.get('review_schema_version')!=4 or review.get('analysis_id')!=analysis.get('analysis_id'):
        raise ValueError('review schema 或 analysis_id 不匹配')
    if not analysis.get('projects'):
        raise ValueError('analysis missing projects')
    facts={}
    relations={}
    units={}
    facets={}
    navigation=set()
    projects={}
    for project in analysis.get('projects',[]):
        name=project['name']
        if name in projects:
            raise ValueError('duplicate project: '+name)
        projects[name]=project
        for key,collector in (('facts',facts),('relations',relations)):
            for row in project.get(key,[]):
                identifier=row['id']
                if identifier in collector:
                    raise ValueError('duplicate '+key+' id: '+identifier)
                collector[identifier]=dict(row,_project=name)
    for project in review.get('projects',[]):
        name=project['name']
        if name not in projects:
            raise ValueError('unknown review project: '+name)
        for key,collector in (('review_units',units),('review_facets',facets)):
            for row in project.get(key,[]):
                identifier=row['id']
                if identifier in collector:
                    raise ValueError('duplicate '+key+' id: '+identifier)
                collector[identifier]=dict(row,_project=name)
        navigation.update(row['id'] for row in project.get('navigation_facets',[]))
    evidence=_unique_rows(context,'evidence')
    return dict(facts=facts,relations=relations,units=units,facets=facets,
                navigation=navigation,evidence=evidence,projects=projects)


def _lookup_selection(indexes,analysis,query=None,unit_id=None):
    if bool(query)==bool(unit_id):
        raise ValueError('必须且只能提供 query 或 unit')
    facts=indexes['facts']
    relations=indexes['relations']
    units=indexes['units']
    facets=indexes['facets']
    evidence=indexes['evidence']
    needle=query.casefold() if query else None
    matched={key:set() for key in ('facts','relations','units','facets','evidence')}
    if unit_id:
        if unit_id not in units:
            raise ValueError('unknown review unit: '+unit_id)
        matched['units'].add(unit_id)
    else:
        for key,collection in (('facts',facts),('relations',relations),('units',units),
                               ('facets',facets),('evidence',evidence)):
            matched[key].update(identifier for identifier,row in collection.items()
                                if _contains(row,needle))
        # Evidence 命中必须反查到所属事实/关系，不能仅返回孤立 snippet。
        for key,collection in (('facts',facts),('relations',relations)):
            matched[key].update(identifier for identifier,row in collection.items()
                if set(row.get('evidence_ids') or []) & matched['evidence'])

        for identifier,row in units.items():
            if (set(row.get('fact_ids') or []) & matched['facts']
                    or set(row.get('relation_ids') or []) & matched['relations']
                    or set(row.get('facet_ids') or []) & matched['facets']):
                matched['units'].add(identifier)
        for identifier in matched['facets']:
            matched['units'].update(facets[identifier].get('unit_ids') or [])

    expanded={key:set(value) for key,value in matched.items()}
    absent_units=sorted(matched['units']-set(units))
    if absent_units:
        raise ValueError('review facet 引用未知 unit，请先执行 --audit: '+','.join(absent_units))
    for identifier in matched['units']:
        row=units[identifier]
        expanded['facts'].update(row.get('fact_ids') or [])
        expanded['relations'].update(row.get('relation_ids') or [])
        expanded['facets'].update(row.get('facet_ids') or [])
    for identifier in expanded['facts']:
        if identifier in facts:
            expanded['evidence'].update(facts[identifier].get('evidence_ids') or [])
    for identifier in expanded['relations']:
        if identifier in relations:
            expanded['evidence'].update(relations[identifier].get('evidence_ids') or [])
    for key,collection in (('facts',facts),('relations',relations),
                           ('facets',facets),('evidence',evidence)):
        missing=sorted(expanded[key]-set(collection))
        if missing:
            raise ValueError('索引引用未知 {}，请先执行 --audit: {}'.format(key,','.join(missing)))
    cross=[row for row in analysis.get('cross_project_relations',[])
           if row.get('source_relation') in expanded['relations']
           or (needle and _contains(row,needle))]
    return matched,expanded,cross


def query_material(analysis,review,context,query=None,unit_id=None):
    """完整展开 API，供显式请求和回归验证；CLI 默认不输出这个大对象。"""
    indexes=_indexes(analysis,review,context)
    matched,expanded,cross=_lookup_selection(indexes,analysis,query,unit_id)
    mapped=dict(fact_ids='facts',relation_ids='relations',unit_ids='units',
                facet_ids='facets',context_ids='evidence')
    selected_facets=[]
    for identifier in sorted(expanded['facets']):
        if identifier in indexes['facets']:
            selected_facets.append(dict(indexes['facets'][identifier],
                                        navigation=identifier in indexes['navigation']))
    return dict(query=query,unit_id=unit_id,note=NOTE,
        matched={key:sorted(matched[value]) for key,value in mapped.items()},
        review_units=[indexes['units'][i] for i in sorted(matched['units']) if i in indexes['units']],
        review_facets=selected_facets,
        facts=[indexes['facts'][i] for i in sorted(expanded['facts']) if i in indexes['facts']],
        relations=[indexes['relations'][i] for i in sorted(expanded['relations']) if i in indexes['relations']],
        cross_project_relations=cross,
        evidence=[indexes['evidence'][i] for i in sorted(expanded['evidence']) if i in indexes['evidence']])


def _unit_index(row,indexes):
    fact_ids=set(row.get('fact_ids') or [])
    direct_fact_ids=sorted(set(row.get('direct_fact_ids') or []))
    context_fact_ids=set(row.get('context_fact_ids') or [])
    relation_ids=set(row.get('relation_ids') or [])
    direct_evidence_ids=set()
    for fact_id in direct_fact_ids:
        fact=indexes['facts'].get(fact_id)
        if fact:
            direct_evidence_ids.update(fact.get('evidence_ids') or [])
    counts={}
    evidence_ids=set()
    for identifier in fact_ids:
        fact=indexes['facts'].get(identifier)
        if fact:
            kind=fact.get('kind','unknown')
            counts[kind]=counts.get(kind,0)+1
            evidence_ids.update(fact.get('evidence_ids') or [])
    for identifier in relation_ids:
        relation=indexes['relations'].get(identifier)
        if relation:
            evidence_ids.update(relation.get('evidence_ids') or [])
    missing_refs=(fact_ids-set(indexes['facts'])) | (relation_ids-set(indexes['relations'])) | (
        set(row.get('facet_ids') or [])-set(indexes['facets']))
    missing_refs.update(set(direct_fact_ids)-set(indexes['facts']))
    missing_refs.update(direct_evidence_ids-set(indexes['evidence']))
    return dict(unit_id=row['id'],project=row['_project'],scope=row.get('scope'),
        symbol=row.get('symbol'),direct_change_count=len(row.get('change_summary') or []),
        direct_fact_ids=direct_fact_ids,
        direct_evidence_ids=sorted(direct_evidence_ids),
        context_fact_count=len(context_fact_ids),
        invalid_reference_count=len(missing_refs),
        fact_count=len(fact_ids),relation_count=len(relation_ids),
        evidence_count=len(evidence_ids),facet_count=len(row.get('facet_ids') or []),
        unresolved_relation_count=sum(
            1 for i in relation_ids if i in indexes['relations']
            and indexes['relations'][i].get('status')=='unresolved'),
        fact_kind_counts=dict(sorted(counts.items())),
        navigation_facet_ids=row.get('navigation_facet_ids') or [])


def _paginate(rows,analysis_id,scope,limit,cursor):
    # Token 绑定范围和内容 ID，材料变化或跨视图重用会直接拒绝。
    signature=hashlib.sha256(json.dumps(
        [analysis_id,scope,limit,rows],ensure_ascii=False,
        sort_keys=True).encode('utf-8')).hexdigest()[:20]
    start=0
    if cursor:
        try:
            raw=base64.urlsafe_b64decode(cursor.encode('ascii')).decode('ascii')
            number,digest=raw.split(':',1)
            start=int(number)
        except (ValueError,UnicodeError,TypeError,binascii.Error) as exc:
            raise ValueError('invalid cursor: '+str(exc))
        if start<0 or start>len(rows) or digest!=signature:
            raise ValueError('cursor 与当前分析范围/视图/内容不匹配')
    selected=rows[start:start+limit]
    end=start+len(selected)
    token=(base64.urlsafe_b64encode('{}:{}'.format(end,signature).encode('ascii'))
           .decode('ascii') if end<len(rows) else None)
    return selected,dict(total=len(rows),returned=len(selected),
        offset=start,complete=token is None,next_cursor=token)


def _audit_rows(analysis,indexes):
    all_units=indexes['units']
    facts=indexes['facts']
    relations=indexes['relations']
    context=indexes['evidence']
    assigned_facts={i for row in all_units.values() for i in row.get('fact_ids') or []}
    assigned_relations={i for row in all_units.values() for i in row.get('relation_ids') or []}
    seeds={(p['name'],symbol) for p in analysis.get('projects',[])
           for symbol in p.get('change_seeds',[])}
    indexed={(row['_project'],row.get('symbol')) for row in all_units.values()
             if row.get('scope')=='method'}
    result={key:[] for key in AUDIT_VIEWS}
    result['missing_seeds']=[dict(project=name,symbol=symbol)
                             for name,symbol in sorted(seeds-indexed)]
    for identifier,row in sorted(facts.items()):
        if identifier not in assigned_facts:
            direct=_is_direct_change_fact(row)
            item=dict(id=identifier,project=row['_project'],kind=row.get('kind'),
                      scope_tier='direct_change' if direct else 'supporting_context')
            result['unindexed_facts'].append(item)
            if direct:
                result['unassigned_primary_facts'].append(item)
    for identifier,row in sorted(relations.items()):
        if identifier not in assigned_relations:
            result['unindexed_relations'].append(dict(
                id=identifier,project=row['_project'],source=row.get('source')))
        if row.get('status')=='unresolved':
            result['unresolved_relations'].append(dict(id=identifier,project=row['_project'],
                source=row.get('source'),reason=row.get('reason'),
                scope_tier=_relation_tier(row,seeds,row['_project'])))
    referenced_evidence=set()
    # 按接近最终净变化的程度排序，但绝不丢弃背景 unresolved。
    result['unresolved_relations'].sort(key=lambda item:(
        item['scope_tier']!='change_touched_boundary',item['project'],item['id']))
    for key,collection in (('fact',facts),('relation',relations)):
        for identifier,row in sorted(collection.items()):
            evidence_ids=set(row.get('evidence_ids') or [])
            referenced_evidence.update(evidence_ids)
            missing=sorted(evidence_ids-set(context))
            if missing or not evidence_ids:
                result['missing_evidence'].append(dict(kind=key,id=identifier,
                    missing_ids=missing,no_evidence=not bool(evidence_ids)))
            for evidence_id in sorted(evidence_ids & set(context)):
                if context[evidence_id].get('project')!=row['_project']:
                    result['evidence_project_mismatch'].append(dict(kind=key,
                        id=identifier,evidence_id=evidence_id,
                        expected_project=row['_project'],
                        actual_project=context[evidence_id].get('project')))
    for evidence_id in sorted(set(context)-referenced_evidence):
        result['unreferenced_evidence'].append(dict(
            id=evidence_id,project=context[evidence_id].get('project')))
    for identifier,row in sorted(all_units.items()):
        for key,collection in (('fact_ids',facts),('relation_ids',relations),
                               ('facet_ids',indexes['facets'])):
            missing=sorted(set(row.get(key) or [])-set(collection))
            if missing:
                result['invalid_unit_references'].append(dict(unit_id=identifier,
                    type=key,missing_ids=missing))
            foreign=sorted(ref for ref in set(row.get(key) or [])
                           if ref in collection and collection[ref]['_project']!=row['_project'])
            if foreign:
                result['invalid_unit_references'].append(dict(unit_id=identifier,
                    type=key,foreign_project_ids=foreign))
    for project in analysis.get('projects',[]):
        name=project['name']
        for message in project.get('hard_errors',[]):
            result['hard_errors'].append(dict(project=name,message=message))
    for cross in analysis.get('cross_project_relations',[]):
        if cross.get('source_relation') not in relations:
            result['cross_source_missing'].append(dict(
                id=cross.get('id'),source_relation=cross.get('source_relation')))
    return result


def audit_material(analysis,review,context):
    indexes=_indexes(analysis,review,context)
    groups=_audit_rows(analysis,indexes)
    counts={key:len(rows) for key,rows in groups.items()}
    tiers={
        'direct_change_unassigned':counts['unassigned_primary_facts'],
        'change_touched_unresolved':sum(
            row['scope_tier']=='change_touched_boundary' for row in groups['unresolved_relations']),
        'supporting_unresolved':sum(
            row['scope_tier']=='supporting_context' for row in groups['unresolved_relations']),
        'supporting_unindexed':sum(
            row['scope_tier']=='supporting_context' for row in groups['unindexed_facts']),
        'unreferenced_evidence':counts['unreferenced_evidence']
    }
    attention_kinds={
        'missing_seeds','unassigned_primary_facts',
        'missing_evidence','evidence_project_mismatch',
        'hard_errors','invalid_unit_references','cross_source_missing'
    }
    return dict(analysis_id=analysis['analysis_id'],
        scope=dict(project_count=len(indexes['projects']),unit_count=len(indexes['units']),
                   fact_count=len(indexes['facts']),relation_count=len(indexes['relations']),
                   evidence_count=len(indexes['evidence']),
                   limits=analysis.get('limits') or {}),
        counts=counts,scope_tiers=tiers,
        requires_attention=(any(counts[key] for key in attention_kinds)
                            or tiers['change_touched_unresolved']>0),
        informational_only_views=['unindexed_facts','unindexed_relations','unreferenced_evidence'],
        available_views=list(AUDIT_VIEWS),
        note=('直接变化和触及变更方法的未解析关系优先展示；'
              '配置读取未改变时属于背景，不计为未归属的主要变化。'
              '背景 unresolved 与全部事实仍可分页回查；'
              '审计只验证结构/可见边界，不证明业务场景完整。'))


def _evidence_source(analysis,indexes,identifier,context_lines):
    record=indexes['evidence'].get(identifier)
    if record is None:
        raise ValueError('unknown evidence: '+identifier)
    project=indexes['projects'].get(record.get('project'))
    if project is None:
        raise ValueError('evidence project missing: '+identifier)
    errors=verify_evidence(project,[record])
    if errors:
        raise ValueError('固定快照证据校验失败: '+'; '.join(errors))
    blob=record['blob']
    raw=git_bytes(project['analysis_clone'],'cat-file','blob',blob)
    lines=source_lines(raw.decode('utf-8-sig'))
    start=max(1,record['start_line']-context_lines)
    end=min(len(lines),record['end_line']+context_lines)
    return dict(id=identifier,record=record,snapshot_verified=True,
        source=dict(project=record['project'],side=record['side'],
                    tree=record['tree'],blob=blob,path=record['path'],
                    start_line=start,end_line=end,
                    redacted=True,content=redact(''.join(lines[start-1:end]))),
        note='内容从固定 Git blob 读取并脱敏；需要更大范围可提高 --source-lines。')


def _render_request(args,analysis,review,context):
    indexes=_indexes(analysis,review,context)
    if args.fact:
        fact=indexes['facts'].get(args.fact)
        if fact is None:
            raise ValueError('unknown fact: '+args.fact)
        owners=[key for key,row in indexes['units'].items()
                if args.fact in (row.get('fact_ids') or [])]
        evidence_ids=fact.get('evidence_ids') or []
        missing=sorted(set(evidence_ids)-set(indexes['evidence']))
        if missing:
            raise ValueError('fact 引用未知 evidence，请先执行 --audit: '+','.join(missing))
        return dict(mode='fact',analysis_id=analysis['analysis_id'],
                    fact=fact,unit_ids=sorted(owners),
                    evidence_ids=evidence_ids,complete=True,note=NOTE)
    if args.evidence:
        return dict(mode='evidence',analysis_id=analysis['analysis_id'],
                    **_evidence_source(analysis,indexes,args.evidence,args.source_lines))
    if args.audit:
        summary=audit_material(analysis,review,context)
        if args.view=='index':
            return dict(mode='audit',**summary)
        rows=_audit_rows(analysis,indexes)[args.view]
        selected,page=_paginate(rows,analysis['analysis_id'],'audit:'+args.view,
                                args.limit,args.cursor)
        return dict(mode='audit',view=args.view,analysis_id=analysis['analysis_id'],
                    items=selected,page=page,note=summary['note'])
    if args.overview:
        if args.view!='index':
            raise ValueError('--overview 只支持 --view index')
        items=[_unit_index(row,indexes) for _,row in sorted(indexes['units'].items())]
        selected,page=_paginate(items,analysis['analysis_id'],'overview',args.limit,args.cursor)
        audit=audit_material(analysis,review,context)
        return dict(mode='overview',analysis_id=analysis['analysis_id'],
                    projects=sorted(indexes['projects']),items=selected,page=page,
                    totals=audit['scope'],audit_counts=audit['counts'],note=NOTE)

    matched,expanded,cross=_lookup_selection(indexes,analysis,args.query,args.unit)
    matched_only=getattr(args,'matched_only',False)
    if matched_only and (not args.query or args.view!='facts'):
        raise ValueError('--matched-only 只能与 --query 和 --view facts 一起使用')
    match_summary={key+'_count':len(value) for key,value in matched.items()}
    if args.view=='index':
        items=[_unit_index(indexes['units'][i],indexes)
               for i in sorted(matched['units']) if i in indexes['units']]
    elif args.view=='facts':
        selected_ids=matched['facts'] if matched_only else expanded['facts']
        items=[indexes['facts'][i] for i in sorted(selected_ids) if i in indexes['facts']]
    elif args.view=='relations':
        items=[indexes['relations'][i] for i in sorted(expanded['relations'])
               if i in indexes['relations']]
    elif args.view=='evidence':
        items=[indexes['evidence'][i] for i in sorted(expanded['evidence'])
               if i in indexes['evidence']]
    elif args.view=='calls':
        items=[indexes['relations'][i] for i in sorted(expanded['relations'])
               if i in indexes['relations']]
    elif args.view=='db':
        items=[indexes['facts'][i] for i in sorted(expanded['facts'])
               if i in indexes['facts'] and indexes['facts'][i].get('kind') in DB_KINDS]
    elif args.view=='conditions':
        items=[indexes['facts'][i] for i in sorted(expanded['facts'])
               if i in indexes['facts'] and indexes['facts'][i].get('kind') in CONDITION_KINDS]
    else:
        # 完整展开仅当 Agent 明确指定 --view all 时提供；大型范围应优先分页视图。
        return dict(mode='lookup',view='all',analysis_id=analysis['analysis_id'],
                    complete=True,**query_material(analysis,review,context,args.query,args.unit))
    selected,page=_paginate(items,analysis['analysis_id'],
        'lookup:{}:{}:{}:{}'.format(args.unit or '',args.query or '',args.view,matched_only),
        args.limit,args.cursor)
    return dict(mode='lookup',query=args.query,unit_id=args.unit,view=args.view,
                matched_only=matched_only,
                related_unit_ids=sorted(matched['units']) if matched_only else [],
                analysis_id=analysis['analysis_id'],matched=match_summary,
                selected_totals={key+'_count':len(value) for key,value in expanded.items()},
                cross_project_relation_count=len(cross),
                items=selected,page=page,
                category_is_exhaustive=(not matched_only and args.view in ('facts','relations','evidence','calls')),
                note=NOTE)


def _record_usage(path,args,analysis_id,status,bytes_generated,bytes_printed,item_count):
    try:
        record_query(path,args,analysis_id,status,bytes_generated,bytes_printed,item_count)
    except (OSError,ValueError,TypeError) as exc:
        print('WARNING: 查询统计写入失败: '+str(exc),file=sys.stderr)


def _item_count(result):
    if isinstance(result.get('items'),list):
        return len(result['items'])
    if result.get('mode') in ('fact','evidence'):
        return 1
    if result.get('view')=='all':
        return sum(len(result.get(key) or []) for key in (
            'review_units','review_facets','facts','relations','cross_project_relations','evidence'))
    return 0


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis',required=True)
    parser.add_argument('--review',required=True)
    parser.add_argument('--context',help='默认使用 analysis.json 同目录的 context.jsonl')
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--overview',action='store_true',help='全局 unit 索引及边界计数')
    group.add_argument('--audit',action='store_true',help='全局完整性/分析边界审计')
    group.add_argument('--query',help='显式字段、方法、Mapper 或配置关键字')
    group.add_argument('--unit',help='精确 review_unit_id')
    group.add_argument('--fact',help='完整 fact 及其证据引用')
    group.add_argument('--evidence',help='校验并读取固定 Git 快照中的源码证据')
    parser.add_argument('--view',default='index',help='查询层级：index/facts/relations/evidence/db/calls/conditions/all；audit 可查看异常分类')
    parser.add_argument('--limit',type=int,default=10,help='每页最多 10 项，可按需提高至 100')
    parser.add_argument('--cursor',help='上一页返回的 next_cursor')
    parser.add_argument('--matched-only',action='store_true',
                        help='只返回关键词真正命中的 fact，不展开关联 unit；需搭配 --query --view facts')
    parser.add_argument('--source-lines',type=int,default=8,
                        help='evidence 前后各补充的源码行数（0-100）')
    parser.add_argument('--output',help='写入新的 JSON 文件，否则打印 stdout')
    args=parser.parse_args(argv)
    event_path=None
    analysis_id=''
    try:
        event_path=metrics_path(args.analysis,args.review,RUN_SCHEMA_VERSION)
    except (OSError,ValueError,TypeError,UnicodeError) as exc:
        print('WARNING: 无法定位查询统计目录: '+str(exc),file=sys.stderr)
    try:
        if args.matched_only and (not args.query or args.view!='facts'):
            raise ValueError('--matched-only 只能与 --query 和 --view facts 一起使用')
        if not 1<=args.limit<=100 or not 0<=args.source_lines<=100:
            raise ValueError('--limit 必须在 1-100，--source-lines 必须在 0-100')
        if args.audit:
            if args.view!='index' and args.view not in AUDIT_VIEWS:
                raise ValueError('--audit --view 支持: '+','.join(AUDIT_VIEWS))
            if args.view=='index' and args.cursor:
                raise ValueError('--audit 概览不支持 cursor，请在 --view 分类中分页')
        elif args.view not in VIEW_CHOICES:
            raise ValueError('未知 --view: '+args.view)
        if (args.fact or args.evidence) and (args.view!='index' or args.cursor):
            raise ValueError('--fact/--evidence 不支持 --view/--cursor')
        if args.view=='all' and args.cursor:
            raise ValueError('--view all 不支持 cursor；优先使用分页视图')
        analysis=_load_json(args.analysis)
        review=_load_json(args.review)
        analysis_path=Path(args.analysis).resolve()
        context_path=(Path(args.context).resolve() if args.context else
                      analysis_path.parent/(analysis.get('context_file') or 'context.jsonl'))
        if context_path.resolve().parent!=analysis_path.parent:
            raise ValueError('context.jsonl 必须与 analysis.json 位于同一目录')
        context=_load_context(context_path)
        analysis_id=analysis.get('analysis_id') or ''
        result=_render_request(args,analysis,review,context)
        rendered=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
        output_bytes=len(rendered.encode('utf-8'))
        if args.output:
            target=Path(args.output)
            target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('x',encoding='utf-8',newline='\n') as stream:
                stream.write(rendered)
            printed='QUERY_OUTPUT: '+str(target.resolve())+'\n'
            sys.stdout.write(printed)
            stdout_bytes=len(printed.encode('utf-8'))
        else:
            sys.stdout.write(rendered)
            stdout_bytes=output_bytes
        _record_usage(event_path,args,analysis_id,'ok',output_bytes,stdout_bytes,
                      _item_count(result))
        return 0
    except (OSError,UnicodeError,ValueError,KeyError,TypeError,IndexError) as exc:
        message='ERROR: '+str(exc)+'\n'
        sys.stderr.write(message)
        _record_usage(event_path,args,analysis_id,'error',0,0,0)
        return 2


if __name__=='__main__':
    sys.exit(main())
