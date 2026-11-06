#!/usr/bin/env python3
"""从 analysis.json 生成按变化点聚合的一层快审材料。"""
import argparse
import json
import sys
from pathlib import Path

from analysis_engine import VERSION


def normal_flow_name(symbol):
    surface=(symbol or '').casefold()
    if any(token in surface for token in ('refund','refundable')):
        return '退款主流程'
    if any(token in surface for token in ('notice','notify','callback','seefee')):
        return '通知与回调主流程'
    if any(token in surface for token in ('uniquecontrol','billno','unique_bill')):
        return '渠道流水号主流程'
    if any(token in surface for token in ('onecode','qrcode','aggregatepay','payment')) or 'pay' in surface:
        return '聚合码支付主流程'
    owner=(symbol or '').split('#',1)[0].rsplit('.',1)[-1]
    return '其他关键流程：'+(owner or '未定位')


def fact_symbols(fact):
    """提取事实中明确绑定的方法符号，不通过共享 evidence 扩大范围。"""
    data=fact.get('data') or {}
    result=set()
    def add(value):
        if isinstance(value,str) and '#' in value:
            result.add(value)
    add(data.get('symbol'))
    for key in ('consumers','linked_symbols'):
        values=data.get(key) or []
        if isinstance(values,list):
            for value in values:
                if isinstance(value,dict):
                    add(value.get('symbol'))
                else:
                    add(value)
    for side in ('before','after'):
        value=data.get(side)
        if not isinstance(value,dict):
            continue
        add(value.get('symbol'))
        for consumer in value.get('consumers') or []:
            if isinstance(consumer,dict):
                add(consumer.get('symbol'))
            else:
                add(consumer)
    return result


def fact_group_summary(facts):
    groups={}
    for fact in facts:
        kind=fact.get('kind','unknown')
        group=groups.setdefault(kind,dict(count=0,fact_ids=[]))
        group['count']+=1
        group['fact_ids'].append(fact['id'])
    for group in groups.values():
        group['fact_ids'].sort()
    return dict(sorted(groups.items()))


def _preview_value(value,depth=0):
    if depth>=2:
        if isinstance(value,(dict,list)):
            return '<details in analysis.json>'
        return value[:240]+'…' if isinstance(value,str) and len(value)>240 else value
    if isinstance(value,str):
        return value[:320]+'…' if len(value)>320 else value
    if isinstance(value,list):
        rows=[_preview_value(item,depth+1) for item in value[:8]]
        if len(value)>8:
            rows.append('<{} more>'.format(len(value)-8))
        return rows
    if isinstance(value,dict):
        preferred=('symbol','change','owner','field','key','path','scope','name','statement',
                   'condition','expression','then','otherwise','source','target','receiver',
                   'receiver_type','status','reason','origins','consumers','linked_symbols',
                   'before','after','default','usage','member','collection')
        keys=[key for key in preferred if key in value]
        keys.extend(key for key in sorted(value) if key not in keys)
        result={}
        for key in keys[:14]:
            result[key]=_preview_value(value[key],depth+1)
        if len(keys)>14:
            result['_omitted_keys']=len(keys)-14
        return result
    return value


def fact_preview(fact):
    """快审只保留事实关键字段；完整 data 在 analysis.json 中按 fact id 回查。"""
    return dict(id=fact['id'],kind=fact.get('kind'),classification=fact.get('classification'),
                data=_preview_value(fact.get('data') or {}),
                evidence_ids=sorted(fact.get('evidence_ids') or []))


def relation_preview(relation):
    site=relation.get('site') or {}
    compact_site={key:_preview_value(site[key]) for key in
                  ('name','receiver','expression','arguments','usage') if key in site}
    return dict(id=relation.get('id'),side=relation.get('side'),source=relation.get('source'),
                targets=relation.get('targets') or [],status=relation.get('status'),
                reason=relation.get('reason'),receiver_type=relation.get('receiver_type'),
                site=compact_site,evidence_ids=sorted(relation.get('evidence_ids') or []))


def build_review(analysis):
    if analysis.get('schema_version')!=2 or analysis.get('analyzer_version')!=VERSION:
        raise ValueError('unsupported analysis schema/analyzer version')
    projects=[]
    selected_relation_ids=set()
    primary_kinds={
        'method_change','field_change','class_binding_change','configuration',
        'configuration_file_change','configuration_parse_boundary',
        'argument_source_change'
    }
    supporting_kinds={
        'branch','switch_branch','membership_filter','return_sources','return_override',
        'field_source','flow_boundary','mapper_statement','unresolved_relation',
        'annotation_binding_unconfigured','entrypoint','constant_or_collection',
        'object_copy','constructor_mapping'
    }
    allowed_kinds=primary_kinds|supporting_kinds

    for source in analysis['projects']:
        facts=[fact for fact in source['facts'] if fact.get('kind') in allowed_kinds]
        facts_by_id={fact['id']:fact for fact in facts}
        symbols_by_fact={fact['id']:fact_symbols(fact) for fact in facts}
        change_seeds=sorted(set(source['change_seeds']))
        relations=list(source['relations'])

        units=[]
        selected_fact_ids=set()
        selected_relations={}
        for symbol in change_seeds:
            touching=[relation for relation in relations
                      if relation.get('source')==symbol or symbol in (relation.get('targets') or [])]
            local_symbols={symbol}
            for relation in touching:
                if relation.get('source'):
                    local_symbols.add(relation['source'])
                local_symbols.update(relation.get('targets') or [])
                if relation.get('id'):
                    selected_relations[relation['id']]=relation
            unit_facts=[fact for fact in facts
                        if symbols_by_fact[fact['id']] & local_symbols]
            unit_fact_ids=sorted(fact['id'] for fact in unit_facts)
            selected_fact_ids.update(unit_fact_ids)
            evidence_ids=sorted(
                {identifier for fact in unit_facts for identifier in fact.get('evidence_ids',[])}
                | {identifier for relation in touching for identifier in relation.get('evidence_ids',[])})
            units.append(dict(
                symbol=symbol,
                flow=normal_flow_name(symbol),
                related_symbols=sorted(local_symbols-{symbol}),
                fact_groups=fact_group_summary(unit_facts),
                fact_ids=unit_fact_ids,
                relation_ids=sorted(relation['id'] for relation in touching if relation.get('id')),
                evidence_ids=evidence_ids))

        project_facts=[fact for fact in facts
                       if fact.get('kind') in primary_kinds and not symbols_by_fact[fact['id']]]
        selected_fact_ids.update(fact['id'] for fact in project_facts)
        fact_catalog=[fact_preview(facts_by_id[identifier]) for identifier in sorted(selected_fact_ids)]
        relation_catalog=[relation_preview(selected_relations[identifier]) for identifier in sorted(selected_relations)]
        selected_relation_ids.update(selected_relations)
        evidence_ids=sorted(
            {identifier for fact in fact_catalog for identifier in fact.get('evidence_ids',[])}
            | {identifier for relation in relation_catalog for identifier in relation.get('evidence_ids',[])})

        projects.append(dict(
            name=source['name'],
            review_summary=dict(
                change_seed_count=len(change_seeds),
                review_unit_count=len(units),
                source_fact_count=len(source['facts']),
                selected_fact_count=len(fact_catalog),
                project_fact_count=len(project_facts),
                source_relation_count=len(source['relations']),
                selected_relation_count=len(relation_catalog),
                selected_evidence_count=len(evidence_ids)),
            review_units=units,
            project_fact_ids=sorted(fact['id'] for fact in project_facts),
            fact_catalog=fact_catalog,
            relation_catalog=relation_catalog,
            hard_errors=source['hard_errors'],
            stats=source['stats']
        ))

    cross=[row for row in analysis['cross_project_relations']
           if row.get('source_relation') in selected_relation_ids]
    return dict(
        analysis_id=analysis['analysis_id'],
        review_schema_version=1,
        projects=projects,
        cross_project_relations=cross,
        note='按变化方法聚合最终净变化和一层直接关系；完整事实仍保留在 analysis.json。'
    )


def write_json_template(path,value):
    target=Path(path)
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('x',encoding='utf-8',newline='\n') as out:
        json.dump(value,out,ensure_ascii=False,indent=2,allow_nan=False)
        out.write('\n')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--cases-template',help='同时生成合法的空 test-cases.json 骨架')
    parser.add_argument('--report-template',help='同时生成固定字段的 report-data.json 骨架')
    args=parser.parse_args(argv)
    try:
        analysis=json.loads(Path(args.analysis).read_text(encoding='utf-8-sig'))
        value=build_review(analysis)
        paths=[Path(args.output)]
        paths.extend(Path(value) for value in (args.cases_template,args.report_template) if value)
        resolved=[path.resolve() for path in paths]
        if len(set(resolved))!=len(resolved):
            raise ValueError('review/cases/report outputs must use different files')
        if any(path.exists() for path in paths):
            raise ValueError('review/cases/report output already exists')
        target=Path(args.output)
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('x',encoding='utf-8',newline='\n') as out:
            json.dump(value,out,ensure_ascii=False,indent=2,allow_nan=False)
            out.write('\n')
        if args.cases_template:
            from case_contract import empty_cases
            write_json_template(args.cases_template,empty_cases(analysis['analysis_id']))
        if args.report_template:
            from generate_report import empty_report_data
            write_json_template(args.report_template,empty_report_data())
        print('已生成按变化点聚合的快审材料。')
        return 0
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print('ERROR: '+str(exc),file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
