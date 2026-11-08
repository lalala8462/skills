#!/usr/bin/env python3
"""初始化并刷新 Agent 直接编辑的 Markdown 报告；不承载业务归纳。"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from validate_report import heading_positions, sections

AGENT_HEADINGS=(
    '### 2.1 需求改动',
    '### 2.2 表字段 DDL 修改',
    '### 2.3 表配置 DML 准备清单',
    '### 3.1 整体流程说明',
    '### 3.2 Mermaid 流程图',
    '## 4. 特殊逻辑与测试关注点',
)
MACHINE_HEADINGS=(
    '## 1. 分析范围',
    '### 2.4 配置文件调整',
    '## 5. 测试用例导出',
    '## 6. 验证情况',
)
AGENT_PLACEHOLDER='【Agent填写：基于完整复核事实形成业务结论；无相关内容时填写“无”】'
TEST_PLACEHOLDER='【脚本将在测试用例导出后更新】'
DRAFT_MARKER='<!-- diff-list-status: draft -->'
DRAFT_STATUS='**报告状态：未完成。** 业务章节待 Agent 复核，校验通过前不作为正式交付。'


def _template_headings(template):
    return [title for _,title in heading_positions(template)]


def _validate_template(template):
    headings=_template_headings(template)
    required=set(AGENT_HEADINGS)|set(MACHINE_HEADINGS)
    missing=sorted(required-set(headings))
    if not headings:
        raise ValueError('report template has no headings')
    if missing:
        raise ValueError('report template is missing required headings: '+', '.join(missing))
    return headings


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
            elif fact.get('kind') in ('configuration_parse_boundary','xml_file_change'):
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


def verification_section():
    return ('已完成固定 Git 快照静态分析、复核范围校验、review unit 到源码事实/证据的自动展开校验、'
            '测试用例结构校验和报告结构预检。未执行构建、单元测试、数据库验证或环境联调。')


def test_summary(cases,excel_name):
    from case_contract import PURPOSES,PURPOSE_NAMES
    groups=cases.get('purpose_cases')
    if not isinstance(groups,dict) or set(groups)!=set(PURPOSES):
        raise ValueError('cases must contain exactly the four current purpose arrays')
    counts=[len(groups[purpose]) for purpose in PURPOSES]
    total=sum(counts)
    if not total:
        return '无'
    if not excel_name or Path(excel_name).name!=excel_name or not excel_name.lower().endswith('.xlsx'):
        raise ValueError('nonempty cases require an Excel basename ending in .xlsx')
    return 'Excel：[{}]({})；'.format(excel_name,excel_name)+'；'.join(
        '{}用例数量：{}'.format(name,count) for name,count in zip(PURPOSE_NAMES,counts))+'；总用例数量：{}'.format(total)


def _render(template,bodies):
    headings=_validate_template(template)
    template_bodies=sections(template,headings)
    output=[headings[0],'',DRAFT_MARKER,DRAFT_STATUS,'']
    for heading in headings[1:]:
        # 仅替换明确由 Agent/脚本管理的章节，保留模板的其它静态提示。
        output.extend(['',heading,'',bodies.get(heading,template_bodies.get(heading,'')).strip()])
    return '\n'.join(output).rstrip()+'\n'


def initial_report(template,analysis):
    headings=_validate_template(template)
    defaults=sections(template,headings)
    bodies={heading:AGENT_PLACEHOLDER for heading in AGENT_HEADINGS}
    # 既有功能影响是模板必有填写项；不用预填“无”来替代 Agent 的判断。
    special='## 4. 特殊逻辑与测试关注点'
    special_template=defaults.get(special,'')
    if '既有功能影响' not in special_template or AGENT_PLACEHOLDER not in special_template:
        raise ValueError('report template must prefill 既有功能影响 with Agent placeholder')
    bodies[special]=special_template
    bodies.update({
        '## 1. 分析范围':scope_section(analysis),
        '### 2.4 配置文件调整':configuration_section(analysis),
        '## 5. 测试用例导出':TEST_PLACEHOLDER,
        '## 6. 验证情况':verification_section(),
    })
    return _render(template,bodies)


def refresh_report(current,template,analysis,cases,excel_name):
    want=_validate_template(template)
    got=[title for _,title in heading_positions(current)]
    if got!=want:
        raise ValueError('报告标题及顺序与模板不一致，不能安全刷新脚本维护章节')
    markers=current.count(DRAFT_MARKER)
    statuses=current.count('报告状态：未完成')
    if markers!=statuses or markers>1:
        raise ValueError('报告存在不一致或重复的草稿状态标记')
    existing=sections(current,got)
    bodies={heading:existing.get(heading,'').strip() for heading in AGENT_HEADINGS}
    bodies.update({
        '## 1. 分析范围':scope_section(analysis),
        '### 2.4 配置文件调整':configuration_section(analysis),
        '## 5. 测试用例导出':test_summary(cases,excel_name),
        '## 6. 验证情况':verification_section(),
    })
    return _render(template,bodies)


def _atomic_replace(path,text):
    target=Path(path)
    handle=tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',newline='\n',
        prefix=target.name+'.',suffix='.tmp',dir=str(target.parent),delete=False)
    temporary=Path(handle.name)
    try:
        with handle:
            handle.write(text)
        os.replace(str(temporary),str(target))
    finally:
        if temporary.exists():
            temporary.unlink()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--init',action='store_true',help='初始化 Agent 直接编辑的 report.md')
    mode.add_argument('--refresh',action='store_true',help='保留 Agent 章节并刷新脚本维护章节')
    parser.add_argument('--analysis',required=True)
    parser.add_argument('--report',required=True)
    parser.add_argument('--cases',help='refresh 时使用的 test-cases.json')
    parser.add_argument('--excel',help='refresh 时已生成的 Excel 路径')
    parser.add_argument('--template',default=str(Path(__file__).resolve().parents[1]/'assets'/'diff-list-template.md'))
    args=parser.parse_args(argv)
    try:
        analysis=json.loads(Path(args.analysis).read_text(encoding='utf-8-sig'))
        template=Path(args.template).read_text(encoding='utf-8-sig')
        report=Path(args.report)
        report.parent.mkdir(parents=True,exist_ok=True)
        if args.init:
            if args.cases or args.excel:
                raise ValueError('--init 不接受 --cases/--excel')
            if report.exists():
                raise ValueError('report already exists')
            with report.open('x',encoding='utf-8',newline='\n') as stream:
                stream.write(initial_report(template,analysis))
            print('REPORT_DRAFT: '+str(report.resolve()))
            return 0

        if not args.cases:
            raise ValueError('--refresh 必须提供 --cases')
        if not report.is_file():
            raise ValueError('report draft does not exist')
        cases=json.loads(Path(args.cases).read_text(encoding='utf-8-sig'))
        excel_path=Path(args.excel) if args.excel else None
        if sum(len(cases.get('purpose_cases',{}).get(name,[])) for name in ('acceptance','core_regression','related_regression','boundary')):
            if excel_path is None or not excel_path.is_file():
                raise ValueError('test cases exist but the separate Excel file is missing')
        rendered=refresh_report(
            report.read_text(encoding='utf-8-sig'),template,analysis,cases,
            excel_path.name if excel_path else None)
        _atomic_replace(report,rendered)
        print('REPORT_REFRESHED: '+str(report.resolve()))
        return 0
    except (OSError,UnicodeError,ValueError,KeyError,TypeError,IndexError) as exc:
        print('ERROR: '+str(exc),file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
