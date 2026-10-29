#!/usr/bin/env python3
"""Export model-reviewed test cases as a five-column XLSX using Python stdlib."""
import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

HEADERS = ('用例编号', '用例名称', '前置条件', '步骤描述', '预期结果')
BAD_XML = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]')


def _string(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + ' must be a nonempty string')
    if BAD_XML.search(value):
        raise ValueError(label + ' contains XML control characters')
    if len(value) > 32767:
        raise ValueError(label + ' exceeds Excel cell limit')
    return value.strip()


def _lines(value, label):
    if isinstance(value, str):
        return _string(value, label)
    if not isinstance(value, list) or not value:
        raise ValueError(label + ' must be a nonempty string or string array')
    items = [_string(item, label) for item in value]
    return '\n'.join('{}. {}'.format(n, item) for n, item in enumerate(items, 1))


def _observable_evidence(value, identifier):
    """Require observable source, target and result without pretending to verify semantics."""
    if not isinstance(value, list) or not value:
        raise ValueError('case {} requires observable_evidence (nonempty array)'.format(identifier))
    assertions = []
    for index, item in enumerate(value, 1):
        if not isinstance(item, dict):
            raise ValueError('case {} observable_evidence[{}] must be an object'.format(identifier, index))
        label = 'case {} observable_evidence[{}]'.format(identifier, index)
        surface = _string(item.get('surface'), label + '.surface')
        target = _string(item.get('target'), label + '.target')
        assertion = _string(item.get('assertion'), label + '.assertion')
        if any('\n' in value or '\r' in value for value in (surface, target, assertion)):
            raise ValueError(label + ' must be single-line')
        simple = re.sub(r'[\s,，。.!！:：;；、]+', '', assertion)
        generic = re.fullmatch(
            r'(?:(?:业务|交易|支付|退款|订单|操作|请求|接口|系统|结果|响应|处理|执行|数据|状态|功能)){0,2}'
            r'(?:成功|正常|正确|无异常|符合预期|与预期一致|通过)', simple)
        if generic or simple in ('符合预期', '处理成功', '结果正确', '功能正常', '正常处理', '无异常'):
            raise ValueError(label + '.assertion is not an observable, verifiable result')
        assertions.append('{}. {}｜{}：{}'.format(index, surface, target, assertion))
    return assertions


def _case(item, ids):
    identifier = _string(item.get('case_id'), 'case_id')
    if '\n' in identifier or identifier in ids:
        raise ValueError('duplicate or multiline case_id: ' + identifier)
    ids.add(identifier)
    title = _string(item.get('title'), 'title')
    preconditions = _string(item.get('preconditions'), 'preconditions')
    steps = _lines(item.get('steps'), 'steps')
    expected = _lines(item.get('expected'), 'expected')
    evidence = _observable_evidence(item.get('observable_evidence'), identifier)
    expected += '\n可观察证据：\n' + '\n'.join(evidence)
    if len(expected) > 32767:
        raise ValueError('case {} expected result exceeds Excel cell limit'.format(identifier))
    return identifier, title, preconditions, steps, expected


def normalize(data):
    """只接受当前四类用例；源码和覆盖校验由交付入口先执行。"""
    from test_points import PURPOSES, case_material, validate_cases
    validate_cases(data)
    ids, groups = set(), {}
    for purpose in PURPOSES:
        rows = []
        for item in sorted(data['purpose_cases'][purpose], key=lambda c: c.get('case_id', '')):
            material, assertions = case_material(data, item)
            expanded = dict(item, preconditions=_string(item.get('preconditions'), 'preconditions') + '\n' + material,
                            expected=_lines(item.get('expected'), 'expected') + '\n' + assertions)
            rows.append(_case(expanded, ids))
        groups[purpose] = rows
    if not any(groups.values()):
        raise ValueError('no test cases to export')
    if sum(map(len, groups.values())) > 10000:
        raise ValueError('too many test cases')
    return {'purposes': groups}


def _cell(column, row, value, style):
    # inlineStr is always text, so input starting with = is never an Excel formula.
    return '<c r="{}{}" s="{}" t="inlineStr"><is><t xml:space="preserve">{}</t></is></c>'.format(
        column, row, style, escape(value, {'"': '&quot;', "'": '&apos;'}))


def _sheet(rows):
    items = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
             '<dimension ref="A1:E{}"/>'.format(len(rows) + 1),
             '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>',
             '<sheetFormatPr defaultRowHeight="18"/>',
             '<cols><col min="1" max="1" width="15" customWidth="1"/>',
             '<col min="2" max="5" width="40" customWidth="1"/></cols>',
             '<sheetData>']
    for n, values in enumerate((HEADERS,) + tuple(rows), 1):
        max_lines = max(value.count('\n') + 1 for value in values)
        height = 24 if n == 1 else min(240, max(32, 18 * max_lines + 10))
        items.append('<row r="{}" ht="{}" customHeight="1">'.format(n, height))
        for col, value in zip('ABCDE', values):
            items.append(_cell(col, n, value, 1 if n == 1 else 2))
        items.append('</row>')
    items.append('</sheetData><autoFilter ref="A1:E{}"/></worksheet>'.format(len(rows) + 1))
    return ''.join(items).encode('utf-8')


def export(rows, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    from test_points import PURPOSES, PURPOSE_NAMES
    if not isinstance(rows,dict) or set(rows)!={'purposes'} or not isinstance(rows['purposes'],dict) or set(rows['purposes'])!=set(PURPOSES):
        raise ValueError('export requires normalized current four-purpose rows')
    names = PURPOSE_NAMES
    sheets = tuple(rows['purposes'][p] for p in PURPOSES)
    # x mode: never overwrite user files.
    with zipfile.ZipFile(target, 'x', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
''' + ''.join('<Override PartName="/xl/worksheets/sheet{}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'.format(n)
              for n in range(1, len(sheets) + 1)) + '''
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>''')
        archive.writestr('_rels/.rels', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>''')
        archive.writestr('xl/workbook.xml', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>''' +
            ''.join('<sheet name="{}" sheetId="{}" r:id="rId{}"/>'.format(escape(name), n, n)
                    for n, name in enumerate(names, 1)) + '</sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
''' + ''.join('<Relationship Id="rId{}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{}.xml"/>'.format(n, n)
              for n in range(1, len(sheets) + 1)) + '''
<Relationship Id="rId''' + str(len(sheets) + 1) + '''" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>''')
        archive.writestr('xl/styles.xml', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="Microsoft YaHei"/></font><font><b/><sz val="11"/><name val="Microsoft YaHei"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFE7E6E6"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left style="thin"><color rgb="FFD0D0D0"/></left><right style="thin"><color rgb="FFD0D0D0"/></right><top style="thin"><color rgb="FFD0D0D0"/></top><bottom style="thin"><color rgb="FFD0D0D0"/></bottom><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>''')
        for number, sheet_rows in enumerate(sheets, 1):
            archive.writestr('xl/worksheets/sheet{}.xml'.format(number), _sheet(sheet_rows))
    result = {purpose: len(rows['purposes'][purpose]) for purpose in PURPOSES}
    result['total'] = sum(result.values())
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, help='JSON with reviewed test cases')
    parser.add_argument('--output', required=True, help='New .xlsx output path')
    parser.add_argument('--analysis', required=True, help='本次当前版本生成的 analysis.json')
    parser.add_argument('--review', required=True, help='本次源码补查与处置记录')
    args = parser.parse_args(argv)
    try:
        if Path(args.output).suffix.lower() != '.xlsx':
            raise ValueError('output must end with .xlsx')
        from validate_analysis import validate_files
        errors,_=validate_files(args.analysis,args.review,args.input)
        if errors:
            raise ValueError('Coverage validation failed: '+'; '.join(errors[:8]))
        rows = normalize(json.loads(Path(args.input).read_text(encoding='utf-8-sig')))
        count = export(rows, args.output)
        from test_points import PURPOSES, PURPOSE_NAMES
        print('EXPORTED: ' + ', '.join('{}={}'.format(name, count[p]) for p, name in zip(PURPOSES, PURPOSE_NAMES))
              + ', total={} -> {}'.format(count['total'], Path(args.output).resolve()))
        return 0
    except (ValueError, OSError, KeyError, TypeError, IndexError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
