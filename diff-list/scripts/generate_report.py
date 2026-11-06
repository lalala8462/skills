#!/usr/bin/env python3
"""Render a report candidate from Agent business conclusions plus deterministic analysis data."""
import argparse
import json
import re
import sys
from pathlib import Path

REPORT_FIELDS=('requirements','ddl','dml','flow','mermaid','special_rules','existing_impact')
SECTION_HEADINGS={
    'requirements':'### 2.1 需求改动',
    'ddl':'### 2.2 表字段 DDL 修改',
    'dml':'### 2.3 表配置 DML 准备清单',
    'flow':'### 3.1 整体流程说明',
    'mermaid':'### 3.2 Mermaid 流程图'
}


def empty_report_data():
    """只生成 Agent 必须填写的业务字段。"""
    return {key:'' for key in REPORT_FIELDS}


def headings(text):
    return [line.strip() for line in text.splitlines() if re.fullmatch(r'#{1,6} .+', line.strip())]


def _validate_report_data(report_data):
    if not isinstance(report_data,dict) or set(report_data)!=set(REPORT_FIELDS):
        missing=sorted(set(REPORT_FIELDS)-set(report_data or {}))
        extra=sorted(set(report_data or {})-set(REPORT_FIELDS))
        raise ValueError('report-data requires fixed business fields; missing={}, extra={}'.format(missing,extra))
    for key,value in report_data.items():
        if not isinstance(value,str) or not value.strip():
            raise ValueError('report field {} requires reviewed text or the explicit word 无'.format(key))
        if re.search(r'(?m)^\s*#{1,6}\s+',value):
            raise ValueError('report field {} must not contain Markdown headings'.format(key))


def scope_section(analysis):
    from validate_report import expected_project_shas
    mappings=expected_project_shas(analysis)
    if not mappings:
        raise ValueError('analysis.json 中无法推导项目与完整 Git SHA')
    return '\n'.join('- '+pair for pair in mappings)


def configuration_section(analysis):
    changes=[]
    boundaries=[]
    for project in analysis.get('projects',[]):
        for fact in project.get('facts',[]):
            data=fact.get('data') or {}
            if fact.get('kind')=='configuration_file_change':
                changes.append(dict(project=project.get('name'),path=data.get('path'),key=data.get('key'),
                    change=data.get('change'),before=data.get('before'),after=data.get('after')))
            elif fact.get('kind')=='configuration_parse_boundary':
                boundaries.append(dict(project=project.get('name'),path=data.get('path'),
                                       reason=data.get('reason') or data.get('kind') or '无法结构化解析'))
    if not changes and not boundaries:
        return '无'
    lines=[]
    normalized=[]
    for row in sorted(changes,key=lambda r:(str(r['project']),str(r['path']),str(r['key']))):
        lines.append('- {} / {}：{} {}'.format(
            row.get('project') or '',row.get('path') or '',row.get('key') or '',row.get('change') or 'modified'))
        normalized.append('{} | {} | before={} | after={}'.format(
            row.get('path') or '',row.get('key') or '',row.get('before'),row.get('after')))
    for row in sorted(boundaries,key=lambda r:(str(r['project']),str(r['path']))):
        lines.append('- {} / {}：配置结构无法自动展开，需按源码证据复核。'.format(
            row.get('project') or '',row.get('path') or ''))
        normalized.append('{} | parse-boundary | {}'.format(row.get('path') or '',row.get('reason') or ''))
    return '\n'.join(lines)+"\n\n~~~text\n"+'\n'.join(normalized)+"\n~~~"


def special_section(report_data):
    return '特殊规则：{}\n\n既有功能影响：{}'.format(
        report_data['special_rules'].strip(),report_data['existing_impact'].strip())


def verification_section():
    return ('已完成固定 Git 快照静态分析、复核范围校验、review unit 到源码事实/证据的自动展开校验、'
            '测试用例结构校验和报告结构预检。未执行构建、单元测试、数据库验证或环境联调。')


def render(template,report_data,analysis,cases,excel_name):
    _validate_report_data(report_data)
    template_headings=headings(template)
    if not template_headings:
        raise ValueError('report template has no headings')
    required=set(SECTION_HEADINGS.values())|{
        '## 1. 分析范围','### 2.4 配置文件调整','## 4. 特殊逻辑与测试关注点',
        '## 5. 测试用例导出','## 6. 验证情况'}
    missing=sorted(required-set(template_headings))
    if missing:
        raise ValueError('report template is missing required headings: '+', '.join(missing))

    from case_contract import PURPOSES,PURPOSE_NAMES
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

    bodies={SECTION_HEADINGS[key]:report_data[key].strip() for key in SECTION_HEADINGS}
    bodies.update({
        '## 1. 分析范围':scope_section(analysis),
        '### 2.4 配置文件调整':configuration_section(analysis),
        '## 4. 特殊逻辑与测试关注点':special_section(report_data),
        '## 5. 测试用例导出':summary,
        '## 6. 验证情况':verification_section()
    })
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


def preflight(rendered,template,analysis,cases):
    marker='<!-- diff-list-status: draft -->'
    status='报告状态：未完成'
    draft_line=re.compile(
        r'(?m)^<!-- diff-list-status: draft -->\r?\n'
        r'\*\*报告状态：未完成。\*\* 结构化报告候选，校验通过前不作为正式交付。\r?\n?')
    candidate_text,replaced=draft_line.subn('',rendered,count=1)
    if replaced!=1 or rendered.count(marker)!=1 or rendered.count(status)!=1:
        return ['生成候选缺少唯一且完整的草稿标记']
    from validate_report import validate,validate_case_counts,expected_project_shas
    mappings=expected_project_shas(analysis)
    if not mappings:
        return ['analysis.json 中无法推导项目与完整 Git SHA']
    errors,_=validate(candidate_text,template,mappings)
    errors.extend(validate_case_counts(candidate_text,cases,analysis))
    return errors


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',required=True,help='Agent 业务结论 JSON')
    parser.add_argument('--analysis',required=True,help='本次固定分析结果')
    parser.add_argument('--review',required=True,help='本次聚合复核结果')
    parser.add_argument('--cases',required=True,help='本次四类结构化用例 JSON')
    parser.add_argument('--excel',help='已生成的独立 Excel 路径；报告内只写文件名')
    parser.add_argument('--template',default=str(Path(__file__).resolve().parents[1]/'assets'/'diff-list-template.md'))
    parser.add_argument('--output',required=True,help='新建报告候选路径；不得指向已交付报告')
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
                raise ValueError('Excel and report candidate must share a directory')
        rendered,counts,total=render(template,data,analysis,cases,excel_path.name if excel_path else None)
        errors=preflight(rendered,template,analysis,cases)
        if errors:
            raise ValueError('report preflight failed: '+'; '.join(errors[:12]))
        output=Path(args.output)
        output.parent.mkdir(parents=True,exist_ok=True)
        with output.open('x',encoding='utf-8',newline='\n') as stream:
            stream.write(rendered)
        print('CANDIDATE: {} cases={} output={}'.format(total,counts,output.resolve()))
        return 0
    except (OSError,UnicodeError,ValueError,KeyError,TypeError,IndexError) as exc:
        print('ERROR: '+str(exc),file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
