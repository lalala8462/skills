#!/usr/bin/env python3
"""Render a report candidate from structured section content and test-case data."""
import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path


def headings(text):
    return [line.strip() for line in text.splitlines() if re.fullmatch(r'#{1,6} .+', line.strip())]


def render(template, report_data, cases, excel_name):
    template_headings = headings(template)
    if not template_headings:
        raise ValueError('report template has no headings')
    sections = report_data.get('sections')
    if not isinstance(sections, dict):
        raise ValueError('report-data JSON requires a sections object keyed by exact template heading')
    export_heading = '## 5. 测试用例导出'
    title = template_headings[0]
    required = set(template_headings) - {title, export_heading}
    supplied = set(sections)
    if supplied != required:
        missing = sorted(required - supplied)
        extra = sorted(supplied - required)
        raise ValueError('report sections mismatch; missing={}, extra={}'.format(missing, extra))
    if any(not isinstance(value, str) or not value.strip() for value in sections.values()):
        raise ValueError('every report section must contain reviewed text or the explicit word 无')

    from test_points import PURPOSES, PURPOSE_NAMES
    groups = cases.get('purpose_cases')
    if not isinstance(groups, dict) or set(groups) != set(PURPOSES):
        raise ValueError('cases must contain exactly the four current purpose arrays')
    counts = [len(groups[purpose]) for purpose in PURPOSES]
    total = sum(counts)
    if total and (not excel_name or Path(excel_name).name != excel_name or not excel_name.lower().endswith('.xlsx')):
        raise ValueError('nonempty cases require an Excel basename ending in .xlsx')
    if total:
        summary = 'Excel：[{}]({})；'.format(excel_name, excel_name) + '；'.join(
            '{}用例数量：{}'.format(name, count) for name, count in zip(PURPOSE_NAMES, counts)) + '；总用例数量：{}'.format(total)
    else:
        summary = '无'

    bodies = dict(sections)
    bodies[export_heading] = summary
    output = []
    for line in template.splitlines():
        stripped = line.strip()
        if stripped in template_headings:
            output.append(stripped)
            if stripped == title:
                output.extend(['', '<!-- diff-list-status: draft -->',
                    '**报告状态：未完成。** 结构化报告候选，校验通过前不作为正式交付。', ''])
                continue
            body = bodies.get(stripped)
            if body:
                output.extend(['', body.strip(), ''])
        else:
            output.append(line)
    return '\n'.join(output).rstrip() + '\n', counts, total


def expected_project_shas(analysis):
    """从本次固定取证结果提取报告必须逐行对应的项目与修订。"""
    expected = []
    for project in analysis.get('projects', []):
        name = project.get('name')
        git_data = project.get('git') or {}
        for item in git_data.get('selection', []):
            sha = item.get('head') if item.get('type') == 'range' else item.get('sha')
            if name and isinstance(sha, str) and re.fullmatch(r'[0-9a-fA-F]{40}', sha):
                expected.append('{}={}'.format(name, sha))
    return list(dict.fromkeys(expected))


def preflight(rendered, template, analysis, cases, expected):
    """候选报告落盘前复用正式报告正文校验，并补验分析边界和用例数量。"""
    marker = '<!-- diff-list-status: draft -->'
    status = '报告状态：未完成'
    draft_line = re.compile(
        r'(?m)^<!-- diff-list-status: draft -->\r?\n'
        r'\*\*报告状态：未完成。\*\* 结构化报告候选，校验通过前不作为正式交付。\r?\n?')
    candidate_text, replaced = draft_line.subn('', rendered, count=1)
    if replaced != 1 or rendered.count(marker) != 1 or rendered.count(status) != 1:
        return ['生成候选缺少唯一且完整的草稿标记']

    from validate_report import validate, validate_boundary_summary, validate_case_counts
    mappings = expected or expected_project_shas(analysis)
    errors, _ = validate(candidate_text, template, mappings)
    errors.extend(validate_boundary_summary(candidate_text, analysis))
    errors.extend(validate_case_counts(candidate_text, cases, analysis))
    if not mappings:
        errors.append('无法从 analysis.json 推导项目与 SHA；请传入 --expect 项目名=40位SHA')
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True, help='结构化报告正文 JSON：sections 按模板标题映射 Markdown 内容')
    parser.add_argument('--analysis', required=True, help='本次固定分析结果')
    parser.add_argument('--review', required=True, help='本次快审或正式复核结果')
    parser.add_argument('--cases', required=True, help='本次四类结构化用例 JSON')
    parser.add_argument('--excel', help='已生成的独立 Excel 路径；报告内只写文件名')
    parser.add_argument('--template', default=str(Path(__file__).resolve().parents[1] / 'assets' / 'diff-list-template.md'))
    parser.add_argument('--output', required=True, help='新建报告候选路径；不得指向已交付报告')
    parser.add_argument('--scope-only', action='store_true', help='只校验快审选中范围')
    parser.add_argument('--expect', action='append', default=[],
                        help='项目名=40位SHA；可重复，未提供时从 analysis.json 推导')
    parser.add_argument('--replace-draft', action='store_true',
                        help='只重写带 diff-list 草稿标记的候选报告，供结构化数据修正后重生成')
    args = parser.parse_args(argv)
    try:
        data = json.loads(Path(args.data).read_text(encoding='utf-8-sig'))
        analysis = json.loads(Path(args.analysis).read_text(encoding='utf-8-sig'))
        cases = json.loads(Path(args.cases).read_text(encoding='utf-8-sig'))
        template = Path(args.template).read_text(encoding='utf-8-sig')
        if args.scope_only:
            from scope_validation import validate_scope_files
            validate_scope_files(args.analysis, args.review, args.cases)
        else:
            from validate_analysis import validate_files
            errors, _ = validate_files(args.analysis, args.review, args.cases)
            if errors:
                raise ValueError('coverage validation failed: ' + '; '.join(errors[:8]))
        from test_points import PURPOSES
        excel_path = Path(args.excel) if args.excel else None
        purpose_counts = [len(cases['purpose_cases'][p]) for p in PURPOSES]
        if sum(purpose_counts):
            if excel_path is None or not excel_path.is_file():
                raise ValueError('test cases exist but the separate Excel file is missing')
            if excel_path.resolve().parent != Path(args.output).resolve().parent:
                raise ValueError('Excel and report candidate must share a directory for the relative workbook link')
        rendered, counts, total = render(template, data, cases, excel_path.name if excel_path else None)
        errors = preflight(rendered, template, analysis, cases, args.expect)
        if errors:
            raise ValueError('report preflight failed: ' + '; '.join(errors[:12]))
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            if not args.replace_draft:
                raise ValueError('output already exists; use --replace-draft only for an internal draft candidate')
            old = output.read_text(encoding='utf-8-sig')
            if (old.count('<!-- diff-list-status: draft -->') != 1 or
                    old.count('报告状态：未完成') != 1):
                raise ValueError('--replace-draft refuses to overwrite a file without one internal draft marker and status')
            handle = tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                prefix=output.name+'.', suffix='.tmp', dir=str(output.parent), delete=False)
            temporary = Path(handle.name)
            with handle:
                handle.write(rendered)
            os.replace(str(temporary), str(output))
        else:
            with output.open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(rendered)
        print('CANDIDATE: {} cases={} output={}'.format(total, counts, output.resolve()))
        return 0
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, IndexError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
