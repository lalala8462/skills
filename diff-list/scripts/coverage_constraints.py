"""将可确定的源码覆盖条件转换为测试谓词；不推测业务角色或配置值。"""
import json
import re


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def expression(value):
    value=re.sub(r'"(?:\\.|[^"\\])*"|\s+',lambda m:m[0] if m[0].startswith('"') else '',value)
    while value.startswith('(') and value.endswith(')'):
        depth=0
        for i,char in enumerate(value):
            depth+=(char=='(')-(char==')')
            if depth==0:break
        if i!=len(value)-1:break
        value=value[1:-1]
    return value


def role(value):
    return expression(value).replace('.size()','.count')


def scalar(value):
    try:
        return json.loads(value)
    except (ValueError,TypeError):
        return value


def branch_condition(value,truth):
    value=expression(value)
    match=re.fullmatch(r'([\w.$()]+)(==|!=|>=|<=|>|<)(null|true|false|-?\d+|"(?:\\.|[^"\\])*")',value)
    if match:
        subject,op,raw=match.groups()
        operators={'==':'eq','!=':'ne','>':'gt','>=':'ge','<':'lt','<=':'le'}
        operator=operators[op]
        if not truth:operator={'eq':'ne','ne':'eq','gt':'le','ge':'lt','lt':'ge','le':'gt'}[operator]
        if raw=='null':
            return (dict(subject=role(subject),operator='is_null') if operator=='eq' else
                    dict(subject=role(subject),operator='ne',value=None)) if operator in ('eq','ne') else None
        return dict(subject=role(subject),operator=operator,value=scalar(raw))
    return dict(subject='java:'+value,operator='eq',value=truth)


def source_contract(item,facts):
    """返回可验证的触发条件备选和可确定的结果；空列表表示该维度仅作源码复核。"""
    dimension,condition=item.get('dimension'),item.get('condition',{})
    alternatives,expected=[],None
    related=[facts[f] for f in item.get('fact_ids',[]) if f in facts]
    data=next((f.get('data',{}) for f in related),{})
    if dimension=='cardinality':
        receivers=[condition['collection']] if condition.get('collection') else re.findall(r'([\w.$]+)\.size\(\)',condition['expression'])
        if len(set(receivers))!=1:
            raise ValueError('unresolved source cardinality; source review required: '+item['id'])
        count=condition['count']
        alternatives=[[dict(subject=receivers[0]+'.count',operator='gt' if count=='many' else 'eq',
                            value=1 if count in ('one','many') else 0)]]
    elif dimension=='branch':
        predicate=branch_condition(condition['expression'],condition['value'])
        if predicate is not None:alternatives=[[predicate]]
        errors=item.get('assertion',{}).get('errors',[])
        if len(errors)==1:expected=dict(kind='equals',value=errors[0])
    elif dimension=='switch_case':
        labels=[scalar(label) for label in condition['labels']]
        if condition['label']=='default':
            alternatives=[[dict(subject=role(condition['expression']),operator='not_in',value=labels)]] if labels else []
        else:
            alternatives=[[dict(subject=role(condition['expression']),operator='eq',value=scalar(condition['label']))]]
    elif dimension=='argument_source':
        before,after=role(data['before']),role(data['after'])
        variation=condition['variation']
        if variation=='source_values_differ':alternatives=[[dict(subject=before,operator='ne',other=after)]]
        if variation=='request_value_absent':alternatives=[[dict(subject=before,operator='missing')]]
        if variation=='record_value_empty':
            alternatives=[[dict(subject=after,operator=op)] for op in ('is_null','is_empty')]
        expected=dict(kind='source_is',value=after)
    elif dimension=='membership':
        collection=condition['collection']
        constants=[f['data'] for f in facts.values() if f.get('kind')=='constant_or_collection'
                   and f['data'].get('side')=='result' and
                   ((f['data']['owner']+'.'+f['data']['name'])==collection or
                    (f['data']['owner']+'.'+f['data']['name']).endswith('.'+collection))]
        values=[]
        if len(constants)==1:
            members=constants[0].get('literal_members',[])
            values=(members if isinstance(members,list) else [])+[r['value'] for r in constants[0].get('references',[]) if r.get('value') is not None]
        if values:
            members={canonical(value):value for value in values}
            alternatives=[[dict(subject=role(data['member']),operator='in' if condition['present'] else 'not_in',
                                value=[members[key] for key in sorted(members)])]]
        else:
            alternatives=[[dict(subject='java:'+expression(collection+'.contains('+data.get('member','?')+')'),
                                operator='eq',value=condition['present'])]]
    return alternatives,expected


def numeric_bounds(predicates):
    low,high,low_closed,high_closed=float('-inf'),float('inf'),False,False
    for p in predicates:
        value=p.get('value')
        if type(value) not in (int,float):continue
        op=p['operator']
        if op in ('eq','gt','ge') and (value>low or (value==low and op=='gt')):
            low,low_closed=value,op!='gt'
        if op in ('eq','lt','le') and (value<high or (value==high and op=='lt')):
            high,high_closed=value,op!='lt'
    if low>high or (low==high and not (low_closed and high_closed)):
        raise ValueError('contradictory numeric test conditions')
    return low,high,low_closed,high_closed


def implies(actual,required):
    same=[p for p in actual if p['subject']==required['subject']]
    if 'other' in required:
        return any(p['operator']==required['operator'] and 'other' in p and
                   {p['subject'],p['other']}=={required['subject'],required['other']} for p in actual)
    if any(canonical(p)==canonical(required) for p in same):return True
    op,value=required['operator'],required.get('value')
    if op in ('eq','gt','ge','lt','le') and type(value) in (int,float):
        low,high,lc,hc=numeric_bounds(same)
        return {'eq':low==high==value and lc and hc,
                'gt':low>value or (low==value and not lc),'ge':low>=value,
                'lt':high<value or (high==value and not hc),'le':high<=value}[op]
    if op in ('in','not_in'):
        allowed={canonical(v) for v in value}
        for p in same:
            if p['operator']=='eq' and 'value' in p:
                contained=canonical(p['value']) in allowed
                if contained == (op=='in'):return True
            if p['operator']=='in':
                actual_values={canonical(v) for v in p['value']}
                if (actual_values<=allowed if op=='in' else actual_values.isdisjoint(allowed)):return True
            if op=='not_in' and p['operator']=='not_in' and {canonical(v) for v in p['value']}>=allowed:
                return True
    if op=='ne' and value is None and any(p['operator']=='nonempty' for p in same):return True
    return False


def check_source_coverage(candidate,items,facts):
    bindings=candidate.get('source_bindings',{})
    if not isinstance(bindings,dict) or any(not isinstance(k,str) or not isinstance(v,str) or not k.strip() or not v.strip()
                                          for k,v in bindings.items()) or len(set(bindings.values()))!=len(bindings):
        raise ValueError('source_bindings requires distinct source-role to test-role strings')
    contracts=[(item,source_contract(item,facts)) for item in items]
    roles={p[k] for _,(alternatives,_) in contracts for option in alternatives for p in option for k in ('subject','other') if k in p}
    roles.update(expected['value'] for _,(_,expected) in contracts if expected and expected['kind']=='source_is')
    if set(bindings)-roles:
        raise ValueError('source_bindings contains an unknown source role')
    actual=candidate['conditions']
    for subject in {p['subject'] for p in actual}:
        predicates=[p for p in actual if p['subject']==subject]
        numeric_bounds(predicates)
        equals=[p['value'] for p in predicates if p['operator']=='eq' and 'value' in p]
        for value in equals:
            if any(p['operator']=='eq' and 'value' in p and canonical(p['value'])!=canonical(value) for p in predicates):
                raise ValueError('contradictory test conditions')
            if any(p['operator']=='ne' and 'value' in p and canonical(p['value'])==canonical(value) for p in predicates):
                raise ValueError('contradictory test conditions')
    for item,(alternatives,expected) in contracts:
        mapped=[]
        for option in alternatives:
            mapped.append([{k:bindings.get(v,v) if k in ('subject','other') else v for k,v in p.items()} for p in option])
        if mapped and not any(all(implies(actual,p) for p in option) for option in mapped):
            raise ValueError('test conditions do not cover source obligation: '+item['id'])
        if expected:
            expected=dict(expected)
            if expected['kind']=='source_is':expected['value']=bindings.get(expected['value'],expected['value'])
            if canonical(candidate['expected'])!=canonical(expected):
                raise ValueError('test assertion contradicts source obligation: '+item['id'])
