#!/usr/bin/env python3
"""从 analysis.json 生成按变化点聚合的一层复核材料。"""
import argparse
import json
import sys
from pathlib import Path

from analysis_engine import VERSION
from source_index import stable


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
                add(value.get('symbol') if isinstance(value,dict) else value)
    for side in ('before','after'):
        value=data.get(side)
        if not isinstance(value,dict):
            continue
        add(value.get('symbol'))
        for consumer in value.get('consumers') or []:
            add(consumer.get('symbol') if isinstance(consumer,dict) else consumer)
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
    return dict(id=fact['id'],kind=fact.get('kind'),classification=fact.get('classification'),
                data=_preview_value(fact.get('data') or {}),
                evidence_ids=sorted(fact.get('evidence_ids') or []))


def _data_anchors(data):
    """从单条事实提取可复用的精确结构锚点。"""
    result=set()
    if not isinstance(data,dict):
        return result
    key=data.get('key')
    if isinstance(key,str) and key:
        result.add(('config',key))
    for side in ('before','after'):
        value=data.get(side)
        if isinstance(value,dict):
            nested=value.get('key')
            if isinstance(nested,str) and nested:
                result.add(('config',nested))
    owner,field=data.get('owner'),data.get('field')
    if isinstance(owner,str) and owner and isinstance(field,str) and field:
        result.add(('field',owner+'#'+field))
    receiver_type=data.get('receiver_type')
    if isinstance(receiver_type,str) and receiver_type and isinstance(field,str) and field:
        result.add(('field',receiver_type+'#'+field))
    namespace=data.get('namespace')
    name=data.get('name') or data.get('statement')
    if isinstance(namespace,str) and namespace and isinstance(name,str) and name:
        result.add(('mapper',namespace+'#'+name))
    return result


def _summary_fields(data):
    preferred=('change','symbol','owner','field','key','path','name','statement','condition',
               'expression','then','otherwise','source','target','receiver_type','status','reason',
               'origins','before','after','default','usage','member','collection')
    result={}
    for key in preferred:
        if key in data:
            result[key]=_preview_value(data[key],1)
    return result


def summarize_facts(facts):
    """把事实压成确定性变化摘要，不做业务语义归纳。"""
    rows=[]
    seen=set()
    for fact in facts:
        data=fact.get('data') or {}
        row=dict(kind=fact.get('kind'),fact_id=fact.get('id'),details=_summary_fields(data))
        marker=json.dumps(row,ensure_ascii=False,sort_keys=True)
        if marker in seen:
            continue
        seen.add(marker)
        rows.append(row)
    return rows


def unit_anchors(unit,facts_by_id,relations_by_id):
    """返回 facet 锚点；每个锚点独立成组，不做传递合并。"""
    anchors=set()
    symbol=unit.get('symbol')
    if symbol:
        anchors.add(('owner',symbol.split('#',1)[0]))
    for fact_id in unit.get('direct_fact_ids') or []:
        fact=facts_by_id.get(fact_id)
        if fact:
            anchors.update(_data_anchors(fact.get('data') or {}))
    for relation_id in unit.get('relation_ids') or []:
        relation=relations_by_id.get(relation_id) or {}
        targets=relation.get('targets') or []
        for target in targets:
            anchors.add(('direct_call',target))
        if not targets:
            receiver=relation.get('receiver_type')
            name=(relation.get('site') or {}).get('name')
            if isinstance(receiver,str) and receiver and isinstance(name,str) and name:
                anchors.add(('direct_call',receiver+'#'+name))
    return anchors


def build_facets(project,units,facts_by_id,relations_by_id):
    """按单一精确锚点生成可重叠 facet，避免连通分量把弱相关 unit 串成大组。"""
    inverted={}
    for unit in units:
        anchors=unit_anchors(unit,facts_by_id,relations_by_id)
        for facet_type,anchor in anchors:
            inverted.setdefault((facet_type,anchor),set()).add(unit['id'])
    facets=[]
    membership={unit['id']:[] for unit in units}
    for (facet_type,anchor),unit_ids in sorted(inverted.items()):
        if len(unit_ids)<2:
            continue
        ordered=sorted(unit_ids)
        facet_id='RF-'+stable(project,facet_type,anchor,ordered)[:12]
        facets.append(dict(
            id=facet_id,
            type=facet_type,
            anchor=anchor,
            unit_ids=ordered,
            unit_count=len(ordered)))
        for unit_id in ordered:
            membership[unit_id].append(facet_id)
    for unit in units:
        unit['facet_ids']=sorted(membership[unit['id']])
    return facets


LOW_VALUE_CALL_OWNER_MARKERS=('.util.','.utils.','.common.','.base.','.support.','.framework.')
LOW_VALUE_CALL_OWNER_SUFFIXES=('Util','Utils','Helper','Base','Abstract','Support','Builder','Converter')
LOW_VALUE_CALL_METHODS={
    'get','set','of','valueOf','toString','hashCode','equals','size','isEmpty','stream',
    'iterator','add','put','remove','contains','containsKey','copy','clone','format','parse',
    'trim','substring','split','join','requireNonNull'
}


def _is_low_value_shared_call(anchor):
    """识别不适合作为首轮导航入口的通用共享调用；完整 facet 仍保留。"""
    if not isinstance(anchor,str) or '#' not in anchor:
        return False
    owner,method=anchor.split('#',1)
    lowered=owner.casefold()
    simple_owner=owner.rsplit('.',1)[-1]
    method_name=method.split('(',1)[0]
    if any(marker in lowered for marker in LOW_VALUE_CALL_OWNER_MARKERS):
        return True
    if simple_owner.endswith(LOW_VALUE_CALL_OWNER_SUFFIXES):
        return True
    return method_name in LOW_VALUE_CALL_METHODS


def _navigation_rank(facet,changed_symbols):
    """返回 (priority, reason)；None 表示只保留在完整 review_facets 中。"""
    facet_type=facet.get('type')
    anchor=facet.get('anchor')
    if facet_type=='config':
        return 10,'配置键变化'
    if facet_type=='mapper':
        return 12,'Mapper/SQL 变化'
    if facet_type=='field':
        return 14,'字段变化'
    if facet_type=='direct_call':
        if anchor in changed_symbols:
            return 16,'直接关联其它变化方法'
        if _is_low_value_shared_call(anchor):
            return None
        return 30,'变化方法直接关联的业务调用'
    if facet_type=='owner':
        return 20,'同一变化类'
    return None


def build_navigation_facets(facets,units):
    """从完整 facet 中选择首轮阅读入口，不删除任何完整关系。"""
    changed_symbols={unit.get('symbol') for unit in units if unit.get('symbol')}
    selected=[]
    membership={unit['id']:[] for unit in units}
    for facet in facets:
        ranked=_navigation_rank(facet,changed_symbols)
        if ranked is None:
            continue
        priority,reason=ranked
        row=dict(facet,navigation_priority=priority,navigation_reason=reason)
        selected.append(row)
        for unit_id in facet['unit_ids']:
            membership[unit_id].append(facet['id'])
    selected.sort(key=lambda row:(row['navigation_priority'],row['type'],row['anchor'],row['id']))
    for unit in units:
        unit['navigation_facet_ids']=sorted(membership[unit['id']])
    return selected



def build_review(analysis):
    if analysis.get('schema_version')!=2 or analysis.get('analyzer_version')!=VERSION:
        raise ValueError('unsupported analysis schema/analyzer version')
    projects=[]
    selected_relation_ids=set()
    primary_kinds={
        'method_change','field_change','class_binding_change','configuration',
        'configuration_file_change','configuration_parse_boundary',
        'argument_source_change','mapper_statement_change'
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
        relations_by_id={relation['id']:relation for relation in relations if relation.get('id')}

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
            unit_facts=[fact for fact in facts if symbols_by_fact[fact['id']] & local_symbols]
            direct_facts=[fact for fact in unit_facts if symbol in symbols_by_fact[fact['id']]]
            context_facts=[fact for fact in unit_facts if fact not in direct_facts]
            unit_fact_ids=sorted(fact['id'] for fact in unit_facts)
            selected_fact_ids.update(unit_fact_ids)
            units.append(dict(
                id='RU-'+stable(source['name'],symbol)[:12],
                scope='method',
                symbol=symbol,
                related_symbols=sorted(local_symbols-{symbol}),
                change_summary=summarize_facts(direct_facts),
                direct_fact_ids=sorted(fact['id'] for fact in direct_facts),
                context_fact_ids=sorted(fact['id'] for fact in context_facts),
                direct_fact_groups=fact_group_summary(direct_facts),
                context_fact_groups=fact_group_summary(context_facts),
                fact_groups=fact_group_summary(unit_facts),
                fact_ids=unit_fact_ids,
                relation_ids=sorted(relation['id'] for relation in touching if relation.get('id'))))

        project_facts=[fact for fact in facts
                       if fact.get('kind') in primary_kinds
                       and (not symbols_by_fact[fact['id']] or
                            (fact.get('kind')=='mapper_statement_change' and
                             fact['id'] not in selected_fact_ids))
                       and not (fact.get('kind')=='configuration' and
                                (fact.get('data') or {}).get('change')=='retained_or_consumers_changed')]
        if project_facts:
            project_unit_fact_ids=sorted(fact['id'] for fact in project_facts)
            units.append(dict(
                id='RU-'+stable(source['name'],'project',project_unit_fact_ids)[:12],
                scope='project',
                symbol=None,
                related_symbols=[],
                change_summary=summarize_facts(project_facts),
                direct_fact_ids=project_unit_fact_ids,
                context_fact_ids=[],
                direct_fact_groups=fact_group_summary(project_facts),
                context_fact_groups={},
                fact_groups=fact_group_summary(project_facts),
                fact_ids=project_unit_fact_ids,
                relation_ids=[]))
            selected_fact_ids.update(project_unit_fact_ids)

        fact_catalog=[fact_preview(facts_by_id[identifier]) for identifier in sorted(selected_fact_ids)]
        selected_relation_ids.update(selected_relations)
        units=sorted(units,key=lambda row:row['id'])
        facets=build_facets(source['name'],units,facts_by_id,relations_by_id)
        navigation_facets=build_navigation_facets(facets,units)
        faceted={unit_id for facet in facets for unit_id in facet['unit_ids']}
        navigation_faceted={unit_id for facet in navigation_facets for unit_id in facet['unit_ids']}

        projects.append(dict(
            name=source['name'],
            review_summary=dict(
                change_seed_count=len(change_seeds),
                review_unit_count=len(units),
                review_facet_count=len(facets),
                navigation_facet_count=len(navigation_facets),
                faceted_unit_count=len(faceted),
                unfaceted_unit_count=len(units)-len(faceted),
                navigation_faceted_unit_count=len(navigation_faceted),
                navigation_unfaceted_unit_count=len(units)-len(navigation_faceted),
                max_facet_size=max([facet['unit_count'] for facet in facets] or [0]),
                max_navigation_facet_size=max([facet['unit_count'] for facet in navigation_facets] or [0]),
                source_fact_count=len(source['facts']),
                selected_fact_count=len(fact_catalog),
                source_relation_count=len(source['relations']),
                selected_relation_count=len(selected_relations)),
            review_facets=facets,
            navigation_facets=navigation_facets,
            review_units=units,
            fact_catalog=fact_catalog,
            hard_errors=source['hard_errors'],
            stats=source['stats']
        ))

    cross=[row for row in analysis['cross_project_relations']
           if row.get('source_relation') in selected_relation_ids]
    return dict(
        analysis_id=analysis['analysis_id'],
        review_schema_version=4,
        projects=projects,
        cross_project_relations=cross,
        note='review_facets 保留完整精确结构关系；navigation_facets 只调整阅读顺序，不定义或过滤复核范围。change_summary 只压缩直接事实，完整事实始终保留。业务结论由 Agent 判断。'
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
    args=parser.parse_args(argv)
    try:
        analysis=json.loads(Path(args.analysis).read_text(encoding='utf-8-sig'))
        value=build_review(analysis)
        paths=[Path(args.output)]
        paths.extend(Path(value) for value in (args.cases_template,) if value)
        resolved=[path.resolve() for path in paths]
        if len(set(resolved))!=len(resolved):
            raise ValueError('review/cases outputs must use different files')
        if any(path.exists() for path in paths):
            raise ValueError('review/cases output already exists')
        target=Path(args.output)
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('x',encoding='utf-8',newline='\n') as out:
            json.dump(value,out,ensure_ascii=False,indent=2,allow_nan=False)
            out.write('\n')
        if args.cases_template:
            from case_contract import empty_cases
            write_json_template(args.cases_template,empty_cases(analysis['analysis_id']))
        print('已生成聚合复核材料及测试用例输入骨架。')
        return 0
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print('ERROR: '+str(exc),file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
