#!/usr/bin/env python3
"""Deterministic configuration-key change extraction."""
import re
import xml.etree.ElementTree as ET

SUPPORTED_EXTENSIONS = ('.properties', '.yaml', '.yml', '.xml')


MAX_CONFIG_FILES=100
MAX_CONFIG_BYTES=300000


def config_name_changes(fields):
    """解析 --name-status -z 的状态、旧路径、新路径，不按换行分割文件名。"""
    renames,kinds={},{}
    position=0
    while position<len(fields):
        status=fields[position]
        position+=1
        kind=status[:1]
        count=2 if kind in ('R','C') else 1
        names=fields[position:position+count]
        if len(names)!=count or kind not in ('A','M','D','R','C','T','U'):
            raise ValueError('invalid Git name-status -z record')
        position+=count
        target=names[-1]
        kinds[target]=kind
        if kind=='R':
            renames[target]=names[0]
    return renames,kinds


def config_source(path,before_bytes,after_bytes):
    """两种 Git 采集路径共用同一配置内容限制和 BOM 处理。"""
    if max(len(before_bytes),len(after_bytes))>MAX_CONFIG_BYTES:
        return None,dict(path=path,reason='file size limit')
    try:
        before=before_bytes.decode('utf-8-sig')
        after=after_bytes.decode('utf-8-sig')
    except UnicodeDecodeError:
        return None,dict(path=path,reason='non-UTF-8 source')
    return dict(path=path,before=before,after=after),None


class NoConfigMapping(ValueError):
    """XML 合法但不包含当前可识别的配置键值映射。"""


SENSITIVE = re.compile(r'(password|secret|token|api.?key|private.?key|credential|cert|sign)', re.I)


def properties(text):
    """读取 Java properties 的续行、转义键和 Unicode 转义。"""
    logical=[]
    current=''
    continuation=False
    for row in text.lstrip('\ufeff').splitlines():
        line=row.lstrip(' \t\f') if continuation else row
        backslashes=len(line)-len(line.rstrip('\\'))
        if backslashes%2:
            current+=line[:-1]
            continuation=True
            continue
        logical.append(current+line)
        current=''
        continuation=False
    if continuation:
        logical.append(current)

    def unescape(value):
        output=[]
        index=0
        mapping={'t':'\t','n':'\n','r':'\r','f':'\f'}
        while index<len(value):
            ch=value[index]
            if ch!='\\':
                output.append(ch)
                index+=1
                continue
            index+=1
            if index>=len(value):
                output.append('\\')
                break
            char=value[index]
            if char=='u':
                code=value[index+1:index+5]
                if len(code)!=4 or any(c not in '0123456789abcdefABCDEF' for c in code):
                    raise ValueError('invalid properties Unicode escape')
                number=int(code,16)
                if 0xD800<=number<=0xDBFF:
                    following=value[index+5:index+11]
                    if not (len(following)==6 and following[:2]==chr(92)+'u' and
                            all(c in '0123456789abcdefABCDEF' for c in following[2:])):
                        raise ValueError('invalid properties Unicode surrogate pair')
                    lower=int(following[2:],16)
                    if not 0xDC00<=lower<=0xDFFF:
                        raise ValueError('invalid properties Unicode surrogate pair')
                    output.append(chr(0x10000+(number-0xD800)*1024+(lower-0xDC00)))
                    index+=11
                elif 0xDC00<=number<=0xDFFF:
                    raise ValueError('invalid properties Unicode surrogate pair')
                else:
                    output.append(chr(number))
                    index+=5
            else:
                output.append(mapping.get(char,char))
                index+=1
        return ''.join(output)

    result={}
    for row in logical:
        line=row.lstrip(' \t\f')
        if not line or line.startswith(('#','!')):
            continue
        slash_count=0
        separator=None
        for index,ch in enumerate(line):
            if ch=='\\':
                slash_count+=1
                continue
            escaped=slash_count%2!=0
            slash_count=0
            if not escaped and (ch in '=:' or ch.isspace()):
                separator=index
                break
        if separator is None:
            key,value=line,''
        else:
            key,value=line[:separator],line[separator:]
            value=value.lstrip(' \t\f')
            if value.startswith(('=',':')):
                value=value[1:].lstrip(' \t\f')
        key=unescape(key)
        value=unescape(value)
        if not key:
            raise ValueError('empty properties key')
        if key in result:
            raise ValueError('duplicate properties key: '+key)
        result[key]=value
    return result

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
