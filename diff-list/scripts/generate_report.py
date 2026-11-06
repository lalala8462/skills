#!/usr/bin/env python3
"""Render a report candidate from semantic section content and test-case data."""
import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

SECTION_HEADINGS={
    'scope':'## 1. 分析范围',
    'requirements':'### 2.1 需求改动',
    'ddl':'### 2.2 表字段 DDL 修改',
    'dml':'### 2.3 表配置 DML 准备清单',
    'config':'### 2.4 配置文件调整',
    'flow':'### 3.1 整体流程说明',
    'mermaid':'### 3.2 Mermaid 流程图',
    'special':'## 4. 特殊逻辑与测试关注点',
    'verification':'## 6. 验证情况'
}
SECTION_KEYS=tuple(SECTION_HEADINGS)


def headings(text):
    return [line.strip() for line in text.splitlines() if re.fullmatch(r'#{1,6} .+', line.strip())]


def render(template, report_data, cases, excel_name):
    template_headings=headings(template)
    if not template_headings:
        raise ValueError('report template has no headings')
    missing_template=[heading for heading in SECTION_HEADINGS.values() if heading not in template_headings]
    if missing_template:
        raise ValueError('report template is missing required headings: '+', '.join(missing_template))

    sections=report_data.get('sections')
    if not isinstance(sections,dict) or set(sections)!=set(SECTION_KEYS):
        missing=sorted(set(SECTION_KEYS)-set(sections or {}))
        extra=sorted(set(sections or {})-set(SECTION_KEYS))
        raise ValueError('report sections require fixed semantic keys; missing={}, extra={}'.format(missing,extra))
    for key,value in sections.items():
        if not isinstance(value,str) or not value.strip():
            raise ValueError('report section {} requires reviewed text or the explicit word 无'.format(key))
        if re.search(r'(?m)^\s*#{1,6}\s+',value):
            raise ValueError('report section {} must not contain Markdown headings'.format(key))

    from case_contract import PURPOSES, PURPOSE_NAMES
    groups=cases.get('purpose_cases')
    if not isinstance(groups,dict) or set(groups)!=set(PURPOSES):
        raise ValueError('cases must contain exactly the four current purpose arrays')
    counts=[len(groups[purpose]) for purpose in PURPOSES]
    total=sum(counts)
    if total and (not excel_name or Path(excel_name).name!=excel_name or not excel_name.lower().endswith('.xlsx')):
        raise ValueError('nonempty cases require an Excel basename ending in .xlsx')
    if total:
        summary='Excel：[{}]({})；'.format(excel_name,excel_name)+'；'.join(
            '{}用例数量：{}'.format(name,count) for name,count in zip(PURPOSE_NAMES,counts))+'；总用例数量：{}'.format(total)
    else:
        summary='无'

    bodies={SECTION_HEADINGS[key]:sections[key].strip() for key in SECTION_KEYS}
    bodies['## 5. 测试用例导出']=summary
    title=template_headings[0]
    output=[]
    for line in template.splitlines():
        stripped=line.strip()
        if stripped in template_headings:
            output.append(stripped)
            if stripped==title:
                output.extend(['','<!-- diff-list-status: draft -->',
                    '**报告状态：未完成。** 结构化报告候选，校验通过前不作为正式交付。',''])
                continue
            body=bodies.get(stripped)
            if body:
                output.extend(['',body,''])
        else:
            output.append(line)
    return '\n'.join(output).rstrip()+'\n',counts,total


def expected_project_shas(analysis):
    """从本次固定取证结果提取报告必须逐行对应的项目与修订。"""
    expected=[]
    for project in analysis.get('projects',[]):
        name=project.get('name')
        git_data=project.get('git') or {}
        for item in git_data.get('selection',[]):
            sha=item.get('head') if item.get('type')=='range' else item.get('sha')
            if name and isinstance(sha,str) and re.fullmatch(r'[0-9a-fA-F]{40}',sha):
                expected.append('{}={}'.format(name,sha))
    return list(dict.fromkeys(expected))


def preflight(rendered,template,analysis,cases,expected):
    """候选报告落盘前复用正式报告正文校验，并补验分析边界和用例数量。"""
    marker='<!-- diff-list-status: draft -->'
    status='报告状态：未完成'
    draft_line=re.compile(
        r'(?m)^<!-- diff-list-status: draft -->\r?\n'
        r'\*\*报告状态：未完成。\*\* 结构化报告候选，校验通过前不作为正式交付。\r?\n?')
    candidate_text,replaced=draft_line.subn('',rendered,count=1)
    if replaced!=1 or rendered.count(marker)!=1 or rendered.count(status)!=1:
        return ['生成候选缺少唯一且完整的草稿标记']

    from validate_report import validate, validate_case_counts
    mappings=expected or expected_project_shas(analysis)
    errors,_=validate(candidate_text,template,mappings)
    errors.extend(validate_case_counts(candidate_text,cases,analysis))
    if not mappings:
        errors.append('无法从 analysis.json 推导项目与 SHA；请传入 --expect 项目名=40位SHA')
    return errors


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',required=True,help='结构化报告 JSON：sections 使用固定语义字段')
    parser.add_argument('--analysis',required=True,help='本次固定分析结果')
    parser.add_argument('--review',required=True,help='本次快审结果')
    parser.add_argument('--cases',required=True,help='本次四类结构化用例 JSON')
    parser.add_argument('--excel',help='已生成的独立 Excel 路径；报告内只写文件名')
    parser.add_argument('--template',default=str(Path(__file__).resolve().parents[1]/'assets'/'diff-list-template.md'))
    parser.add_argument('--output',required=True,help='新建报告候选路径；不得指向已交付报告')
    parser.add_argument('--expect',action='append',default=[],
                        help='项目名=40位SHA；可重复，未提供时从 analysis.json 推导')
    parser.add_argument('--replace-draft',action='store_true',
                        help='只重写带 diff-list 草稿标记的候选报告，供结构化数据修正后重生成')
    args=parser.parse_args(argv)
    try:
        data=json.loads(Path(args.data).read_text(encoding='utf-8-sig'))
        analysis=json.loads(Path(args.analysis).read_text(encoding='utf-8-sig'))
        cases=json.loads(Path(args.cases).read_text(encoding='utf-8-sig'))
        template=Path(args.template).read_text(encoding='utf-8-sig')
        from scope_validation import validate_scope_files
        validate_scope_files(args.analysis,args.review,args.cases)
        from case_contract import PURPOSES
        excel_path=Path(args.excel) if args.excel else None
        purpose_counts=[len(cases['purpose_cases'][p]) for p in PURPOSES]
        if sum(purpose_counts):
            if excel_path is None or not excel_path.is_file():
                raise ValueError('test cases exist but the separate Excel file is missing')
            if excel_path.resolve().parent!=Path(args.output).resolve().parent:
                raise ValueError('Excel and report candidate must share a directory for the relative workbook link')
        rendered,counts,total=render(template,data,cases,excel_path.name if excel_path else None)
        errors=preflight(rendered,template,analysis,cases,args.expect)
        if errors:
            raise ValueError('report preflight failed: '+'; '.join(errors[:12]))
        output=Path(args.output)
        output.parent.mkdir(parents=True,exist_ok=True)
        if output.exists():
            if not args.replace_draft:
                raise ValueError('output already exists; use --replace-draft only for an internal draft candidate')
            old=output.read_text(encoding='utf-8-sig')
            if (old.count('<!-- diff-list-status: draft -->')!=1 or
                    old.count('报告状态：未完成')!=1):
                raise ValueError('--replace-draft refuses to overwrite a file without one internal draft marker and status')
            handle=tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',newline='\n',
                prefix=output.name+'.',suffix='.tmp',dir=str(output.parent),delete=False)
            temporary=Path(handle.name)
            with handle:
                handle.write(rendered)
            os.replace(str(temporary),str(output))
        else:
            with output.open('x',encoding='utf-8',newline='\n') as stream:
                stream.write(rendered)
        print('CANDIDATE: {} cases={} output={}'.format(total,counts,output.resolve()))
        return 0
    except (OSError,UnicodeError,ValueError,KeyError,TypeError,IndexError) as exc:
        print('ERROR: '+str(exc),file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
