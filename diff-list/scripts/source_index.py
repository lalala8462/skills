"""指定 Git 树上的轻量源码索引，不检出或构建目标项目。"""
import hashlib
import json
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from logic_candidates import jdk_tools

DEFAULT_LIMITS = dict(files=10000, file_bytes=1048576, total_bytes=134217728,
                      depth=4, methods=400, context_bytes=8388608)


def source_lines(text):
    return re.findall(r'[^\r\n]*(?:\r\n|\r|\n|$)',text)[:-1]


def stable(*values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()[:24]


def git_bytes(repo, *args):
    run = subprocess.run(['git', '-c', 'core.longpaths=true', '-C', str(repo), *args],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if run.returncode:
        raise ValueError('Git source read failed: ' + run.stderr.decode('utf-8', 'replace')[:300])
    return run.stdout


def batch_blobs(repo, oids):
    if not oids:
        return {}
    process = subprocess.run(['git','-C',str(repo),'cat-file','--batch'],
                             input=('\n'.join(oids)+'\n').encode('ascii'),
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if process.returncode:
        raise ValueError('Git batch blob read failed')
    output, position, result = process.stdout, 0, {}
    for oid in oids:
        end = output.find(b'\n',position)
        header=output[position:end].decode('ascii').split()
        if len(header)!=3 or header[1]!='blob':
            raise ValueError('Missing Git blob: '+oid)
        size=int(header[2]); position=end+1
        result[oid]=output[position:position+size]; position+=size+1
    return result


def list_sources(repo, tree):
    records = git_bytes(repo, 'ls-tree', '-r', '-z', tree).split(b'\0')
    result = []
    for record in records:
        if not record:
            continue
        meta, path = record.split(b'\t', 1)
        mode, kind, oid = meta.decode('ascii').split()
        name = path.decode('utf-8')
        if kind == 'blob' and mode != '120000' and name.lower().endswith(('.java', '.xml')):
            result.append((name, oid))
    return sorted(result)


class JavaParser:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix='diff-list-index-')
        self.root = Path(self.temp.name)
        javac, self.java, tools = jdk_tools()
        import os
        self.classpath = os.pathsep.join([str(self.root), tools] if tools else [str(self.root)])
        source = Path(__file__).parent / 'java' / 'JavaSourceIndex.java'
        run = subprocess.run([javac, '-source', '8', '-target', '8', '-encoding', 'UTF-8',
                              '-cp', self.classpath, '-d', str(self.root), str(source)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if run.returncode:
            raise ValueError('Source index compiler failed: ' + run.stderr.decode('utf-8', 'replace')[:1200])

    def close(self):
        self.temp.cleanup()

    def parse(self, sources):
        if not sources:
            return {}
        names = {}
        for oid, source in sorted(sources.items()):
            path = self.root / (oid + '.java')
            path.write_bytes(source.encode('utf-8'))
            names[str(path)] = oid
        manifest = self.root / 'files.txt'
        manifest.write_text('\n'.join(names), encoding='utf-8')
        run = subprocess.run([self.java, '-Xmx768m', '-cp', self.classpath, 'JavaSourceIndex', str(manifest)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if run.returncode:
            raise ValueError('Source parser failed: ' + run.stderr.decode('utf-8', 'replace')[:800])
        result, active = {}, None
        for line in run.stdout.decode('utf-8').splitlines():
            row = json.loads(line)
            if row['kind'] == 'file':
                active = names[row['file']]
                result[active] = dict(events=[], error='Java syntax error' if row['parse_error'] else None)
            else:
                result[active]['events'].append(row)
        for oid in sources:
            result.setdefault(oid, dict(events=[], error='parser did not return file'))
        return result


def mapper_events(source):
    # 不加载外部实体；动态标签原样保留为条件边界。
    if '<!ENTITY' in source.upper():
        raise ValueError('XML entities are unsupported')
    root = ET.fromstring(source)
    if root.tag != 'mapper':
        return []
    owner = root.attrib.get('namespace', '')
    result = []
    for element in root:
        if element.tag not in ('select', 'insert', 'update', 'delete', 'resultMap', 'sql'):
            continue
        identifier = element.attrib.get('id', '')
        text = ET.tostring(element, encoding='unicode')
        start = source.find('<' + element.tag)
        if identifier:
            match = re.search(r'<'+element.tag+r'\b[^>]*\bid=["\']'+re.escape(identifier)+r'["\']', source)
            if match:
                start = match.start()
        line = source[:max(0, start)].count('\n') + 1
        result.append(dict(kind='mapper', owner=owner, method=owner+'#'+identifier,
                           name=identifier, statement=element.tag, line=line,
                           end_line=line+text.count('\n'), source=text,
                           dynamic=[dict(tag=e.tag, test=e.attrib.get('test', ''))
                                    for e in element.iter() if e.tag in ('if', 'choose', 'when', 'foreach', 'include')],
                           mappings=[dict(e.attrib) for e in element.iter() if e.tag in ('id', 'result', 'association', 'collection')]))
    return result


def build_index(repo, tree, limits, parser, cache):
    files, issues, new_sources, total = [], [], {}, 0
    entries = list_sources(repo, tree)
    oids = sorted(set(oid for _,oid in entries))
    check = subprocess.run(['git','-C',str(repo),'cat-file','--batch-check'],
                           input=('\n'.join(oids)+'\n').encode('ascii'),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if check.returncode:
        raise ValueError('Cannot inspect snapshot blob sizes')
    sizes={}
    for oid,line in zip(oids,check.stdout.decode('ascii').splitlines()):
        parts=line.split()
        sizes[oid]=int(parts[2]) if len(parts)==3 and parts[1]=='blob' else None
    selected=[]
    for position, (path, oid) in enumerate(entries):
        if position >= limits['files']:
            issues.append(dict(path=path, reason='file_count_limit'))
            continue
        try:
            size = sizes.get(oid)
            if size is None:
                raise ValueError('missing source blob')
            if size > limits['file_bytes'] or total + size > limits['total_bytes']:
                issues.append(dict(path=path, reason='file_size_limit' if size > limits['file_bytes'] else 'total_size_limit'))
                continue
            total += size
            selected.append((path,oid,size))
        except ValueError as exc:
            issues.append(dict(path=path,reason=str(exc)))
    blobs=batch_blobs(repo,sorted(set(oid for _,oid,_ in selected)))
    for path,oid,size in selected:
        try:
            source=blobs[oid].decode('utf-8-sig')
        except (ValueError, UnicodeError) as exc:
            issues.append(dict(path=path, reason=str(exc)))
            continue
        files.append(dict(path=path, blob=oid, source=source, size=size))
        if path.lower().endswith('.java') and oid not in cache:
            new_sources[oid] = source
    cache.update(parser.parse(new_sources))
    events = []
    for file in files:
        try:
            if file['path'].lower().endswith('.java'):
                parsed = cache[file['blob']]
                if parsed['error']:
                    raise ValueError(parsed['error'])
                rows = parsed['events']
            else:
                rows = mapper_events(file['source'])
            for row in rows:
                events.append(dict(row, path=file['path'], blob=file['blob']))
        except (ValueError, ET.ParseError) as exc:
            issues.append(dict(path=file['path'], reason=str(exc)))
    return dict(tree=tree, files=files, events=events, issues=issues,
                stats=dict(discovered_files=len(entries), parsed_files=len(files), bytes=total))
