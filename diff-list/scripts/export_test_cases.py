#!/usr/bin/env python3
"""Export model-reviewed test cases as a five-column XLSX using Python stdlib."""
import argparse
import json
import re
import sys
import unicodedata
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

HEADERS = ('用例编号', '用例名称', '前置条件', '步骤描述', '预期结果')
BAD_XML = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]')
SHEETS = ('重点测试用例', '常规功能用例')


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


def _identity(text):
    """Conservative text equality; business equivalence uses explicit coverage keys."""
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())


def _coverage(item, label):
    """Stable model-reviewed coverage identities, independent of case titles."""
    if 'coverage_keys' in item:
        value = item['coverage_keys']
        if not isinstance(value, list) or not value:
            raise ValueError(label + ' coverage_keys must be a nonempty array')
        keys = [_identity(_string(key, label + ' coverage_keys')) for key in value]
    else:
        keys = [_identity(_string(item.get('coverage_key'), label + ' coverage_key'))]
    if len(set(keys)) != len(keys):
        raise ValueError(label + ' repeats a coverage key')
    return set(keys)


def _case(item, number, prefix, ids, require_coverage):
    if not isinstance(item, dict):
        raise ValueError('case {} must be an object'.format(number))
    identifier = _string(item.get('case_id') or '{}{:04d}'.format(prefix, number), 'case_id')
    if '\n' in identifier or identifier in ids:
        raise ValueError('duplicate or multiline case_id: ' + identifier)
    ids.add(identifier)
    title = _string(item.get('title'), 'title')
    preconditions = _string(item.get('preconditions'), 'preconditions')
    steps = _lines(item.get('steps'), 'steps')
    expected = _lines(item.get('expected'), 'expected')
    signature = tuple(_identity(value) for value in (title, preconditions, steps, expected))
    coverage = _coverage(item, 'case ' + identifier) if require_coverage else set()
    return (identifier, title, preconditions, steps, expected), coverage, signature


def normalize(data):
    """Accept historic cases input or two-category input without inventing tests.

    New input: focus_cases + regular_cases. Every new case has coverage_key(s).
    Return dict for dual input; preserve a list for older API callers.
    """
    if not isinstance(data, dict):
        raise ValueError('input must be an object')
    if 'cases' in data:
        if 'focus_cases' in data or 'regular_cases' in data:
            raise ValueError('do not mix cases with focus_cases/regular_cases')
        if not isinstance(data['cases'], list) or not data['cases']:
            raise ValueError('input must contain a nonempty cases array')
        if len(data['cases']) > 10000:
            raise ValueError('too many test cases')
        ids = set()
        return [_case(item, n, 'DL', ids, False)[0]
                for n, item in enumerate(data['cases'], 1)]

    focus, regular = data.get('focus_cases'), data.get('regular_cases')
    if not isinstance(focus, list) or not isinstance(regular, list):
        raise ValueError('new input requires focus_cases and regular_cases arrays')
    if not focus and not regular:
        raise ValueError('no test cases to export')
    if len(focus) + len(regular) > 10000:
        raise ValueError('too many test cases')

    ids, focus_keys, regular_keys = set(), set(), set()
    focus_rows, regular_rows = [], []
    focus_signatures, regular_signatures = set(), set()
    for number, item in enumerate(focus, 1):
        row, keys, signature = _case(item, number, 'DL-F', ids, True)
        if keys & focus_keys or signature in focus_signatures:
            raise ValueError('duplicate key or scenario inside focus_cases: ' + row[0])
        focus_keys.update(keys)
        focus_signatures.add(signature)
        focus_rows.append(row)

    deduplicated = 0
    for number, item in enumerate(regular, 1):
        row, keys, signature = _case(item, len(regular_rows)+1, 'DL-N', ids, True)
        overlap = keys & focus_keys
        if keys <= focus_keys or signature in focus_signatures:
            ids.discard(row[0])
            deduplicated += 1
            continue
        if overlap:
            raise ValueError('regular case {} partially overlaps focus coverage; split the case'.format(row[0]))
        if keys & regular_keys or signature in regular_signatures:
            raise ValueError('duplicate key or scenario inside regular_cases: ' + row[0])
        regular_keys.update(keys)
        regular_signatures.add(signature)
        regular_rows.append(row)
    return {'focus': focus_rows, 'regular': regular_rows, 'deduplicated': deduplicated}

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
    legacy = isinstance(rows, list)
    if legacy:
        sheets = (rows, [])
        removed = 0
    else:
        sheets = (rows['focus'], rows['regular'])
        removed = rows['deduplicated']
    # x mode: never overwrite user files.
    with zipfile.ZipFile(target, 'x', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>''')
        archive.writestr('_rels/.rels', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>''')
        archive.writestr('xl/workbook.xml', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="重点测试用例" sheetId="1" r:id="rId1"/><sheet name="常规功能用例" sheetId="2" r:id="rId2"/></sheets></workbook>''')
        archive.writestr('xl/_rels/workbook.xml.rels', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
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
    if legacy:
        return len(sheets[0])
    return {'focus': len(sheets[0]), 'regular': len(sheets[1]),
            'total': len(sheets[0])+len(sheets[1]), 'deduplicated': removed}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, help='JSON with reviewed test cases')
    parser.add_argument('--output', required=True, help='New .xlsx output path')
    args = parser.parse_args(argv)
    try:
        if Path(args.output).suffix.lower() != '.xlsx':
            raise ValueError('output must end with .xlsx')
        rows = normalize(json.loads(Path(args.input).read_text(encoding='utf-8-sig')))
        count = export(rows, args.output)
        if isinstance(count, dict):
            print('EXPORTED: focus={focus}, regular={regular}, total={total}, duplicate regular removed={deduplicated} -> {path}'.format(path=Path(args.output).resolve(), **count))
        else:
            print('EXPORTED: {} cases -> {}'.format(count, Path(args.output).resolve()))
        return 0
    except (ValueError, OSError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())