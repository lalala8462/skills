#!/usr/bin/env python3
"""校验报告结构、固定源码证据和覆盖声明；不证明测试执行或 Mermaid 渲染。"""
import argparse
import json
import re
import sys
from pathlib import Path, PureWindowsPath

HEAD=re.compile(r'^#{1,6} .+$',re.M)
FENCE=re.compile(r'^```([^\n]*)\n(.*?)^```\s*$',re.M|re.S)

def heading_positions(text):
    """Find Markdown headings outside fenced code blocks, preserving source lines."""
    headings = []
    fence_char, fence_width = None, 0
    for i, line in enumerate(text.splitlines()):
        fence = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if fence:
            marker, tail = fence.groups()
            if fence_char is None:
                fence_char, fence_width = marker[0], len(marker)
                continue
            if marker[0] == fence_char and len(marker) >= fence_width and not tail.strip():
                fence_char, fence_width = None, 0
                continue
        if fence_char is None and HEAD.fullmatch(line):
            headings.append((i, line.strip()))
    return headings


def sections(text, headings):
    lines = text.splitlines()
    located = heading_positions(text)
    return {title: '\n'.join(lines[pos+1:located[i+1][0] if i+1 < len(located) else len(lines)]).strip()
            for i, (pos, title) in enumerate(located)}


def fenced_blocks(text):
    """Return (language, body) for completed backtick/tilde fences."""
    blocks, marker, language, content = [], None, None, []
    for line in text.splitlines():
        m = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if marker is None:
            if m:
                marker = m.group(1)
                language = m.group(2).strip()
                content = []
        elif m and m.group(1)[0] == marker[0] and len(m.group(1)) >= len(marker) and not m.group(2).strip():
            blocks.append((language, '\n'.join(content)))
            marker = None
        else:
            content.append(line)
    return blocks


def validate(text,template,expected):
    errors=[]; warnings=[]
    want=[title for _, title in heading_positions(template)]
    got=[title for _, title in heading_positions(text)]
    if got!=want:
        errors.append('标题及顺序与模板不一致')
    body=sections(text,got)
    for h in want[1:]:
        # Parent headings may legitimately contain only nested subsections.
        pos = want.index(h)
        level = len(h.split(' ', 1)[0])
        if pos + 1 < len(want) and len(want[pos+1].split(' ', 1)[0]) > level:
            continue
        if not body.get(h,'').strip():
            errors.append('章节为空: '+h)
    scope=body.get('## 1. 分析范围','')
    if not re.search(r'\b[0-9a-fA-F]{40}\b',scope):
        errors.append('分析范围缺少完整 40 位 Git SHA')
    for pair in expected:
        if '=' not in pair:
            errors.append('--expect 格式必须为 项目名=40位SHA: '+pair)
            continue
        name,sha=pair.split('=',1)
        if not name.strip() or not re.fullmatch(r'[0-9a-fA-F]{40}',sha):
            errors.append('--expect 需要非空项目名及40位SHA: '+pair)
            continue
        # Require a single project/SHA mapping in the same logical line/table row.
        # Global substring presence could falsely bind A's SHA to project B.
        matching = []
        for line in scope.splitlines():
            textline = line.replace('`','')
            shas = re.findall(r'\b[0-9a-fA-F]{40}\b', textline)
            project = re.search(r'(?<![\w-])'+re.escape(name)+r'(?![\w-])',textline)
            if project and len(shas) == 1 and shas[0].lower() == sha.lower():
                matching.append(line)
        if not matching:
            errors.append('分析范围缺少同一行的项目与修订对应关系: '+pair)

    for h in ('### 2.1 需求改动','### 2.2 表字段 DDL 修改',
              '### 2.3 表配置 DML 准备清单','### 2.4 配置文件调整'):
        if not body.get(h,''):
            errors.append('四类改动结论缺失: '+h)
    if '是否必须新增数据' in text:
        errors.append('禁止使用“是否必须新增数据”列')
    if re.search(r'待确认|待核实|代码依据|证据编号|源码行号',text):
        errors.append('包含禁止的占位或代码证据描述')
    checked = text
    start_tag = '<!-- diff-list-generated-clones:start -->'
    end_tag = '<!-- diff-list-generated-clones:end -->'
    found_block = re.search(re.escape(start_tag) + r'\n(.*?)\n' +
                            re.escape(end_tag) + r'\s*\Z', text, re.S)
    if start_tag in text or end_tag in text:
        if not found_block or text.count(start_tag)!=1 or text.count(end_tag)!=1:
            errors.append('分析目录清单必须完整且位于报告最后')
        else:
            rows = found_block.group(1).splitlines()
            if not rows or rows[0].strip() != '**本次创建的分析仓库目录（请后续手动删除）：**':
                errors.append('分析目录清单缺少手动删除提示')
            if len(rows) < 2:
                errors.append('分析目录清单为空')
            for row in rows[1:]:
                m = re.fullmatch(r'- \x60([^\x60\r\n]+)\x60', row)
                if m is None:
                    errors.append('分析目录清单格式不正确')
                    continue
                p = m.group(1)
                parts = PureWindowsPath(p).parts
                if (not re.fullmatch(r'[A-Za-z]:\\.*', p) or
                        '..' in parts or len(parts) < 4 or
                        [piece.casefold() for piece in parts[-3:-1]] != ['diff-list','runs'] or
                        not parts[-1].startswith('run-')):
                    errors.append('分析目录路径不属于 diff-list 专用目录')
            checked = text[:found_block.start()]
    if re.search(r'[A-Za-z]:\\[^\s\x60|)]+',checked):
        errors.append('文档包含 Windows 本机绝对路径')
    if text.count('```')%2:
        errors.append('代码围栏未闭合')
    flow=body.get('### 3.2 Mermaid 流程图','')
    if body.get('### 3.1 整体流程说明','')!='无' or flow!='无':
        blocks=[content for lang,content in fenced_blocks(flow) if lang.strip().lower()=='mermaid']
        if not blocks:
            errors.append('缺少 Mermaid 代码块')
        elif not all(re.match(r'^\s*(?:flowchart TD|sequenceDiagram)\s*(?:\n|$)',b)
                     for b in blocks):
            errors.append('Mermaid 图必须以 flowchart TD 或 sequenceDiagram 开始')
    export_section=body.get('## 5. 测试用例导出','')
    if export_section != '无':
        if not re.search(r'\S+\.xlsx\b',export_section,re.I):
            errors.append('测试用例导出章节缺少 Excel 文件名')
        counts = {}
        labels=('需求验收用例数量','既有核心流程回归用例数量','关联回归用例数量','业务规则与边界验证用例数量')
        if any(label in export_section for label in ('重点用例数量','常规用例数量')):
            errors.append('测试用例导出章节只接受当前四类数量')
        for label in labels+('总用例数量',):
            found = re.search(re.escape(label) + r'\s*[:：]\s*(0|[1-9]\d*)(?!\d)', export_section)
            if not found:
                errors.append('测试用例导出章节缺少有效' + label)
            else:
                counts[label] = int(found.group(1))
        if len(counts) == len(labels)+1:
            if counts['总用例数量'] < 1:
                errors.append('总用例数量必须大于 0；所有组均无用例时填写无')
            elif sum(counts[label] for label in labels) != counts['总用例数量']:
                errors.append('各类用例数量之和与总用例数量不一致')
        if re.search(r'(?:\*\*)?(?:测试点|测试前提|步骤描述|预期结果)[:：]',export_section):
            errors.append('Excel 测试用例不得重复写入 Markdown 报告')
    special=body.get('## 4. 特殊逻辑与测试关注点','')
    if special and special!='无' and not re.search(r'既有功能影响|历史功能影响',special):
        errors.append('特殊逻辑章节缺少“既有功能影响”说明')
    field_change = (body.get('### 2.1 需求改动','') + '\n' +
                    body.get('### 2.2 表字段 DDL 修改',''))
    if re.search(r'(?:新增|增加|修改|调整|删除|废弃).{0,20}(?:对外报文|请求字段|响应字段|数据库字段|表字段|DTO字段|接口字段|消息字段)',field_change) and special=='无':
        errors.append('字段/接口契约变化时须在特殊逻辑章节说明既有功能影响')
    cfg=body.get('### 2.4 配置文件调整','')
    if cfg and cfg!='无' and not fenced_blocks(cfg):
        errors.append('配置文件有调整但缺少配置片段代码块')
    if re.search(r'(?i)(?:token|secret|password|api[_-]?key)\s*[:=]\s*[^\s<>$#]{12,}',text):
        warnings.append('可能含真实凭据：人工检查敏感信息')
    warnings.append('静态检查不证明 Mermaid 8.8.3 实际渲染、业务语义或 Git 归属正确')
    return errors,warnings


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('report')
    p.add_argument('--template',default=str(Path(__file__).resolve().parents[1]/'assets'/'diff-list-template.md'))
    p.add_argument('--expect',action='append',default=[],help='项目名=40位SHA; repeatable')
    p.add_argument('--analysis',required=True,help='本次当前版本生成的固定取证结果')
    p.add_argument('--review',required=True,help='本次模型源码补查与处置记录')
    p.add_argument('--cases',required=True,help='当前四类用例JSON')
    a=p.parse_args(argv)
    try:
        raw=Path(a.report).read_bytes()
        text=raw.decode('utf-8-sig')
        template=Path(a.template).read_text(encoding='utf-8-sig')
        errors,warnings=validate(text,template,a.expect)
        from validate_analysis import validate_files
        coverage_errors,coverage_warnings=validate_files(a.analysis,a.review,a.cases)
        errors.extend(coverage_errors);warnings.extend(coverage_warnings)
        if not coverage_errors:
            case_data=json.loads(Path(a.cases).read_text(encoding='utf-8-sig'))
            errors.extend(validate_case_counts(text,case_data))
            warnings.append('覆盖声明校验通过，不代表业务测试已执行')
    except (OSError,UnicodeError,ValueError,KeyError,TypeError,IndexError) as exc:
        print('ERROR: '+str(exc),file=sys.stderr)
        return 2
    for msg in errors: print('ERROR: '+msg)
    for msg in warnings: print('NOTE: '+msg)
    print('RESULT: '+('FAIL' if errors else 'PASS'))
    return 1 if errors else 0


def validate_case_counts(text,cases):
    """有用例材料时，报告数量必须与实际导出结果一致。"""
    from test_points import PURPOSES,PURPOSE_NAMES,validate_cases
    validate_cases(cases)
    counts={name+'用例数量':len(cases['purpose_cases'][p]) for p,name in zip(PURPOSES,PURPOSE_NAMES)}
    section=sections(text,[]).get('## 5. 测试用例导出','')
    total=sum(counts.values())
    if total==0:
        return [] if section=='无' else ['没有用例时导出章节必须填写无']
    counts['总用例数量']=total
    errors=[]
    for label,value in counts.items():
        match=re.search(re.escape(label)+r'\s*[:：]\s*(0|[1-9]\d*)(?!\d)',section)
        if not match or int(match.group(1))!=value:
            errors.append('报告数量与用例材料不一致: '+label)
    return errors


if __name__=='__main__':
    sys.exit(main())
