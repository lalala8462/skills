#!/usr/bin/env python3
"""按显式关键词或 review unit 查询完整静态事实、关系和源码证据；不做业务语义归纳。"""
import argparse
import json
import sys
from pathlib import Path


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
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as exc:
                raise ValueError('context.jsonl 第 {} 行不是合法 JSON: {}'.format(number,exc))
    return rows


def query_material(analysis,review,context,query=None,unit_id=None):
    if bool(query)==bool(unit_id):
        raise ValueError('必须且只能提供 query 或 unit_id')
    needle=query.casefold() if query else None

    facts={}
    relations={}
    for project in analysis.get('projects',[]):
        project_name=project.get('name')
        for fact in project.get('facts',[]):
            row=dict(fact)
            row['_project']=project_name
            facts[fact.get('id')]=row
        for relation in project.get('relations',[]):
            row=dict(relation)
            row['_project']=project_name
            relations[relation.get('id')]=row

    units={}
    facets={}
    navigation_ids=set()
    for project in review.get('projects',[]):
        project_name=project.get('name')
        for row in project.get('review_units',[]):
            item=dict(row)
            item['_project']=project_name
            units[row['id']]=item
        for row in project.get('review_facets',[]):
            item=dict(row)
            item['_project']=project_name
            facets[row['id']]=item
        navigation_ids.update(row.get('id') for row in project.get('navigation_facets',[]))

    matched_fact_ids=set()
    matched_relation_ids=set()
    matched_unit_ids=set()
    matched_facet_ids=set()
    matched_context_ids=set()

    if unit_id:
        if unit_id not in units:
            raise ValueError('unknown review unit: '+unit_id)
        matched_unit_ids.add(unit_id)
    else:
        matched_fact_ids.update(identifier for identifier,row in facts.items()
                                if identifier and _contains(row,needle))
        matched_relation_ids.update(identifier for identifier,row in relations.items()
                                    if identifier and _contains(row,needle))
        matched_unit_ids.update(identifier for identifier,row in units.items()
                                if _contains(row,needle))
        matched_facet_ids.update(identifier for identifier,row in facets.items()
                                 if _contains(row,needle))
        matched_context_ids.update(row.get('id') for row in context
                                   if row.get('id') and _contains(row,needle))
        if matched_context_ids:
            matched_fact_ids.update(
                identifier for identifier,row in facts.items()
                if identifier and matched_context_ids & set(row.get('evidence_ids') or []))
            matched_relation_ids.update(
                identifier for identifier,row in relations.items()
                if identifier and matched_context_ids & set(row.get('evidence_ids') or []))

    # 显式命中的事实/关系/evidence/facet 反查所属 unit。这里只扩展确定性引用，不做相似度或业务聚类。
    for identifier,row in units.items():
        if matched_fact_ids & set(row.get('fact_ids') or []):
            matched_unit_ids.add(identifier)
        if matched_relation_ids & set(row.get('relation_ids') or []):
            matched_unit_ids.add(identifier)
        if matched_facet_ids & set(row.get('facet_ids') or []):
            matched_unit_ids.add(identifier)
    for identifier in list(matched_facet_ids):
        matched_unit_ids.update(facets[identifier].get('unit_ids') or [])

    # 选中 unit 后展开它已经绑定的全部 fact/relation/facet，避免查询结果只返回局部摘要。
    expanded_fact_ids=set(matched_fact_ids)
    expanded_relation_ids=set(matched_relation_ids)
    expanded_facet_ids=set(matched_facet_ids)
    for identifier in matched_unit_ids:
        row=units[identifier]
        expanded_fact_ids.update(row.get('fact_ids') or [])
        expanded_relation_ids.update(row.get('relation_ids') or [])
        expanded_facet_ids.update(row.get('facet_ids') or [])

    evidence_ids=set(matched_context_ids)
    for identifier in expanded_fact_ids:
        row=facts.get(identifier) or {}
        evidence_ids.update(row.get('evidence_ids') or [])
    for identifier in expanded_relation_ids:
        row=relations.get(identifier) or {}
        evidence_ids.update(row.get('evidence_ids') or [])

    evidence=[row for row in context if row.get('id') in evidence_ids]
    selected_facets=[]
    for identifier in sorted(expanded_facet_ids):
        row=facets.get(identifier)
        if not row:
            continue
        item=dict(row)
        item['navigation']=identifier in navigation_ids
        selected_facets.append(item)

    cross=[]
    for row in analysis.get('cross_project_relations',[]):
        if ((query and _contains(row,needle)) or
                row.get('source_relation') in expanded_relation_ids):
            cross.append(row)

    return dict(
        query=query,
        unit_id=unit_id,
        note=('结果只基于显式关键词/复核单元和现有确定性引用展开；'
              '未命中的 analysis/review 材料仍保留在原始文件中，不表示与业务无关。'),
        matched=dict(
            fact_ids=sorted(matched_fact_ids),
            relation_ids=sorted(matched_relation_ids),
            unit_ids=sorted(matched_unit_ids),
            facet_ids=sorted(matched_facet_ids),
            context_ids=sorted(matched_context_ids)),
        review_units=[units[identifier] for identifier in sorted(matched_unit_ids)],
        review_facets=selected_facets,
        facts=[facts[identifier] for identifier in sorted(expanded_fact_ids) if identifier in facts],
        relations=[relations[identifier] for identifier in sorted(expanded_relation_ids) if identifier in relations],
        cross_project_relations=cross,
        evidence=evidence)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis',required=True)
    parser.add_argument('--review',required=True)
    parser.add_argument('--context',help='默认使用 analysis.json 同目录的 context.jsonl')
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--query',help='大小写不敏感的显式文本查询，例如字段、方法、配置键或 Mapper')
    group.add_argument('--unit',help='精确 review_unit_id')
    parser.add_argument('--output',help='输出 JSON 文件；不指定则打印到 stdout')
    args=parser.parse_args(argv)
    try:
        analysis=_load_json(args.analysis)
        review=_load_json(args.review)
        context_path=Path(args.context) if args.context else Path(args.analysis).resolve().parent/'context.jsonl'
        context=_load_context(context_path)
        result=query_material(analysis,review,context,args.query,args.unit)
        rendered=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
        if args.output:
            target=Path(args.output)
            target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('x',encoding='utf-8',newline='\n') as stream:
                stream.write(rendered)
            print('QUERY_OUTPUT: '+str(target.resolve()))
        else:
            sys.stdout.write(rendered)
        return 0
    except (OSError,UnicodeError,ValueError,KeyError,TypeError,IndexError) as exc:
        print('ERROR: '+str(exc),file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
