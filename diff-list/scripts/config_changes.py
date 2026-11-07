#!/usr/bin/env python3
"""Deterministic configuration-key change extraction."""
import re
import xml.etree.ElementTree as ET

SUPPORTED_EXTENSIONS = ('.properties', '.yaml', '.yml', '.xml')


class NoConfigMapping(ValueError):
    """XML 合法但不包含当前可识别的配置键值映射。"""


def _likely_runtime_yaml(path):
    """Return whether a YAML file is likely application/runtime configuration.

    Generic YAML such as CI workflow files is still visible in the Git diff, but
    it is not a target of this scalar configuration parser and should not by
    itself force the whole analysis into review_required.
    """
    normalized = path.replace('\\\\', '/').lower()
    parts = [part for part in normalized.split('/') if part]
    name = parts[-1] if parts else normalized
    if name.startswith(('application.', 'application-', 'bootstrap.', 'bootstrap-')):
        return True
    if any(part in ('config', 'profile', 'resources') for part in parts[:-1]):
        return True
    return False
SENSITIVE = re.compile(r'(password|secret|token|api.?key|private.?key|credential|cert|sign)', re.I)


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
        raise NoConfigMapping('no supported XML key/value mappings')
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


def extract_config_changes(pairs):
    changes, issues = [], []
    for pair in pairs:
        path = pair['path']
        before = after = None
        before_error = after_error = None
        try:
            before = parse(path, pair['before'])
        except NoConfigMapping as exc:
            before_error = exc
        except ValueError as exc:
            before_error = exc
        try:
            after = parse(path, pair['after'])
        except NoConfigMapping as exc:
            after_error = exc
        except ValueError as exc:
            after_error = exc

        lower = path.lower()
        if lower.endswith('.xml'):
            if isinstance(before_error,NoConfigMapping) and isinstance(after_error,NoConfigMapping):
                issues.append({'path':path,'reason':'no supported XML key/value mappings',
                               'kind':'not_config_mapping','review_required':False})
                continue
            if isinstance(before_error,NoConfigMapping):
                before,before_error={},None
            if isinstance(after_error,NoConfigMapping):
                after,after_error={},None

        reasons=[]
        if before_error is not None:
            reasons.append('before: '+str(before_error))
        if after_error is not None:
            reasons.append('after: '+str(after_error))
        if reasons:
            review_required=True
            kind='parse_failure'
            if lower.endswith(('.yaml','.yml')) and not _likely_runtime_yaml(path):
                review_required=False
                kind='non_runtime_yaml'
            issues.append({'path':path,'reason':'; '.join(reasons),'kind':kind,
                           'review_required':review_required})
            continue
        for key in sorted(set(before) | set(after)):
            old, new = before.get(key), after.get(key)
            if old == new:
                continue
            operation = 'added' if old is None else ('removed' if new is None else 'modified')
            def show(value):
                if value is None:
                    return None
                if SENSITIVE.search(key) or len(value) > 48 or '\n' in value:
                    return '<redacted>'
                if re.fullmatch(r'(?:true|false|yes|no|on|off|null|\d{1,9})', value, re.I):
                    return value
                return '<value>'
            changes.append({'path': path, 'change': operation, 'key': key,
                            'before': show(old), 'after': show(new)})
    return changes, issues
