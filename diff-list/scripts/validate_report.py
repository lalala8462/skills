#!/usr/bin/env python3
"""Static report validator: structure and hygiene, not semantic/Mermaid parsing."""
import argparse
import re
import sys
from pathlib import Path

HEAD=re.compile(r'^#{1,6} .+$',re.M)
FENCE=re.compile(r'^```([^\n]*)\n(.*?)^```\s*$',re.M|re.S)
TEST=re.compile(r'(?m)^\s*(?:\d+[.)]\s*)?\*\*测试点[:：]\*\*')
LABELS=('测试点','测试前提','预期结果')


def sections(text, headings):
    lines=text.splitlines()
    positions=[i for i,line in enumerate(lines) if HEAD.fullmatch(line)]
    return {lines[pos].strip(): '\n'.join(lines[pos+1:positions[n+1] if n+1<len(positions) else len(lines)]).strip()
            for n,pos in enumerate(positions)}


def validate(text,template,expected):
    errors=[]; warnings=[]
    want=HEAD.findall(template)
    got=HEAD.findall(text)
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
        if not re.fullmatch(r'[0-9a-fA-F]{40}',sha) or name not in scope or sha.lower() not in scope.lower():
            errors.append('分析范围缺少项目与修订: '+pair)
    for h in ('### 2.1 需求改动','### 2.2 表字段 DDL 修改',
              '### 2.3 表配置 DML 准备清单','### 2.4 配置文件调整'):
        if not body.get(h,''):
            errors.append('四类改动结论缺失: '+h)
    if '是否必须新增数据' in text:
        errors.append('禁止使用“是否必须新增数据”列')
    if re.search(r'待确认|待核实|代码依据|证据编号|源码行号',text):
        errors.append('包含禁止的占位或代码证据描述')
    if re.search(r'[A-Za-z]:\\[^\s`|)]+',text):
        errors.append('文档包含 Windows 本机绝对路径')
    if text.count('```')%2:
        errors.append('代码围栏未闭合')
    flow=body.get('### 3.2 Mermaid 流程图','')
    if body.get('### 3.1 整体流程说明','')!='无' or flow!='无':
        blocks=[content for lang,content in FENCE.findall(flow) if lang.strip().lower()=='mermaid']
        if not blocks:
            errors.append('缺少 Mermaid 代码块')
        elif not all(re.match(r'^\s*(?:flowchart TD|sequenceDiagram)\s*(?:\n|$)',b)
                     for b in blocks):
            errors.append('Mermaid 图必须以 flowchart TD 或 sequenceDiagram 开始')
    tests=body.get('## 5. 重点测试场景','')
    if tests!='无':
        if not TEST.search(tests):
            errors.append('重点测试场景缺少“测试点”标签')
        for label in LABELS:
            if not re.search(r'(?m)^\s*(?:\d+[.)]\s*)?\*\*'+label+r'[:：]\*\*\s*\S',tests):
                errors.append('缺少标签或内容: '+label)
        markers=list(TEST.finditer(tests))
        for i, marker in enumerate(markers):
            group=tests[marker.end():markers[i+1].start() if i+1<len(markers) else len(tests)]
            labels=[]
            for lab in ('测试前提','预期结果'):
                hits=list(re.finditer(r'(?m)^\s*(?:\d+[.)]\s*)?\*\*'+lab+r'[:：]\*\*\s*\S',group))
                if len(hits)!=1:
                    errors.append('第 '+str(i+1)+' 个场景缺少唯一有效的 '+lab)
                labels.extend((m.start(),lab) for m in hits)
            if len(labels)==2 and sorted(labels)[0][1]!='测试前提':
                errors.append('第 '+str(i+1)+' 个场景标签顺序错误')
        counts=[len(re.findall(r'(?m)^\s*(?:\d+[.)]\s*)?\*\*'+lab+r'[:：]\*\*',tests)) for lab in LABELS]
        if len(set(counts))!=1:
            errors.append('测试点/测试前提/预期结果数量不一致')
    cfg=body.get('### 2.4 配置文件调整','')
    if cfg and cfg!='无' and not FENCE.search(cfg):
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
    a=p.parse_args(argv)
    try:
        raw=Path(a.report).read_bytes()
        text=raw.decode('utf-8-sig')
        template=Path(a.template).read_text(encoding='utf-8-sig')
        errors,warnings=validate(text,template,a.expect)
    except (OSError,UnicodeError) as exc:
        print('ERROR: '+str(exc),file=sys.stderr)
        return 2
    for msg in errors: print('ERROR: '+msg)
    for msg in warnings: print('NOTE: '+msg)
    print('RESULT: '+('FAIL' if errors else 'PASS'))
    return 1 if errors else 0


if __name__=='__main__':
    sys.exit(main())
