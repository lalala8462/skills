#!/usr/bin/env python3
"""在 Excel/报告生成前一次性检查 Agent 输入的纯格式与当前分析身份。"""
import argparse
import json
import re
import sys
from pathlib import Path

from case_contract import PURPOSES, validate_cases
from generate_report import REPORT_FIELDS
from scope_validation import validate_scope_files
from validate_report import fenced_blocks


CASE_FIELDS=('case_id','title','preconditions','steps','expected','observable_evidence','review_unit_ids')
OBSERVATION_FIELDS=('surface','target','assertion')


def _nonempty_text(value):
    return isinstance(value,str) and bool(value.strip())


def report_data_errors(data):
    errors=[]
    if not isinstance(data,dict):
        return ['report-data 必须是 JSON object']
    missing=sorted(set(REPORT_FIELDS)-set(data))
    extra=sorted(set(data)-set(REPORT_FIELDS))
    if missing:
        errors.append('report-data 缺少字段: '+', '.join(missing))
    if extra:
        errors.append('report-data 包含不支持字段: '+', '.join(extra))
    for key in REPORT_FIELDS:
        if key not in data:
            continue
        value=data[key]
        if not _nonempty_text(value):
            errors.append('report-data.{} 必须填写复核结论或明确写“无”'.format(key))
            continue
        if re.search(r'(?m)^\s*#{1,6}\s+',value):
            errors.append('report-data.{} 不允许包含 Markdown 标题'.format(key))
    flow=data.get('flow')
    mermaid=data.get('mermaid')
    if _nonempty_text(flow) and _nonempty_text(mermaid):
        need_diagram=flow.strip()!='无' or mermaid.strip()!='无'
        if need_diagram:
            if mermaid.strip()=='无':
                errors.append('整体流程非“无”时必须提供 Mermaid 代码块')
            else:
                backtick=chr(96)*3
                if mermaid.count(backtick)%2 or mermaid.count('~~~')%2:
                    errors.append('Mermaid 代码围栏未闭合')
                blocks=[body for lang,body in fenced_blocks(mermaid)
                        if lang.strip().lower()=='mermaid']
                if len(blocks)!=1:
                    errors.append('mermaid 字段必须且只能包含一个 mermaid 代码块')
                elif not re.match(r'^\s*(?:flowchart TD|sequenceDiagram)\s*(?:\n|$)',blocks[0]):
                    errors.append('Mermaid 必须以 flowchart TD 或 sequenceDiagram 开始，禁止 graph TD')
    return errors


def case_shape_errors(data):
    """收集可一次性发现的 JSON/字段格式问题；严格身份校验仍交给 case_contract。"""
    errors=[]
    if not isinstance(data,dict):
        return ['test-cases 必须是 JSON object']
    groups=data.get('purpose_cases')
    if not isinstance(groups,dict):
        return ['test-cases.purpose_cases 必须是 object']
    missing=sorted(set(PURPOSES)-set(groups))
    extra=sorted(set(groups)-set(PURPOSES))
    if missing:
        errors.append('purpose_cases 缺少分组: '+', '.join(missing))
    if extra:
        errors.append('purpose_cases 包含不支持分组: '+', '.join(extra))
    seen=set()
    for purpose in PURPOSES:
        rows=groups.get(purpose)
        if rows is None:
            continue
        if not isinstance(rows,list):
            errors.append('purpose_cases.{} 必须是 array'.format(purpose))
            continue
        for index,case in enumerate(rows,1):
            prefix='purpose_cases.{}[{}]'.format(purpose,index-1)
            if not isinstance(case,dict):
                errors.append(prefix+' 必须是 object')
                continue
            missing_fields=sorted(set(CASE_FIELDS)-set(case))
            extra_fields=sorted(set(case)-set(CASE_FIELDS))
            if missing_fields:
                errors.append(prefix+' 缺少字段: '+', '.join(missing_fields))
            if extra_fields:
                errors.append(prefix+' 包含不支持字段: '+', '.join(extra_fields))
            case_id=case.get('case_id')
            if not _nonempty_text(case_id):
                errors.append(prefix+'.case_id 必须是非空字符串')
            elif case_id in seen:
                errors.append(prefix+'.case_id 重复: '+case_id)
            else:
                seen.add(case_id)
            for key in ('title','preconditions'):
                if key in case and not _nonempty_text(case[key]):
                    errors.append(prefix+'.{} 必须是非空字符串'.format(key))
            for key in ('steps','expected','review_unit_ids'):
                if key not in case:
                    continue
                values=case[key]
                if not isinstance(values,list) or not values:
                    errors.append(prefix+'.{} 必须是非空数组'.format(key))
                elif any(not _nonempty_text(value) for value in values):
                    errors.append(prefix+'.{} 只能包含非空字符串'.format(key))
            observations=case.get('observable_evidence')
            if observations is not None:
                if not isinstance(observations,list) or not observations:
                    errors.append(prefix+'.observable_evidence 必须是非空数组')
                else:
                    for obs_index,item in enumerate(observations):
                        obs_prefix=prefix+'.observable_evidence[{}]'.format(obs_index)
                        if not isinstance(item,dict):
                            errors.append(obs_prefix+' 必须是 object')
                            continue
                        if set(item)!=set(OBSERVATION_FIELDS):
                            errors.append(obs_prefix+' 字段必须固定为 surface/target/assertion')
                            continue
                        for key in OBSERVATION_FIELDS:
                            if not _nonempty_text(item[key]):
                                errors.append(obs_prefix+'.{} 必须是非空字符串'.format(key))
    return errors


def validate_inputs(analysis_path,review_path,cases_path,report_data_path):
    errors=[]
    try:
        analysis=json.loads(Path(analysis_path).read_text(encoding='utf-8-sig'))
    except (OSError,ValueError) as exc:
        return ['无法读取 analysis.json: '+str(exc)]
    try:
        review=json.loads(Path(review_path).read_text(encoding='utf-8-sig'))
    except (OSError,ValueError) as exc:
        return ['无法读取 review.json: '+str(exc)]
    try:
        cases=json.loads(Path(cases_path).read_text(encoding='utf-8-sig'))
    except (OSError,ValueError) as exc:
        return ['无法读取 test-cases.json: '+str(exc)]
    try:
        report_data=json.loads(Path(report_data_path).read_text(encoding='utf-8-sig'))
    except (OSError,ValueError) as exc:
        return ['无法读取 report-data.json: '+str(exc)]

    errors.extend(case_shape_errors(cases))
    errors.extend(report_data_errors(report_data))
    try:
        validate_scope_files(analysis_path,review_path,cases_path)
    except (ValueError,KeyError,TypeError,OSError) as exc:
        errors.append('分析范围/身份不一致: '+str(exc))
    try:
        validate_cases(cases,analysis,review)
    except (ValueError,KeyError,TypeError) as exc:
        errors.append('测试用例严格契约失败: '+str(exc))
    return list(dict.fromkeys(errors))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis',required=True)
    parser.add_argument('--review',required=True)
    parser.add_argument('--cases',required=True)
    parser.add_argument('--report-data',required=True)
    args=parser.parse_args(argv)
    errors=validate_inputs(args.analysis,args.review,args.cases,args.report_data)
    for error in errors:
        print('ERROR: '+error)
    print('PREFLIGHT: '+('FAIL' if errors else 'PASS'))
    return 1 if errors else 0


if __name__=='__main__':
    sys.exit(main())
