#!/usr/bin/env python3
"""生成分组复核视图和未完成用例骨架；不推断结论或豁免。"""
import argparse
import collections
import json
import sys
from pathlib import Path

from analysis_engine import VERSION, relation_receiver_hint, relation_review_signature
from test_points import PURPOSES, verify_catalog


def snapshot_neutral_fact(fact):
    data=dict(fact.get('data') or {})
    data.pop('side',None)
    data.pop('source_location',None)
    return data


def representative_relation_members(members, limit):
    ordered=sorted(members,key=lambda item:(item.get('symbol') or '',item.get('expression') or '',item['obligation_id']))
    selected=[];selected_ids=set();symbols=set();expressions=set()
    for item in ordered:
        symbol=item.get('symbol')
        if symbol and symbol not in symbols:
            selected.append(item);selected_ids.add(item['obligation_id']);symbols.add(symbol)
            expressions.add(item.get('expression'))
            if len(selected)>=limit:return selected
    for item in ordered:
        expression=item.get('expression')
        if item['obligation_id'] not in selected_ids and expression and expression not in expressions:
            selected.append(item);selected_ids.add(item['obligation_id']);expressions.add(expression)
            if len(selected)>=limit:return selected
    for item in ordered:
        if item['obligation_id'] not in selected_ids:
            selected.append(item);selected_ids.add(item['obligation_id'])
            if len(selected)>=limit:return selected
    return selected


def grouped(analysis, context=None):
    if analysis.get('schema_version') != 1 or analysis.get('analyzer_version') != VERSION:
        raise ValueError('unsupported analysis schema/analyzer version')
    projects = []
    for project in analysis['projects']:
        facts = {f['id']: f for f in project['facts']}
        groups = {}
        boundary_candidates = collections.defaultdict(list)
        relation_candidates = collections.defaultdict(list)
        coverage_gaps = collections.defaultdict(list)
        deferred = collections.defaultdict(list)
        deferred_relation_sites=set()
        required_relation_sites=set()
        required_evidence={e for item in project['obligations'] if item.get('required',True)
                           for e in item['evidence_ids']}
        for item in project['obligations']:
            fact = facts[item['fact_ids'][0]]
            data = fact['data']
            symbol = (data.get('symbol') or item['condition'].get('symbol') or
                      item['condition'].get('parent') or item['condition'].get('owner') or
                      data.get('owner') or item['condition'].get('path') or '<未定位方法>')
            member=dict(id=item['id'], dimension=item['dimension'], condition=item['condition'],
                assertion=item['assertion'], fact_ids=item['fact_ids'], evidence_ids=item['evidence_ids'],
                required=item.get('required',True), optional_reason=item.get('optional_reason'))
            if item.get('required',True):
                group=groups.setdefault(symbol, {'symbol': symbol, 'obligations': [], 'relations': [], 'incoming': [], 'boundaries': []})
                group['obligations'].append(member)
                if item['dimension']=='review_relation':
                    relation_candidates[relation_review_signature(fact)].append(member)
                    call=fact.get('data',{}).get('call',{})
                    required_relation_sites.add((symbol,'result',call.get('expression'),fact.get('data',{}).get('reason')))
            elif item['dimension']=='review_analysis_boundary':
                condition=item['condition']
                coverage_gaps[(condition.get('reason'),condition.get('side'))].append(dict(
                    obligation_id=item['id'],evidence_ids=item['evidence_ids'],parent=condition.get('parent'),
                    symbol=condition.get('symbol'),path=condition.get('path'),
                    source_location=condition.get('source_location')))
            else:
                deferred[(symbol,item['dimension'],item.get('optional_reason'))].append(member)
                if item['dimension']=='review_relation':
                    call=data.get('call',{})
                    deferred_relation_sites.add((symbol,'result',call.get('expression'),data.get('reason')))
            if item['dimension'] == 'review_analysis_boundary':
                condition = item['condition']
                if item.get('required',True):
                    key=tuple((k,v) for k,v in sorted(condition.items()) if k not in ('side','source_location'))
                    if condition.get('reason') and any(condition.get(k) for k in ('parent','symbol','path')):
                        boundary_candidates[key].append(dict(obligation_id=item['id'],side=condition.get('side'),
                            evidence_ids=item['evidence_ids']))
        for relation in project['relations']:
            symbol = relation['source']
            if relation['status']=='unresolved' and (symbol,relation['side'],relation['site'].get('expression'),relation['reason']) not in required_relation_sites:
                continue
            if (symbol,relation['side'],relation['site'].get('expression'),relation['reason']) in deferred_relation_sites:
                continue
            summary = dict(source=symbol, side=relation['side'], status=relation['status'],
                targets=relation['targets'], reason=relation['reason'], site=relation['site'],
                evidence_ids=relation['evidence_ids'])
            if symbol in groups:
                groups[symbol]['relations'].append(summary)
            for target in relation['targets']:
                if target in groups:
                    groups[target]['incoming'].append(summary)
        for boundary in project.get('boundaries', []):
            if not boundary.get('review_required',True):
                continue
            symbol = boundary.get('parent') or boundary.get('symbol') or '<未定位方法>'
            groups.setdefault(symbol, {'symbol': symbol, 'obligations': [], 'relations': [], 'incoming': [], 'boundaries': []})['boundaries'].append(boundary)
        batches=[];unpaired=[]
        for key,items in sorted(boundary_candidates.items(),key=lambda pair:repr(pair[0])):
            sides=[item['side'] for item in items]
            coordinates=dict(key)
            if len(items)==2 and set(sides)=={'baseline','result'}:
                batches.append(dict(condition=coordinates,members=[dict(obligation_id=item['obligation_id'],
                    evidence_ids=item['evidence_ids']) for item in sorted(items,key=lambda item:item['side'])]))
            else:
                unpaired.append(dict(condition=coordinates,members=sorted(items,key=lambda item:(item.get('side') or '',item['obligation_id']))))
        relation_batches=[]
        for signature,items in sorted(relation_candidates.items()):
            if len(items)<2:
                continue
            members=[]
            for item in sorted(items,key=lambda item:item['id']):
                data=facts[item['fact_ids'][0]]['data'];call=data.get('call',{})
                members.append(dict(obligation_id=item['id'],symbol=data.get('symbol'),
                    expression=call.get('expression'),evidence_ids=item['evidence_ids']))
            data=facts[items[0]['fact_ids'][0]]['data']
            symbols=sorted({facts[item['fact_ids'][0]]['data'].get('symbol') for item in items})
            relation_batches.append(dict(signature=signature,symbol=symbols[0] if len(symbols)==1 else None,
                symbols=symbols,reason=data.get('reason'),candidates=data.get('candidates'),members=members))
        relation_groups=[]
        for signature,items in relation_candidates.items():
            members=[]
            for item in items:
                data=facts[item['fact_ids'][0]]['data'];call=data.get('call',{})
                members.append(dict(obligation_id=item['id'],symbol=data.get('symbol'),
                                    expression=call.get('expression')))
            data=facts[items[0]['fact_ids'][0]]['data'];call=data.get('call',{})
            surface=' '.join((data.get('symbol') or '',data.get('receiver_type') or '',
                              call.get('name') or '',call.get('receiver') or '')).casefold()
            if any(token in surface for token in ('refund','refundable','transstate','paystate','status','state','uniquecontrol')):
                priority=0
            elif any(token in surface for token in ('dao','mapper','repository','jdbc','database','channel','qrcode','notify','notice','callback')):
                priority=1
            else:
                priority=2
            relation_groups.append(dict(signature=signature,priority=priority,reason=data.get('reason'),
            receiver_type=data.get('receiver_type'),method_name=call.get('name'),
            receiver_hint=relation_receiver_hint(call),argument_count=len(call.get('arguments',[])),
            candidates=data.get('candidates'),member_count=len(members),members=members))
        relation_groups.sort(key=lambda item:(item['priority'],-item['member_count'],item['signature']))
        fast10_method_limit=8
        fast10_relation_limit=4
        fast10_per_method_limit=2
        fast10_relation_member_limit=3
        method_groups=[]
        for group in groups.values():
            counts=collections.Counter(item['dimension'] for item in group['obligations'])
            reviewable_count=sum(item['dimension']!='review_relation' for item in group['obligations'])
            surface_parts=[group['symbol'],json.dumps([item['condition'] for item in group['obligations']],
                ensure_ascii=False,sort_keys=True)]
            for item in group['obligations']:
                for identifier in item['fact_ids']:
                    data=facts.get(identifier,{}).get('data',{})
                    surface_parts.extend(str(data.get(key) or '') for key in
                        ('symbol','field','target_field','owner','receiver_type','name','method'))
                    call=data.get('call')
                    if isinstance(call,dict):
                        surface_parts.extend((str(call.get('name') or ''),relation_receiver_hint(call)))
            surface=' '.join(surface_parts).casefold()
            priority=(0 if any(token in surface for token in ('refund','refundable','transstate','paystate','status','state'))
                      else 1 if any(token in surface for token in ('dao','mapper','repository','jdbc','channel','qcm','uniquecontrol',
                          'qrcode','pay','payment','notice','notify','response','trade','amount','mchid','fgtradeno','outtradeno','billno'))
                      else 2)
            enum_owner='.enums.' in group['symbol'].casefold() or 'enum#' in group['symbol'].casefold()
            semantic_priority=(2 if enum_owner else 0 if any(counts.get(dimension,0) for dimension in
                ('changed_behavior','argument_source','final_return','field_behavior')) else 1)
            method_groups.append(dict(symbol=group['symbol'],priority=priority,
                obligation_count=len(group['obligations']),reviewable_obligation_count=reviewable_count,
                semantic_priority=semantic_priority,dimension_counts=dict(sorted(counts.items())),
                obligations=group['obligations']))
        method_groups.sort(key=lambda item:(item['priority'],item['semantic_priority'],
                                            -item['reviewable_obligation_count'],item['symbol']))
        obligation_priority={'changed_behavior':0,'argument_source':1,'final_return':2,
            'field_behavior':3,'review_boundary':4,'configuration_enabled':5,
            'configuration_disabled':5,'branch':6,'switch_case':7,'membership':8,
            'review_relation':9}
        selected_method_groups=[];fast10_methods=[];selected_obligation_ids=set()
        for group in method_groups:
            candidates=[item for item in group['obligations'] if item['dimension']!='review_relation']
            if not candidates:
                continue
            candidates.sort(key=lambda item:(obligation_priority.get(item['dimension'],8),item['id']))
            queued=candidates[:fast10_per_method_limit]
            selected_obligation_ids.update(item['id'] for item in queued)
            selected_method_groups.append(group)
            fast10_methods.append(dict(symbol=group['symbol'],priority=group['priority'],
                semantic_priority=group['semantic_priority'],
                obligation_count=group['obligation_count'],
                reviewable_obligation_count=group['reviewable_obligation_count'],
                dimension_counts=group['dimension_counts'],
                queued_obligation_ids=[item['id'] for item in queued],
                queued_obligation_count=len(queued),
                deferred_obligation_count=group['obligation_count']-len(queued)))
            if len(selected_method_groups)>=fast10_method_limit:
                break
        selected_relations=relation_groups[:fast10_relation_limit]
        for group in selected_relations:
            selected_obligation_ids.update(member['obligation_id'] for member in representative_relation_members(
                group['members'],fast10_relation_member_limit))
        fast10_by_symbol={item['symbol']:item for item in fast10_methods}
        for group in selected_method_groups:
            fast10_by_symbol[group['symbol']]['deferred_obligation_count']=sum(
                item['id'] not in selected_obligation_ids for item in group['obligations'])
        fast10_relations=[dict(signature=group['signature'],priority=group['priority'],reason=group['reason'],
            receiver_type=group['receiver_type'],method_name=group['method_name'],
            receiver_hint=group['receiver_hint'],argument_count=group['argument_count'],
            candidates=group['candidates'],member_count=group['member_count'],
            queued_member_count=min(group['member_count'],fast10_relation_member_limit),
            deferred_member_count=max(0,group['member_count']-fast10_relation_member_limit),
            sample_members=representative_relation_members(group['members'],fast10_relation_member_limit),
            additional_member_count=max(0,group['member_count']-fast10_relation_member_limit))
            for group in selected_relations]
        required_obligation_count=sum(len(group['obligations']) for group in method_groups)
        fast10_review=dict(time_budget_minutes=10,max_method_groups=fast10_method_limit,
            max_relation_groups=fast10_relation_limit,
            max_obligations_per_method=fast10_per_method_limit,
            max_members_per_relation_group=fast10_relation_member_limit,
            queued_method_groups=fast10_methods,queued_method_group_count=len(fast10_methods),
            total_method_group_count=len(method_groups),deferred_method_group_count=max(0,len(method_groups)-len(fast10_methods)),
            queued_obligation_count=len(selected_obligation_ids),total_obligation_count=required_obligation_count,
            deferred_obligation_count=max(0,required_obligation_count-len(selected_obligation_ids)),
            queued_relation_groups=fast10_relations,queued_relation_group_count=len(fast10_relations),
            total_relation_group_count=len(relation_groups),
            deferred_relation_member_count=sum(item['deferred_member_count'] for item in fast10_relations),
            deferred_relation_group_count=max(0,len(relation_groups)-len(selected_relations)))
        gap_groups=[dict(reason=key[0],side=key[1],count=len(items),members=items)
                    for key,items in sorted(coverage_gaps.items(),key=lambda pair:(pair[0][0] or '',pair[0][1] or ''))]
        deferred_groups=[dict(symbol=key[0],dimension=key[1],reason=key[2],count=len(items),members=items)
                         for key,items in sorted(deferred.items(),key=lambda pair:repr(pair[0]))]
        evidence_groups=collections.defaultdict(lambda:{'baseline':[],'result':[]})
        evidence_by_id={}
        for record in context or []:
            if record.get('project')!=project['name'] or record.get('id') not in required_evidence:
                continue
            if record.get('side') not in ('baseline','result'):
                continue
            evidence_by_id[record['id']]=record
            evidence_groups[(record.get('path'),record.get('digest'))][record['side']].append(record['id'])
        shared_evidence=[dict(path=key[0],digest=key[1],baseline_evidence_ids=sorted(value['baseline']),
                              result_evidence_ids=sorted(value['result']))
                         for key,value in sorted(evidence_groups.items(),key=lambda pair:(pair[0][0] or '',pair[0][1] or ''))
                         if value['baseline'] and value['result']]
        required_facts={identifier for item in project['obligations'] if item.get('required',True)
                        for identifier in item['fact_ids']}
        fact_groups=collections.defaultdict(lambda:{'baseline':[],'result':[],'sources':{'baseline':set(),'result':set()}})
        for fact in project['facts']:
            if fact['id'] not in required_facts:
                continue
            sides=collections.defaultdict(set)
            evidence_ids_by_side=collections.defaultdict(list)
            for identifier in fact['evidence_ids']:
                record=evidence_by_id.get(identifier)
                if record:
                    sides[record['side']].add((record.get('path'),record.get('digest')))
                    evidence_ids_by_side[record['side']].append(identifier)
            fact_sides=[side for side in ('baseline','result') if evidence_ids_by_side[side]]
            if len(fact_sides)!=1:
                continue
            side=fact_sides[0]
            key=(fact['kind'],json.dumps(snapshot_neutral_fact(fact),sort_keys=True,ensure_ascii=False))
            fact_groups[key][side].append(dict(fact_id=fact['id'],evidence_ids=sorted(evidence_ids_by_side[side])))
            fact_groups[key]['sources'][side].update(sides[side])
        shared_facts=[]
        for key,facts_by_side in sorted(fact_groups.items(),key=lambda pair:pair[0][0]):
            if facts_by_side['baseline'] and facts_by_side['result'] and facts_by_side['sources']['baseline'] & facts_by_side['sources']['result']:
                shared_facts.append(dict(kind=key[0],data=json.loads(key[1]),baseline=facts_by_side['baseline'],
                    result=facts_by_side['result']))
        projects.append(dict(name=project['name'], groups=[groups[k] for k in sorted(groups)],
            boundary_review_batches=batches,unpaired_boundaries=unpaired,relation_review_batches=relation_batches,
            fast10_review=fast10_review,
            coverage_gap_groups=gap_groups,deferred_obligation_groups=deferred_groups,
            shared_evidence_groups=shared_evidence,shared_fact_groups=shared_facts,
            field_label_candidates=project['field_label_candidates']))
    return dict(analysis_id=analysis['analysis_id'], projects=projects,
                note='必要义务进入复核队列；预算边界和远距离预算/解析缺口不生成复核义务；仅保留可能影响数据库、渠道、QCM、通知、退款状态或最终响应的未解析调用；同名字段候选仍是按需检索的弱线索。')


def case_skeleton(analysis, catalog):
    verify_catalog(catalog, analysis)
    obligations = {o['id']: o for p in analysis['projects'] for o in p['obligations']}
    groups = {purpose: [] for purpose in PURPOSES}
    for index, point in enumerate(catalog['points'], 1):
        coverage = []
        for identifier in point['obligation_ids']:
            item = obligations[identifier]
            coverage.append(dict(obligation_id=identifier, condition=item['condition'], assertion=item['assertion'],
                fact_ids=item['fact_ids'], evidence_ids=item['evidence_ids'], observable_evidence_indices=[0]))
        groups[point['purpose']].append(dict(case_id='DRAFT-{:03d}'.format(index), title='', preconditions='',
            steps=[], expected=[], observable_evidence=[dict(**point['observation'], assertion='', constraint=point['expected'])],
            coverage_links=coverage, test_point_links=[dict(point_id=point['id'], conditions=point['conditions'],
                expected=point['expected'], data_group_ids=[g['id'] for g in point['data_groups']],
                observable_evidence_indices=[0])]))
    return dict(schema_version=2, test_point_catalog=catalog, purpose_cases=groups)


def normal_flow_name(symbol):
    """将快审方法归入少量业务流候选；未知域保持按类拆分。"""
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


def normal_flow_review(method_groups, relation_groups, facts, review):
    """把方法与重复调用义务合并为业务流候选，保留支持证据引用。"""
    flows={}
    def ensure(name):
        return flows.setdefault(name,dict(name=name,methods=set(),obligation_ids=set(),
            fact_ids=set(),evidence_ids=set(),relation_signatures=set(),method_obligation_count=0,
            relation_member_count=0))
    for method_group in method_groups:
        flow=ensure(normal_flow_name(method_group['symbol']))
        flow['methods'].add(method_group['symbol'])
        for item in method_group['obligations']:
            flow['obligation_ids'].add(item['id'])
            flow['fact_ids'].update(item['fact_ids'])
            flow['evidence_ids'].update(item['evidence_ids'])
            flow['method_obligation_count']+=1
    unassigned_relation_members=[]
    for relation_group in relation_groups:
        for member in relation_group['members']:
            name=normal_flow_name(member.get('symbol'))
            if name not in flows:
                unassigned_relation_members.append(member)
                continue
            flow=flows[name]
            flow['obligation_ids'].add(member['obligation_id'])
            flow['fact_ids'].update(member['fact_ids'])
            flow['evidence_ids'].update(member['evidence_ids'])
            flow['relation_signatures'].add(relation_group['signature'])
            flow['relation_member_count']+=1
    ordered=sorted(flows.values(),key=lambda item:(
        ('退款主流程','聚合码支付主流程','渠道流水号主流程','通知与回调主流程').index(item['name'])
        if item['name'] in ('退款主流程','聚合码支付主流程','渠道流水号主流程','通知与回调主流程') else 9,
        item['name']))
    result=[]
    for flow in ordered:
        result.append(dict(name=flow['name'],method_count=len(flow['methods']),
            methods=sorted(flow['methods']),supporting_obligation_count=len(flow['obligation_ids']),
            method_obligation_count=flow['method_obligation_count'],
            relation_member_count=flow['relation_member_count'],
            relation_group_count=len(flow['relation_signatures']),
            supporting_obligation_ids=sorted(flow['obligation_ids']),
            fact_ids=sorted(flow['fact_ids']),evidence_ids=sorted(flow['evidence_ids'])))
    selected_ids={identifier for flow in result for identifier in flow['supporting_obligation_ids']}
    selected_facts={identifier for flow in result for identifier in flow['fact_ids']}
    visible_facts={identifier for identifier in selected_facts
                    if facts.get(identifier,{}).get('kind')!='unresolved_relation'}
    unassigned_ids={member['obligation_id'] for member in unassigned_relation_members}
    unassigned_facts={identifier for member in unassigned_relation_members for identifier in member['fact_ids']}
    unassigned_evidence={identifier for member in unassigned_relation_members for identifier in member['evidence_ids']}
    return dict(group_count=len(result),total_obligation_count=review['total_obligation_count'],
        supporting_obligation_count=len(selected_ids),unassigned_relation_member_count=len(unassigned_ids),
        deferred_obligation_count=review['deferred_obligation_count'],
        method_group_count=review['queued_method_group_count'],relation_group_count=review['queued_relation_group_count'],
        groups=result,supporting_fact_ids=sorted(selected_facts),
        visible_fact_ids=sorted(visible_facts),visible_fact_count=len(visible_facts),
        suppressed_relation_fact_count=len(selected_facts-visible_facts),
        unassigned_relation_obligation_ids=sorted(unassigned_ids),
        unassigned_relation_fact_ids=sorted(unassigned_facts),
        unassigned_relation_evidence_ids=sorted(unassigned_evidence),
        note='按类/方法名称归并的主流程候选，须结合源码证据确认语义；未解析调用仅作为支持证据，未归入入选主流程的关系单独保留待确认。')


def fast10_view(analysis, context=None):
    """只输出本次净变化、其直接关系和对应源码事实；不再先构造全量复核队列再截断。"""
    if analysis.get('schema_version') != 1 or analysis.get('analyzer_version') != VERSION:
        raise ValueError('unsupported analysis schema/analyzer version')
    compact_projects=[]
    primary_kinds={'method_change','field_change','class_binding_change','configuration','configuration_file_change',
                   'configuration_parse_boundary','argument_source_change','history_boundary'}
    supporting_kinds={'branch','switch_branch','membership_filter','return_sources','return_override','field_source',
                      'flow_boundary','mapper_statement','unresolved_relation','annotation_binding_unconfigured',
                      'entrypoint','constant_or_collection'}
    for source in analysis['projects']:
        facts=source.get('facts',[])
        primary=[fact for fact in facts if fact.get('kind') in primary_kinds]
        changed_symbols={fact.get('data',{}).get('symbol') for fact in primary
                         if fact.get('data',{}).get('symbol')}
        for fact in primary:
            if fact.get('kind')=='field_change':
                changed_symbols.update(fact.get('data',{}).get('consumers') or [])
        primary_evidence={identifier for fact in primary for identifier in fact.get('evidence_ids',[])}
        direct_relations=[relation for relation in source.get('relations',[])
                          if relation.get('source') in changed_symbols
                          or any(target in changed_symbols for target in relation.get('targets',[]))]
        relation_evidence={identifier for relation in direct_relations for identifier in relation.get('evidence_ids',[])}
        visible=[]
        for fact in facts:
            if fact in primary:
                visible.append(fact);continue
            if fact.get('kind') not in supporting_kinds:
                continue
            data=fact.get('data',{})
            symbol=data.get('symbol')
            if symbol in changed_symbols or set(fact.get('evidence_ids',[])) & (primary_evidence|relation_evidence):
                visible.append(fact)
        visible_ids={fact['id'] for fact in visible}
        evidence_ids=sorted({identifier for fact in visible for identifier in fact.get('evidence_ids',[])}
                            | relation_evidence)
        flow_groups={}
        for symbol in sorted(changed_symbols):
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
        normal=dict(group_count=len(groups),groups=groups,
                    supporting_fact_ids=sorted(visible_ids),visible_fact_ids=sorted(visible_ids),
                    visible_fact_count=len(visible_ids),evidence_ids=evidence_ids,
                    note='范围直接来自本次净变化及一层直接关系；业务名称只用于展示归组，不参与是否入选。')
        summary=dict(time_budget_minutes=10,changed_method_count=len(changed_symbols),
                     primary_change_fact_count=len(primary),selected_fact_count=len(visible),
                     direct_relation_count=len(direct_relations),selected_evidence_count=len(evidence_ids))
        compact_projects.append(dict(name=source['name'],normal_flow_review=normal,
            fast10_review=summary,facts=visible,direct_relations=direct_relations,
            hard_errors=source.get('hard_errors',[]),scope_errors=source.get('scope_errors',[]),
            critical_evidence_omissions=source.get('critical_evidence_omissions',[]),
            stats=source.get('stats',{})))
    return dict(analysis_id=analysis['analysis_id'],fast10_mode=True,projects=compact_projects,
        note='快审只消费本次净变化、直接一层关系和对应事实；不创建额外分支/关系测试义务。')

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis', required=True)
    parser.add_argument('--catalog', help='已编译测试点目录；提供时生成未完成用例骨架')
    parser.add_argument('--fast10', action='store_true', help='输出本次净变化、一层直接关系及相关事实')
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        analysis = json.loads(Path(args.analysis).read_text(encoding='utf-8-sig'))
        if args.catalog and args.fast10:
            raise ValueError('--fast10 cannot be combined with --catalog')
        if args.catalog:
            value=case_skeleton(analysis,json.loads(Path(args.catalog).read_text(encoding='utf-8-sig')))
        elif args.fast10:
            value=fast10_view(analysis)
        else:
            context_path=(Path(args.analysis).parent/analysis.get('context_file','context.jsonl')).resolve()
            if context_path.parent!=Path(args.analysis).parent.resolve():
                raise ValueError('context file must stay beside analysis.json')
            context=[json.loads(line) for line in context_path.read_text(encoding='utf-8').splitlines() if line.strip()]
            value=grouped(analysis,context)
        with Path(args.output).open('x', encoding='utf-8', newline='\n') as out:
            json.dump(value, out, ensure_ascii=False, indent=2, allow_nan=False)
            out.write('\n')
        label='未完成用例骨架' if args.catalog else '10 分钟快审视图' if args.fast10 else '分组复核视图'
        if args.fast10:
            print('已生成{}；请按净变化和一层直接关系核对业务影响与配置前提。'.format(label))
        else:
            print('已生成{}；仍需人工复核和填写。'.format(label))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print('ERROR: '+str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
