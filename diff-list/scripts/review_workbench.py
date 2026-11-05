#!/usr/bin/env python3
"""从 analysis.json 生成当前净变化的一层快审材料。"""
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


def fast10_view(analysis):
    if analysis.get('schema_version')!=2 or analysis.get('analyzer_version')!=VERSION:
        raise ValueError('unsupported analysis schema/analyzer version')
    projects=[]
    selected_relation_ids=set()
    primary_kinds={
        'method_change','field_change','class_binding_change','configuration',
        'configuration_file_change','configuration_parse_boundary',
        'argument_source_change','history_boundary'
    }
    supporting_kinds={
        'branch','switch_branch','membership_filter','return_sources','return_override',
        'field_source','flow_boundary','mapper_statement','unresolved_relation',
        'annotation_binding_unconfigured','entrypoint','constant_or_collection',
        'object_copy','constructor_mapping'
    }
    for source in analysis.get('projects',[]):
        facts=source.get('facts',[])
        primary=[fact for fact in facts if fact.get('kind') in primary_kinds]
        change_seeds=set(source.get('change_seeds') or [])
        direct_relations=list(source.get('relations') or [])
        selected_relation_ids.update(relation.get('id') for relation in direct_relations if relation.get('id'))
        direct_symbols=set(change_seeds)
        for relation in direct_relations:
            direct_symbols.add(relation.get('source'))
            direct_symbols.update(relation.get('targets') or [])
        direct_symbols.discard(None)

        primary_evidence={identifier for fact in primary for identifier in fact.get('evidence_ids',[])}
        relation_evidence={identifier for relation in direct_relations for identifier in relation.get('evidence_ids',[])}
        visible=[]
        for fact in facts:
            if fact in primary:
                visible.append(fact)
                continue
            if fact.get('kind') not in supporting_kinds:
                continue
            data=fact.get('data',{})
            linked_symbols=set(data.get('linked_symbols') or [])
            if (data.get('symbol') in direct_symbols or linked_symbols & direct_symbols
                    or set(fact.get('evidence_ids',[])) & (primary_evidence|relation_evidence)):
                visible.append(fact)

        visible_ids={fact['id'] for fact in visible}
        evidence_ids=sorted({identifier for fact in visible for identifier in fact.get('evidence_ids',[])}
                            | relation_evidence)
        flow_groups={}
        for symbol in sorted(change_seeds):
            related=[fact for fact in visible if fact.get('data',{}).get('symbol')==symbol
                     or symbol in (fact.get('data',{}).get('consumers') or [])]
            if not related:
                continue
            name=normal_flow_name(symbol)
            group=flow_groups.setdefault(name,dict(name=name,methods=set(),fact_ids=set(),evidence_ids=set()))
            group['methods'].add(symbol)
            group['fact_ids'].update(fact['id'] for fact in related)
            group['evidence_ids'].update(identifier for fact in related for identifier in fact.get('evidence_ids',[]))
        groups=[dict(name=value['name'],method_count=len(value['methods']),methods=sorted(value['methods']),
                     fact_ids=sorted(value['fact_ids']),evidence_ids=sorted(value['evidence_ids']))
                for value in sorted(flow_groups.values(),key=lambda item:item['name'])]

        projects.append(dict(
            name=source['name'],
            normal_flow_review=dict(
                group_count=len(groups),groups=groups,
                supporting_fact_ids=sorted(visible_ids),
                visible_fact_ids=sorted(visible_ids),
                visible_fact_count=len(visible_ids),
                evidence_ids=evidence_ids,
                note='范围来自本次净变化及一层直接关系；业务名称仅用于展示归组。'),
            fast10_review=dict(
                change_seed_count=len(change_seeds),
                primary_change_fact_count=len(primary),
                selected_fact_count=len(visible),
                direct_relation_count=len(direct_relations),
                selected_evidence_count=len(evidence_ids)),
            facts=visible,
            direct_relations=direct_relations,
            hard_errors=source.get('hard_errors',[]),
            stats=source.get('stats',{})
        ))

    cross=[row for row in analysis.get('cross_project_relations',[])
           if row.get('source_relation') in selected_relation_ids]
    return dict(
        analysis_id=analysis['analysis_id'],
        fast10_mode=True,
        projects=projects,
        cross_project_relations=cross,
        note='快审只消费最终净变化、一层直接关系及对应源码事实。'
    )


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    try:
        analysis=json.loads(Path(args.analysis).read_text(encoding='utf-8-sig'))
        value=fast10_view(analysis)
        with Path(args.output).open('x',encoding='utf-8',newline='\n') as out:
            json.dump(value,out,ensure_ascii=False,indent=2,allow_nan=False)
            out.write('\n')
        print('已生成快审材料；请按净变化和一层直接关系核对业务影响与配置前提。')
        return 0
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print('ERROR: '+str(exc),file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
