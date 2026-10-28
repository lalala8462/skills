#!/usr/bin/env python3
"""Deterministic config-key diff candidates. No runtime overrides are inferred."""
import hashlib
import re
import xml.etree.ElementTree as ET

SUPPORTED_EXTENSIONS = ('.properties', '.yaml', '.yml', '.xml')
SENSITIVE = re.compile(r'(password|secret|token|api.?key|private.?key|credential|cert|sign)', re.I)
HIGH_RISK = re.compile(r'(pay|refund|trade|amount|money|retry|timeout|lock|switch|enable|callback|idempot|route|transaction|channel)', re.I)


def properties(text):
    data = {}
    lines = text.splitlines()
    logical = []
    current = ''
    for line in lines:
        current += line.lstrip() if current else line
        if (len(current) - len(current.rstrip('\\'))) % 2:
            current = current[:-1]
            continue
        logical.append(current)
        current = ''
    if current:
        logical.append(current)
    for line in logical:
        line = line.strip()
        if not line or line.startswith(('#', '!')):
            continue
        # Java properties delimiter: first unescaped =, :, or whitespace.
        delimiter = re.search(r'(?<!\\)(?:[=:]|\s+)', line)
        if delimiter:
            name, value = line[:delimiter.start()], line[delimiter.end():].strip()
            value = value.lstrip('=:').strip()
        else:
            name, value = line, ''
        name = name.replace('\\.', '.').replace('\\:', ':').replace('\\=', '=')
        if not name:
            raise ValueError('empty properties key')
        if name in data:
            raise ValueError('duplicate properties key: ' + name)
        data[name] = value
    return data


def yaml_scalars(text):
    """Parse ordinary nested scalar YAML keys; reject unsupported constructs safely."""
    result, stack = {}, []
    for index, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith('#') or raw.strip() in ('---', '...'):
            continue
        if '\t' in raw[:len(raw)-len(raw.lstrip())]:
            raise ValueError('YAML tabs are unsupported at line ' + str(index))
        indent = len(raw) - len(raw.lstrip(' '))
        stripped = raw.strip()
        if stripped.startswith(('-', '{', '[', '&', '*', '!', '?')) or stripped.endswith(('|', '>')):
            raise ValueError('complex YAML needs manual review at line ' + str(index))
        m = re.fullmatch(r'([\w.-]+)\s*:\s*(.*)', stripped)
        if not m:
            raise ValueError('unsupported YAML syntax at line ' + str(index))
        key, value = m.groups()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        path = '.'.join([v for _, v in stack] + [key])
        if value == '' or value.startswith('#'):
            stack.append((indent, key))
            continue
        # Literal comment stripping (quotes retained); quoted # remains part of value.
        if not (value.startswith('"') or value.startswith("'")):
            value = re.split(r'\s+#', value, maxsplit=1)[0].strip()
        if path in result:
            raise ValueError('duplicate YAML key: ' + path)
        result[path] = value
    return result


def xml_entries(text):
    if not text.strip():
        return {}
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError('invalid XML: ' + str(exc)) from exc
    result = {}
    for el in root.iter():
        tag = el.tag.split('}')[-1]
        if tag == 'property' and 'name' in el.attrib:
            name = el.attrib['name']
        elif tag == 'entry' and 'key' in el.attrib:
            name = el.attrib['key']
        else:
            continue
        value = el.attrib.get('value')
        if value is None:
            child = next((x for x in el if x.tag.split('}')[-1] == 'value'), None)
            if child is not None and not list(child):
                value = child.text or ''
        if value is None:
            raise ValueError('complex XML property ' + name + ' needs review')
        if name in result:
            raise ValueError('duplicate XML property: ' + name)
        result[name] = value.strip()
    if not result:
        raise ValueError('no supported XML key/value mappings')
    return result


def parse(path, content):
    suffix = path.lower()
    if suffix.endswith('.properties'):
        return properties(content)
    if suffix.endswith(('.yaml', '.yml')):
        return yaml_scalars(content)
    if suffix.endswith('.xml'):
        return xml_entries(content)
    raise ValueError('unsupported config format')


def extract_config_candidates(pairs):
    findings, skipped = [], []
    for pair in pairs:
        path = pair['path']
        try:
            before = parse(path, pair['before'])
            after = parse(path, pair['after'])
        except ValueError as exc:
            skipped.append({'path': path, 'reason': str(exc)[:240]})
            continue
        for key in sorted(set(before) | set(after)):
            old, new = before.get(key), after.get(key)
            if old == new:
                continue
            operation = 'added' if old is None else ('removed' if new is None else 'modified')
            score = 85 if HIGH_RISK.search(key) else 65
            def show(value):
                if value is None:
                    return None
                if SENSITIVE.search(key) or len(value) > 48 or '\n' in value:
                    return '<redacted>'
                if re.fullmatch(r'(?:true|false|yes|no|on|off|null|\d{1,9})', value, re.I):
                    return value
                return '<value>'
            findings.append({'path': path, 'method': '<config>', 'category': 'CONFIG',
                             'change': operation, 'description': 'config key ' + key,
                             'key': key, 'before': show(old), 'after': show(new),
                             'priority': 'high' if score >= 75 else 'medium',
                             'priority_score': score,
                             'risk_hint': '核对配置作用域、覆盖顺序、默认值及对应业务分支',
                             'actual_behavior_verified': False, 'binding_verified': False})
    return findings, skipped
