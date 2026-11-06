#!/usr/bin/env python3
"""在 Excel/报告刷新前一次性检查 Agent 输入格式与当前分析身份。"""
import argparse
import json
import re
import sys
from pathlib import Path

from case_contract import PURPOSES, validate_cases
from generate_report import AGENT_HEADINGS, AGENT_PLACEHOLDER, DRAFT_MARKER
from scope_validation import validate_scope_files
from validate_report import fenced_blocks, heading_positions, sections


CASE_FIELDS=('case_id','title','preconditions','steps','expected','observable_evidence','review_unit_ids')
OBSERVATION_FIELDS=('surface','target','assertion')


def _nonempty_text(value):
    return isinstance(value,str) and bool(value.strip())


def report_errors(text,template):
    """只检查 Agent 直接维护的业务章节和 Markdown 结构；脚本维护章节在 refresh 时覆盖。"""
    errors=[]
    want=[title for _,title in heading_positions(template)]
    got=[title for _,title in heading_positions(text)]
    if got!=want:
        errors.append('report.md 标题及顺序与模板不一致')
        return errors
    if text.count(DRAFT_MARKER)!=1 or text.count('报告状态：未完成')!=1:
        errors.append('report.md 必须保留唯一草稿标记，finalize 前不得手工删除')
    body=sections(text,got)
    for heading in AGENT_HEADINGS:
        value=body.get(heading,'').strip()
        if not value or value==AGENT_PLACEHOLDER:
            errors.append('{} 必须填写复核结论或明确写“无”'.format(heading))

    flow=body.get('### 3.1 整体流程说明','').strip()
    mermaid=body.get('### 3.2 Mermaid 流程图','').strip()
    if flow and mermaid and (flow!='无' or mermaid!='无'):
        if mermaid=='无':
            errors.append('整体流程非“无”时必须提供 Mermaid 代码块')
        else:
            backtick=chr(96)*3
            if mermaid.count(backtick)%2 or mermaid.count('~~~')%2:
                errors.append('Mermaid 代码围栏未闭合')
            blocks=[content for lang,content in fenced_blocks(mermaid)
                    if lang.strip().lower()=='mermaid']
            if len(blocks)!=1:
                errors.append('Mermaid 章节必须且只能包含一个 mermaid 代码块')
            elif not re.match(r'^\s*(?:flowchart TD|sequenceDiagram)\s*(?:\n|$)',blocks[0]):
                errors.append('Mermaid 必须以 flowchart TD 或 sequenceDiagram 开始，禁止 graph TD')

    special=body.get('## 4. 特殊逻辑与测试关注点','').strip()
    if AGENT_PLACEHOLDER in special:
        errors.append('特殊逻辑章节的“既有功能影响”仍是模板占位，请填写实际结论或“无”')
    elif special and special!='无' and '既有功能影响' not in special:
        errors.append('特殊逻辑章节非“无”时必须包含“既有功能影响”说明')
    return errors


def case_shape_errors(data):
    """收集可一次性发现的 JSON/字段格式问题；严格身份校验仍交给 case_contract。"""
    errors=[]
    if not isinstance(data,dict):
        return ['test-cases 必须是 JSON object']
    root_fields={'schema_version','analysis_id','purpose_cases'}
    missing_root=sorted(root_fields-set(data))
    extra_root=sorted(set(data)-root_fields)
    if missing_root:
        errors.append('test-cases 缺少根字段: '+', '.join(missing_root))
    if extra_root:
        errors.append('test-cases 包含不支持根字段: '+', '.join(extra_root))
    if data.get('schema_version')!=4:
        errors.append('test-cases.schema_version 必须为 4')
    if not _nonempty_text(data.get('analysis_id')):
        errors.append('test-cases.analysis_id 必须是非空字符串')
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


def validate_inputs(analysis_path,review_path,cases_path,report_path,template_path):
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
        report=Path(report_path).read_text(encoding='utf-8-sig')
        template=Path(template_path).read_text(encoding='utf-8-sig')
    except (OSError,UnicodeError) as exc:
        return ['无法读取 report.md/template: '+str(exc)]

    errors.extend(case_shape_errors(cases))
    errors.extend(report_errors(report,template))
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
    parser.add_argument('--report',required=True)
    parser.add_argument('--template',default=str(Path(__file__).resolve().parents[1]/'assets'/'diff-list-template.md'))
    args=parser.parse_args(argv)
    errors=validate_inputs(args.analysis,args.review,args.cases,args.report,args.template)
    for error in errors:
        print('ERROR: '+error)
    print('PREFLIGHT: '+('FAIL' if errors else 'PASS'))
    return 1 if errors else 0


if __name__=='__main__':
    sys.exit(main())
