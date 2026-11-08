#!/usr/bin/env python3
"""Agent 复核回执：仅校验结构化身份与来源，不判断业务结论是否成立。"""
from generate_report import AGENT_HEADINGS
from validate_report import existing_impact_values, heading_positions, sections

SCHEMA_VERSION=1
IMPACT_KEY='existing_impact'
SECTION_KEYS=AGENT_HEADINGS+(IMPACT_KEY,)


def _unresolved_by_unit(analysis,review):
    relations={row['id']:row for project in analysis.get('projects',[])
               for row in project.get('relations',[])}
    result={}
    for project in review.get('projects',[]):
        for unit in project.get('review_units',[]):
            ids=sorted(rid for rid in unit.get('relation_ids',[])
                       if rid in relations and relations[rid].get('status')=='unresolved')
            if ids:
                result[unit['id']]=ids
    return result


def empty_receipts(analysis,review):
    """只生成状态骨架；Agent 必须亲自填写处理状态和真实来源。"""
    blank=lambda: dict(status='pending',unit_ids=[],fact_ids=[],evidence_ids=[])
    return dict(schema_version=SCHEMA_VERSION,analysis_id=analysis['analysis_id'],
                sections={key:blank() for key in SECTION_KEYS},
                audit_unresolved_by_unit={
                    unit_id:dict(status='pending',relation_ids=ids,fact_ids=[],evidence_ids=[])
                    for unit_id,ids in _unresolved_by_unit(analysis,review).items()})


def receipt_counts(data,analysis=None,review=None):
    """概览仅显示状态数量，缺失与未知状态计入待处理，不证明语义正确。"""
    if not isinstance(data,dict):
        raise ValueError('review-receipts 必须是 JSON object')
    sections=data.get('sections')
    audit=data.get('audit_unresolved_by_unit')
    if not isinstance(sections,dict) or not isinstance(audit,dict):
        raise ValueError('review-receipts sections/audit_unresolved_by_unit 必须是对象')
    expected=(_unresolved_by_unit(analysis,review)
              if analysis is not None and review is not None else audit)
    def state(row):
        return row.get('status') if isinstance(row,dict) else None
    return dict(section_total=len(SECTION_KEYS),
                section_pending=sum(state(sections.get(key)) not in ('assessed','not_applicable')
                                    for key in SECTION_KEYS),
                audit_unresolved_unit_total=len(expected),
                audit_unresolved_unit_pending=sum(state(audit.get(uid)) not in ('assessed','deferred')
                                                  for uid in expected),
                audit_unresolved_unit_deferred=sum(state(audit.get(uid))=='deferred'
                                                   for uid in expected))


def validate_receipts(data,analysis,review,context,report):
    """仅检验结构、事实与证据的真实身份及所属 unit；不检验文字与业务含义。"""
    if not isinstance(data,dict) or set(data)!={
            'schema_version','analysis_id','sections','audit_unresolved_by_unit'}:
        raise ValueError('review-receipts 根字段不正确')
    if data['schema_version']!=SCHEMA_VERSION or data['analysis_id']!=analysis['analysis_id']:
        raise ValueError('review-receipts 与 analysis_id/schema_version 不一致')
    units={}
    facts={}
    relations={}
    for project in analysis.get('projects',[]):
        for fact in project.get('facts',[]):
            facts[fact['id']]=(project['name'],fact)
        for relation in project.get('relations',[]):
            relations[relation['id']]=(project['name'],relation)
    for project in review.get('projects',[]):
        for unit in project.get('review_units',[]):
            units[unit['id']]=(project['name'],unit)
    evidence={row['id']:row for row in context}
    if not isinstance(data['sections'],dict) or set(data['sections'])!=set(SECTION_KEYS):
        raise ValueError('review-receipts.sections 缺少或多出报告章节')
    expected_audit=_unresolved_by_unit(analysis,review)
    audit=data['audit_unresolved_by_unit']
    if not isinstance(audit,dict) or set(audit)!=set(expected_audit):
        raise ValueError('review-receipts.audit_unresolved_by_unit 与固定分析范围不一致')

    def check_sources(label,entry,unit_ids,require):
        fact_ids=entry.get('fact_ids')
        evidence_ids=entry.get('evidence_ids')
        if not isinstance(fact_ids,list) or not isinstance(evidence_ids,list):
            raise ValueError(label+' fact_ids/evidence_ids 必须为数组')
        if len(set(fact_ids))!=len(fact_ids) or len(set(evidence_ids))!=len(evidence_ids):
            raise ValueError(label+' 来源 ID 不得重复')
        if not all(isinstance(x,str) and x for x in fact_ids+evidence_ids):
            raise ValueError(label+' 来源 ID 必须为非空字符串')
        if require and (not unit_ids or not fact_ids or not evidence_ids):
            raise ValueError(label+' 必须关联 unit、fact 与 evidence ID')
        if not require and (fact_ids or evidence_ids or unit_ids):
            raise ValueError(label+' 不适用时不得提交无关联来源')
        permitted_facts=set()
        for unit_id in unit_ids:
            project,unit=units[unit_id]
            own_facts={fid for fid in unit.get('fact_ids',[])
                       if fid in facts and facts[fid][0]==project}
            permitted_facts.update(own_facts)
            if require:
                selected_facts=own_facts & set(fact_ids)
                if not selected_facts:
                    raise ValueError(label+' 的 unit '+unit_id+' 缺少属于自身的 fact')
                own_evidence={eid for fid in selected_facts
                              for eid in facts[fid][1].get('evidence_ids') or []}
                if not own_evidence & set(evidence_ids):
                    raise ValueError(label+' 的 unit '+unit_id+' 缺少关联 evidence')
        if not set(fact_ids)<=permitted_facts:
            raise ValueError(label+' fact ID 不属于关联 unit')
        permitted_evidence={eid for fid in fact_ids
                            for eid in facts[fid][1].get('evidence_ids') or []}
        if not set(evidence_ids)<=permitted_evidence:
            raise ValueError(label+' evidence ID 不属于所引用 fact')
        for eid in evidence_ids:
            record=evidence.get(eid)
            if record is None or not any(eid in facts[fid][1].get('evidence_ids',[])
                                         and record.get('project')==facts[fid][0]
                                         for fid in fact_ids):
                raise ValueError(label+' evidence ID 缺失或跨项目')

    body=sections(report,[title for _,title in heading_positions(report)])
    for key in SECTION_KEYS:
        entry=data['sections'][key]
        if not isinstance(entry,dict) or set(entry)!={'status','unit_ids','fact_ids','evidence_ids'}:
            raise ValueError(key+' 回执字段不完整')
        status=entry['status']
        if status not in ('pending','assessed','not_applicable'):
            raise ValueError(key+' 回执状态不合法')
        if status=='pending':
            raise ValueError(key+' 尚未完成评估回执')
        unit_ids=entry['unit_ids']
        if not isinstance(unit_ids,list) or len(set(unit_ids))!=len(unit_ids):
            raise ValueError(key+' unit_ids 必须为无重复数组')
        if any(uid not in units for uid in unit_ids):
            raise ValueError(key+' 引用了不存在的 unit')
        if key==IMPACT_KEY:
            special=body.get('## 4. 特殊逻辑与测试关注点','').strip()
            values=existing_impact_values(special)
            if special!='无' and len(values)!=1:
                raise ValueError('既有功能影响条目缺失或重复')
            empty=special=='无'
        else:
            empty=body.get(key,'').strip()=='无'
        if (status=='not_applicable')!=empty:
            raise ValueError(key+' 回执状态与报告是否为“无”不一致')
        check_sources(key,entry,unit_ids,status=='assessed')
    for unit_id,expected in sorted(expected_audit.items()):
        entry=audit[unit_id]
        if not isinstance(entry,dict) or set(entry)!={'status','relation_ids','fact_ids','evidence_ids'}:
            raise ValueError(unit_id+' unresolved 回执字段不完整')
        status=entry['status']
        if status not in ('pending','assessed','deferred'):
            raise ValueError(unit_id+' unresolved 回执状态不合法')
        if status=='pending':
            raise ValueError(unit_id+' 存在未处理的 unresolved 关系')
        if entry['relation_ids']!=expected:
            raise ValueError(unit_id+' unresolved 关系 ID 与分析范围不符')
        project,_=units[unit_id]
        if any(rid not in relations or relations[rid][0]!=project
               for rid in expected):
            raise ValueError(unit_id+' unresolved 关系跨项目或已缺失')
        if status=='deferred':
            # 未核实只保留关系身份与状态，不要求 Agent 为未评估事项伪造 fact/evidence。
            if entry['fact_ids']!=[] or entry['evidence_ids']!=[]:
                raise ValueError('audit '+unit_id+' deferred 不得伪装成已核实的依据')
            continue
        check_sources('audit '+unit_id,entry,[unit_id],True)
        related=set(eid for rid in expected
                    for eid in relations[rid][1].get('evidence_ids') or [])
        if not set(entry['evidence_ids']) & related:
            raise ValueError('audit '+unit_id+' evidence 必须关联待复核关系')
    return receipt_counts(data)
