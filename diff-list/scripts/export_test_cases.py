#!/usr/bin/env python3
"""Export model-reviewed test cases as a five-column XLSX using Python stdlib."""
import argparse
import json
import posixpath
import re
import sys
import unicodedata
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
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
    if not isinstance(value, list) or not value:
        raise ValueError(label + ' must be a nonempty string array')
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
        # 只识别没有具体值或可判定条件的空泛结论，不依赖支付领域的词表。
        generic = re.fullmatch(
            r'[\u4e00-\u9fff]{0,12}(?:成功|正常|正确|无异常|符合预期|与预期一致|通过)', simple)
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


def normalize(data, analysis, review):
    """只接受当前四类用例；源码和覆盖校验由交付入口先执行。"""
    from case_contract import validate_cases
    validate_cases(data, analysis, review)
    return _normalize_validated(data)


def _normalize_validated(data):
    """仅供同一调用中已校验的对象使用，不缓存跨调用的校验结果。"""
    from case_contract import PURPOSES
    ids, groups = set(), {}
    for purpose in PURPOSES:
        rows = []
        for item in sorted(data['purpose_cases'][purpose], key=lambda c: c.get('case_id', '')):
            rows.append(_case(item, ids))
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
        def display_lines(value, width):
            total = 0
            for line in value.split('\n'):
                length = sum(2 if unicodedata.east_asian_width(ch) in ('W','F') else 1 for ch in line)
                total += max(1, (length + width - 1) // width)
            return total
        max_lines = max(display_lines(value, 13 if index == 0 else 38) for index, value in enumerate(values))
        height = 24 if n == 1 else min(409, max(32, 18 * max_lines + 10))
        items.append('<row r="{}" ht="{}" customHeight="1">'.format(n, height))
        for col, value in zip('ABCDE', values):
            items.append(_cell(col, n, value, 1 if n == 1 else 2))
        items.append('</row>')
    items.append('</sheetData><autoFilter ref="A1:E{}"/></worksheet>'.format(len(rows) + 1))
    return ''.join(items).encode('utf-8')


def _template_sheet(xml_text, rows, shared_strings):
    """Keep the template sheet layout/styles and replace only header/data rows."""
    main_ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    root = ET.fromstring(xml_text)
    if root.tag != '{%s}worksheet' % main_ns:
        raise ValueError('template worksheet uses an unsupported XML namespace')
    if not re.search(r'<worksheet\b[^>]*\bxmlns=["\']' + re.escape(main_ns) + r'["\']', xml_text):
        raise ValueError('template worksheet must declare the standard namespace as its default namespace')
    sheet_data = root.find('{%s}sheetData' % main_ns)
    if sheet_data is None:
        raise ValueError('template worksheet is missing sheetData')
    template_rows = {row.get('r'): row for row in sheet_data.findall('{%s}row' % main_ns)}
    header = template_rows.get('1')
    if header is None:
        raise ValueError('template worksheet is missing header row 1')
    header_cells = {cell.get('r'): cell for cell in header.findall('{%s}c' % main_ns)}
    header_styles = {}
    data_styles = {}
    data_row = template_rows.get('2')
    data_cells = {cell.get('r'): cell for cell in data_row.findall('{%s}c' % main_ns)} if data_row is not None else {}
    for column, expected in zip('ABCDE', HEADERS):
        cell = header_cells.get(column + '1')
        if cell is None:
            raise ValueError('template worksheet header is missing cell ' + column + '1')
        value = ''.join(node.text or '' for node in cell.findall('.//{%s}t' % main_ns))
        if cell.get('t') == 's':
            try:
                shared_index = int(cell.find('{%s}v' % main_ns).text)
                if shared_index < 0 or shared_index >= len(shared_strings):
                    raise ValueError('shared-string index is out of range')
                value = shared_strings[shared_index]
            except (AttributeError, TypeError, ValueError, IndexError):
                raise ValueError('template worksheet has an invalid shared-string header')
        if value != expected:
            raise ValueError('template worksheet header mismatch at ' + column + '1')
        header_styles[column] = cell.get('s', '0')
        data_cell = data_cells.get(column + '2')
        data_styles[column] = data_cell.get('s', '0') if data_cell is not None else '0'

    row_height = header.get('ht', '24')
    widths = [15, 40, 40, 40, 40]
    generated = []
    all_rows = [HEADERS] + rows
    for row_number, values in enumerate(all_rows, 1):
        if row_number == 1:
            height = row_height
        else:
            line_count = 1
            for value, width in zip(values, widths):
                display_width = 13 if width == 15 else 38
                for line in value.split('\n'):
                    visible = sum(2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1 for ch in line)
                    line_count = max(line_count, (visible + display_width - 1) // display_width)
            height = str(min(409, max(32, 18 * line_count + 10)))
        generated.append('<row r="{}" ht="{}" customHeight="1">'.format(row_number, escape(str(height))))
        for column, value in zip('ABCDE', values):
            style = header_styles[column] if row_number == 1 else data_styles[column]
            generated.append(_cell(column, row_number, value, style))
        generated.append('</row>')
    sheet_data_xml = '<sheetData>' + ''.join(generated) + '</sheetData>'
    pattern = re.compile(r'<(?:[A-Za-z_][\w.-]*:)?sheetData\b[^>]*>.*?</(?:[A-Za-z_][\w.-]*:)?sheetData>', re.S)
    updated, count = pattern.subn(sheet_data_xml, xml_text, count=1)
    if count != 1:
        raise ValueError('template worksheet sheetData could not be replaced safely')
    dimension = 'A1:E{}'.format(len(all_rows))
    updated, _ = re.subn(r'(<dimension\b[^>]*\bref=)(["\'])[^"\']*\2',
                         lambda match: match.group(1) + match.group(2) + dimension + match.group(2),
                         updated, count=1)
    updated, _ = re.subn(r'(<autoFilter\b[^>]*\bref=)(["\'])[^"\']*\2',
                         lambda match: match.group(1) + match.group(2) + dimension + match.group(2),
                         updated, count=1)
    return updated.encode('utf-8')


def _export_template(rows, target, template, names, purposes):
    if target.resolve() == template.resolve():
        raise ValueError('output workbook cannot overwrite its template')
    main_ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    document_rel_ns = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    package_rel_ns = 'http://schemas.openxmlformats.org/package/2006/relationships'
    with zipfile.ZipFile(template, 'r') as source:
        workbook = ET.fromstring(source.read('xl/workbook.xml'))
        relationships = ET.fromstring(source.read('xl/_rels/workbook.xml.rels'))
        targets = {relation.get('Id'): relation.get('Target')
                   for relation in relationships.findall('{%s}Relationship' % package_rel_ns)}
        sheets = {}
        order = []
        for sheet in workbook.findall('.//{%s}sheet' % main_ns):
            name = sheet.get('name')
            target_id = sheet.get('{%s}id' % document_rel_ns)
            relative = targets.get(target_id)
            if not name or not relative:
                raise ValueError('template workbook has an unresolved worksheet relationship')
            sheet_path = relative.lstrip('/') if relative.startswith('/') else posixpath.normpath(posixpath.join('xl', relative))
            sheets[name] = sheet_path
            order.append(name)
        if order != list(names):
            raise ValueError('template workbook must contain exactly the four current sheets in their standard order')
        shared_strings = []
        if 'xl/sharedStrings.xml' in source.namelist():
            shared = ET.fromstring(source.read('xl/sharedStrings.xml'))
            shared_strings = [''.join(node.text or '' for node in item.findall('.//{%s}t' % main_ns))
                              for item in shared.findall('{%s}si' % main_ns)]
        sheet_content = {}
        for name, purpose in zip(names, purposes):
            sheet_path = sheets[name]
            original = source.read(sheet_path).decode('utf-8')
            sheet_content[sheet_path] = _template_sheet(original, rows['purposes'][purpose], shared_strings)
        with zipfile.ZipFile(target, 'x', zipfile.ZIP_DEFLATED) as output:
            for item in source.infolist():
                output.writestr(item, sheet_content.get(item.filename, source.read(item.filename)))


def export(rows, target, template=None):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    from case_contract import PURPOSES, PURPOSE_NAMES
    if not isinstance(rows,dict) or set(rows)!={'purposes'} or not isinstance(rows['purposes'],dict) or set(rows['purposes'])!=set(PURPOSES):
        raise ValueError('export requires normalized current four-purpose rows')
    names = PURPOSE_NAMES
    sheets = tuple(rows['purposes'][p] for p in PURPOSES)
    if template:
        _export_template(rows, target, Path(template), names, PURPOSES)
        result = {purpose: len(rows['purposes'][purpose]) for purpose in PURPOSES}
        result['total'] = sum(result.values())
        return result
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
    parser.add_argument('--template', help='可选的既有四表 Excel 模板；保留模板工作表、列宽和单元格样式')
    args = parser.parse_args(argv)
    try:
        if Path(args.output).suffix.lower() != '.xlsx':
            raise ValueError('output must end with .xlsx')
        cases=json.loads(Path(args.input).read_text(encoding='utf-8-sig'))
        from scope_validation import validate_scope_files
        validate_scope_files(args.analysis,args.review,args.input)
        from validate_analysis import validate_files
        errors,_=validate_files(args.analysis,args.review,args.input)
        if errors:
            raise ValueError('Static reference validation failed: '+'; '.join(errors[:8]))
        rows = _normalize_validated(cases)
        count = export(rows, args.output, args.template)
        from case_contract import PURPOSES, PURPOSE_NAMES
        print('EXPORTED: ' + ', '.join('{}={}'.format(name, count[p]) for p, name in zip(PURPOSES, PURPOSE_NAMES))
              + ', total={} -> {}'.format(count['total'], Path(args.output).resolve()))
        return 0
    except (ValueError, OSError, KeyError, TypeError, IndexError, json.JSONDecodeError,
            zipfile.BadZipFile, ET.ParseError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
