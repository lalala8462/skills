"""有界源码关联与覆盖义务；静态事实不等于运行验证。"""
import collections
import copy
import json
import re
from functools import lru_cache
from source_index import stable, source_lines

VERSION = '1.8.2'
COMMON_RULES = dict(version=1, config_annotations=['Value', 'ConfigurationProperties'],
                    config_scopes={}, aliases=['JSONField', 'JsonProperty'],
                    relation_review_tokens=[], limits={})
STANDARD_TYPES = set('String Object Boolean Integer Long Double Float Short Byte Character Math System '
                     'List ArrayList LinkedList Map HashMap Set HashSet Collection Collections Arrays '
                     'Objects Optional Comparator BigDecimal StringUtils CollectionUtils Assert Date '
                     'JSON JSONObject JSONArray Logger Slf4j ThreadLocal Stream Function Pair'.split())


def compact(text):
    return re.sub(r'"(?:\\.|[^"\\])*"|\s+', lambda m: m[0] if m[0].startswith('"') else '', text or '')


def test_source(path):
    return '/src/test/' in '/'+(path or '').replace('\\','/')


def standalone_diagnostic_log(site):
    if site.get('usage')!='EXPRESSION_STATEMENT' or site.get('name','').casefold() not in (
            'trace','debug','info','warn','error'):
        return False
    receiver=(site.get('receiver') or '').strip().rsplit('.',1)[-1].casefold()
    return receiver in ('log','logger')


def relation_receiver_hint(site):
    """只保留简单接收者，避免把复杂链式调用的参数文本当成接收者名称。"""
    receiver=(site.get('receiver') or '').strip()
    created=re.match(r'^new\s+([\w.$]+)',receiver)
    if created:return created[1].casefold()
    builder=re.match(r'^([\w.$]+)\.builder\s*\(',receiver,re.I)
    if builder:return builder[1].casefold()
    if re.fullmatch(r'(?:this|super|[\w$]+(?:\.[\w$]+)*)',receiver):
        return receiver.casefold()
    return ''


def relation_affects_review_scope(edge, graph, custom_tokens=()):
    """只把可能触及关键业务结果的未解析调用放入人工复核。"""
    site=edge.get('site') or {}
    name=(site.get('name') or '').casefold()
    receiver=' '.join((edge.get('receiver_type') or '',relation_receiver_hint(site))).casefold()
    source=edge.get('source') or ''
    target_surface=' '.join((receiver,name)).casefold()
    caller=source.casefold()
    caller_method=source.split('#')[-1].split('(',1)[0].casefold()
    method_rows=getattr(graph,'methods',{})
    method_meta=method_rows.get(source,{}) if isinstance(method_rows,dict) else {}
    class_rows=getattr(graph,'classes',{})
    owner_meta=class_rows.get(method_meta.get('owner'),{}) if isinstance(class_rows,dict) else {}
    annotation_rows=(method_meta.get('annotations',[]) or [])+(owner_meta.get('annotations',[]) or [])
    annotations=[a.get('name','').rsplit('.',1)[-1].casefold() for a in annotation_rows]
    endpoint_annotation=any(a in ('restcontroller','controller','requestmapping','getmapping',
        'postmapping','putmapping','patchmapping','deletemapping') for a in annotations)
    return_type=(method_meta.get('return_type') or '').rsplit('.',1)[-1].casefold()
    response_return=any(token in return_type for token in ('response','reply','result','bizresult'))
    business_return=any(token in (method_meta.get('owner','')+' '+caller_method).casefold()
                        for token in ('refund','payment','pay','notify','notification','callback'))
    critical_return_context=(endpoint_annotation or 'controller' in method_meta.get('owner','').casefold()
                             or response_return or business_return)
    scopes=('mapper','dao','repository','jdbc','database','recordindex','store',
            'channel','proxy','acquirer','qcm','qrcode','notification','notify','callback',
            'uniquecontrol','fgtradeno','outtradeno','orderno','billno','transtype','mchid','entnum')
    sink_context=any(token in target_surface for token in scopes)
    custom_target=any(token.casefold() in target_surface for token in custom_tokens)
    raw_receiver=site.get('receiver') or ''
    builder_chain=(name in ('builder','newbuilder','tobuilder') or
                   'builder' in (edge.get('receiver_type') or '').casefold() or
                   bool(re.search(r'\.(?:new)?builder\s*\(',raw_receiver,re.I)))
    # Builder 属性写入由 field_source 事实覆盖；不再逐个复核 Lombok/DTO 构造器绑定。
    if builder_chain and name not in ('build',):
        return False
    def critical_sink(candidate):
        candidate_site=candidate.get('site') or {}
        candidate_name=(candidate_site.get('name') or '').casefold()
        candidate_target=' '.join((candidate.get('receiver_type') or '',
                                   relation_receiver_hint(candidate_site),candidate_name)).casefold()
        candidate_builder=(candidate_name in ('builder','newbuilder','tobuilder') or
            'builder' in (candidate.get('receiver_type') or '').casefold() or
            bool(re.search(r'\.(?:new)?builder\s*\(',candidate_site.get('receiver') or '',re.I)))
        candidate_db=any(token in candidate_target for token in ('mapper','dao','repository','jdbc','database','recordindex','store'))
        if candidate_builder and candidate_name!='build':return False
        if candidate_name.startswith('set'):
            return candidate_db and any(token in candidate_name[3:] for token in ('refund','status','state'))
        if candidate_name=='<init>':
            return any(token in candidate_target for token in ('bizexception','exception','bizresult','response','resp','reply','errorcode'))
        if 'bizresult' in candidate_target and candidate_name in ('success','fail','error','ok'):
            return True
        if candidate_name.startswith(('get','is','has','equals','hashcode','tostring','contains')):
            return (candidate_db and candidate_name.startswith(('getby','find','query','select','count','exists','list','load','read')) or
                    any(token.casefold() in candidate_target for token in custom_tokens) and
                    any(token.casefold() in candidate_target for token in ('uniquecontrolapi','fgqrcodehelper')) or
                    any(token in candidate_name for token in ('refundstatus','refundstate','channelrespdesc','channelrefundno','channelparam')))
        return (any(token in candidate_target for token in scopes) or
                any(token.casefold() in candidate_target for token in custom_tokens) or
                any(token in candidate_name for token in ('refund','refundable','transstate','paystate','status','state')) or
                candidate_name.startswith(('insert','update','delete','save','persist','execute','batch',
                    'commit','rollback','register','send','publish','pay','refund','close','cancel',
                    'notice','notify','write','remove','mark')))
    if name.startswith('log') or 'logger' in receiver:
        return False
    access_method=(name.startswith(('get','is','has','equals','hashcode','tostring','contains')) or
                   name in ('getvalue','getcode','getdata','getinfo','getmessage','getmsg'))
    direct_return=site.get('usage')=='RETURN'
    if direct_return and not access_method and name!='<init>' and not builder_chain:
        return sink_context or custom_target or critical_return_context
    # 未解析调用的返回值若沿局部赋值进入方法返回，属于最终响应来源。
    call_origins=set(graph.origins(site.get('expression',''),edge['source'],site.get('order',0)))
    returned_flow=False
    state_branch=False
    sink_flow=False
    if call_origins:
        for row in graph.events.get(source,[]):
            if row.get('kind')=='return':
                if call_origins & set(graph.origins(row.get('expression',''),source,row.get('order',0))):
                    returned_flow=True
            if row.get('kind')=='branch':
                condition=(row.get('condition') or '').casefold()
                has_state=any(token in condition for token in (
                    'refund','refundable','transstate','paystate','status','state'))
                if has_state and call_origins & set(graph.origins(
                        row.get('condition',''),source,row.get('order',0))):
                    state_branch=True
                if 'refund' in caller and name in ('isfail','isunKnow','issuccess'):
                    state_branch|=bool(call_origins & set(graph.origins(
                        row.get('condition',''),source,row.get('order',0))))
        for sibling in graph.edges:
            if sibling['source']!=source or sibling is edge or not critical_sink(sibling):
                continue
            for argument in sibling['site'].get('arguments',[]):
                if call_origins & set(graph.origins(argument,source,sibling['site'].get('order',0))):
                    sink_flow=True
    state_context=(any(token in name for token in ('refund','refundable','transstate','paystate','status','state')) or
                   any(token in caller_method for token in ('refundstatus','refundstate','transstatus','paystate')))
    if name=='<init>':
        response_type=any(token in target_surface for token in (
            'bizexception','exception','bizresult','response','resp','reply','errorcode'))
        return response_type or sink_flow
    if access_method:
        if name.startswith(('equals','hashcode','tostring')):
            return state_branch or sink_flow
        # DTO/枚举访问器由字段来源与分支事实覆盖；这里只保留关键状态或副作用数据流。
        database_query=any(token in receiver for token in ('mapper','dao','repository','jdbc','database','recordindex','store')) and name.startswith(
            ('getby','find','query','select','count','exists','list','load','read'))
        qcm_accessor=custom_target and any(token in receiver for token in ('uniquecontrolapi','fgqrcodehelper'))
        return state_context or state_branch or sink_flow or database_query or qcm_accessor
    # DTO 字段填充由字段来源事实覆盖；不把每个 setter 或 builder 链拆成绑定义务。
    if name.startswith('set'):
        database_target=any(token in receiver for token in ('mapper','dao','repository','jdbc','database','recordindex','store'))
        return database_target and any(token in name[3:] for token in ('refund','status','state'))
    if builder_chain:
        return sink_flow
    if name=='<init>':
        return any(token in target_surface for token in ('bizexception','exception','bizresult','response','resp',
                                                          'reply','errorcode'))
    if (returned_flow and critical_return_context) or sink_flow or state_branch:
        return True
    if sink_context:
        return True
    # 只检查调用者、接收者和方法名；参数中的关键词不应把普通工具方法误判成关键调用。
    if custom_target:
        return True
    if name.startswith(('insert','update','delete','save','persist','execute','batch','commit','rollback',
                        'register','send','publish','pay','refund','close','cancel','notice','notify','write','remove','mark')):
        return True
    return False


def relation_review_signature(fact):
    data=fact.get('data',{})
    site=dict(data.get('call') or {})
    return stable(data.get('reason'),data.get('receiver_type'),data.get('candidates'),
                  site.get('name'),relation_receiver_hint(site),len(site.get('arguments',[])))


def short(type_name):
    return re.sub(r'<.*>|\[\]|\?\s*(?:extends|super)\s*', '', type_name or '').strip().rsplit('.', 1)[-1]


def literal(expr):
    expr = (expr or '').strip()
    if expr.startswith('"') and expr.endswith('"'):
        try:
            return json.loads(expr)
        except ValueError:
            return None
    if re.fullmatch(r'-?\d+', expr):
        return int(expr)
    return {'true': True, 'false': False}.get(expr)


def redact(text):
    # 源码只存内部材料；仍遮盖明显凭据和含认证信息的地址。
    text = re.sub(r'(?i)((?:password|secret|token|privatekey|api_key)\s*[=:]\s*)"[^"\n]*"',
                  r'\1"<redacted>"', text)
    text = re.sub(r'(?i)((?:put|setProperty)\(\s*"(?:password|secret|token|privatekey|api_key)"\s*,\s*)"[^"\n]*"',r'\1"<redacted>"',text)
    text = re.sub(r'(?im)^(\s*[\w.-]*(?:password|secret|token|private[_-]?key|api[_-]?key)[\w.-]*\s*[:=]\s*)([^\r\n]+)$',r'\1<redacted>',text)
    text = re.sub(r'(?i)((?:key|name)=["\'][^"\']*(?:password|secret|token|privatekey)[^"\']*["\'][^>]*\bvalue=)["\'][^"\']*["\']',r'\1"<redacted>"',text)
    return re.sub(r'(?i)(https?://)[^/\s"\']+:[^/@\s"\']+@', r'\1<redacted>@', text)


def clean(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, dict):
        result={k:clean(v) for k,v in value.items()}
        if any(isinstance(value.get(k),str) and re.search(r'(?i)password|secret|token|private_?key|api_?key',value[k])
               for k in ('name','field','key')):
            for key in ('initializer','expression','origins','default','literal_members','values'):
                if key in result:result[key]='<redacted>'
        return result
    return value


class Graph:
    def __init__(self, index, rules):
        self.index, self.rules = index, rules
        self.classes, self.methods, self.fields = {}, {}, {}
        self.events = collections.defaultdict(list)
        self.duplicates = set()
        for position, row in enumerate(index['events']):
            row = dict(row, order=position)
            if row['kind'] == 'class':
                if row['owner'] in self.classes:
                    self.duplicates.add(row['owner'])
                self.classes[row['owner']] = row
            if row['kind'] == 'method':
                self.methods[row['method']] = row
            if row['kind'] == 'field':
                self.fields[(row['owner'], row['name'])] = row
            if row.get('method'):
                self.events[row['method']].append(row)
        self.methods_by_owner = collections.defaultdict(list)
        self.fields_by_owner = collections.defaultdict(list)
        self.method_lookup = collections.defaultdict(list)
        for owner in sorted(self.duplicates):
            self.index['issues'].append(dict(path=self.classes[owner]['path'],symbol=owner,reason='duplicate_source_type'))
        for symbol, method in self.methods.items():
            self.methods_by_owner[method['owner']].append(symbol)
            self.method_lookup[(method['owner'],method['name'],len(method['parameters']))].append(symbol)
        for (owner, _), field in self.fields.items():
            self.fields_by_owner[owner].append(field)
        self.field_reads = []
        for symbol in sorted(self.methods):
            for row in self.events[symbol]:
                if row['kind'] == 'field_read':
                    key = self.read_field(row)
                    if key:
                        self.field_reads.append(dict(source=symbol, field=key[0]+'#'+key[1], site=row))
        self.edges = []
        self.mapper_methods={(r['owner'],r['name']) for r in index['events'] if r['kind']=='mapper'}
        for symbol in sorted(self.methods):
            for row in self.events[symbol]:
                if row['kind'] == 'call':
                    self.edges.append(self.resolve(row))
                elif row['kind']=='construct':
                    self.edges.append(self.resolve(dict(row,name='<init>',receiver='new '+row['type'],usage='CONSTRUCTOR')))
        callbacks=[];bindings=collections.defaultdict(dict)
        callback_owners={m['owner'] for m in self.methods.values() if m['name'] not in
                         ('equals','hashCode','toString','clone','finalize','<init>')
                         and not re.match(r'^(?:get|set|is)[A-Z]',m['name'])
                         and any(a['name'].rsplit('.',1)[-1]=='Override' for a in m['annotations'])}
        def callback_types(argument,symbol,order):
            result=dict(bindings.get((symbol,argument),{}))
            constructed=argument.strip().startswith('new ') or any(r['kind']=='variable' and r['name']==argument
                and r['order']<order and r['initializer'].strip().startswith('new ') for r in self.events[symbol])
            if constructed:
                owner=self.variable_type(argument,symbol,order)
                if owner in callback_owners:
                    origin=dict(source=symbol,argument=argument,type=owner)
                    result[stable(origin)]=origin
            return result
        # 有界传递源码参数中的具体对象来源，保留来源而不假定实际运行时选择。
        for _ in range(self.rules.get('limits',{}).get('depth',4)):
            changed=False
            for edge in self.edges:
                if len(edge['targets'])!=1:continue
                target=edge['targets'][0]
                for argument,param in zip(edge['site']['arguments'],self.methods[target]['parameters']):
                    values=callback_types(argument,edge['source'],edge['site']['order'])
                    existing=bindings[(target,param['name'])]
                    for key,origin in values.items():
                        if key not in existing:existing[key]=origin;changed=True
            if not changed:break
        for edge in list(self.edges):
            site=edge['site'];symbol=edge['source']
            if site['kind']!='call':continue
            if any(self.methods[t]['has_body'] for t in edge['targets']):continue
            for argument in site['arguments']:
                for _,origin in sorted(callback_types(argument,symbol,site['order']).items()):
                    owner=origin['type']
                    targets=[s for s in self.methods_by_owner.get(owner,[]) if self.methods[s]['name'] not in
                         ('equals','hashCode','toString','clone','finalize','<init>') and
                         not re.match(r'^(?:get|set|is)[A-Z]',self.methods[s]['name']) and
                         any(a['name'].rsplit('.',1)[-1]=='Override' for a in self.methods[s]['annotations'])]
                    if targets:
                        callbacks.append(dict(source=symbol,targets=sorted(targets),receiver_type=owner,
                            status='convention_inferred',reason='constructed_object_passed_to_call_not_runtime_dispatch',
                            requires_review=False,field=None,site=dict(site,callback_argument=argument,callback_origin=origin)))
        self.edges.extend(callbacks)
        # 索引保留原始顺序，重复调用和回调关系不能丢失。
        self.origin_edges = collections.defaultdict(list)
        self.source_edges = collections.defaultdict(list)
        self.field_edges = collections.defaultdict(list)
        self.receiver_edges = collections.defaultdict(list)
        for position, edge in enumerate(self.edges):
            self.source_edges[edge['source']].append(edge)
            self.origin_edges[(edge['source'], compact(edge['site']['expression']))].append(edge)
            if edge['field'] is not None:
                self.field_edges[edge['field']].append(position)
            self.receiver_edges[(edge['receiver_type'], edge['site']['name'].casefold())].append(position)

    def configuration_edges(self, owner, field, getter):
        positions = set(self.field_edges.get(owner+'#'+field, ()))
        positions.update(self.receiver_edges.get((owner, getter), ()))
        return (self.edges[position] for position in sorted(positions))

    @lru_cache(maxsize=50000)
    def type_owner(self, name, current):
        name = re.sub(r'<.*>|\[\]', '', name or '').strip()
        if not name or not current:
            return None
        if name in self.classes and name not in self.duplicates:
            return name
        if name in self.duplicates:
            return None
        owner = self.classes.get(current, {})
        options = []
        if current.rsplit('.', 1)[-1] == name:
            options.append(current)
        options.append(owner.get('package', '') + '.' + name if owner.get('package') else name)
        for imported in owner.get('imports', []):
            if imported.startswith('static '):
                continue
            if imported.endswith('.*'):
                options.append(imported[:-1] + name)
            elif imported.rsplit('.', 1)[-1] == name:
                options.append(imported)
        found = sorted(set(x for x in options if x in self.classes and x not in self.duplicates))
        return found[0] if len(found) == 1 else None

    def declared_type(self,name,owner):
        if name.endswith('[]'):
            return self.declared_type(name[:-2],owner)+'[]'
        resolved=self.type_owner(name,owner)
        if resolved:return resolved
        for imported in self.classes.get(owner,{}).get('imports',[]):
            if not imported.startswith('static ') and imported.rsplit('.',1)[-1]==name:return imported
        return name

    def parent_owners(self, owner, seen=None):
        if owner not in self.classes or owner in self.duplicates:
            return []
        seen = set() if seen is None else seen
        if owner in seen:
            return []
        seen.add(owner)
        result = [owner]
        cls = self.classes.get(owner, {})
        for name in [cls.get('extends', '')] + cls.get('implements', []):
            target = self.type_owner(name, owner)
            if target:
                result.extend(self.parent_owners(target, seen))
        return result

    def variable_type(self, expression, symbol, order, depth=0):
        if depth > 5:
            return None
        method = self.methods[symbol]
        owner = method['owner']
        expression = expression.strip()
        if expression in ('this', 'super'):
            return owner if expression == 'this' else self.type_owner(self.classes[owner].get('extends', ''), owner)
        if expression.startswith('this.'):
            expression = expression[5:]
        for param in method['parameters']:
            if param['name'] == expression:
                return self.declared_type(param['type'],owner)
        declarations = [r for r in self.events[symbol] if r['kind'] == 'variable'
                        and r['name'] == expression and r['order'] < order]
        if declarations:
            return self.declared_type(declarations[-1]['type'],owner)
        for parent in self.parent_owners(owner):
            field = self.fields.get((parent, expression))
            if field:
                return self.declared_type(field['type'],parent)
        known = self.type_owner(expression, owner)
        if known:
            return known
        declared=self.declared_type(expression,owner)
        if declared!=expression:return declared
        if literal(expression) is not None:
            return 'String' if expression.startswith('"') else ('boolean' if expression in ('true', 'false') else 'int')
        created = re.match(r'new\s+([\w.]+)', expression)
        if created:
            return self.type_owner(created[1], owner) or created[1]
        chain = re.fullmatch(r'(.+)\.([\w]+)\(\)', expression)
        if chain:
            receiver = self.variable_type(chain[1], symbol, order, depth+1)
            candidates = self.match_methods(receiver, chain[2], [])
            if len(candidates) == 1:
                target = self.methods[candidates[0]]
                return self.type_owner(target['return_type'], target['owner']) or target['return_type']
            prop = self.accessor(receiver, chain[2])
            if prop:
                return self.type_owner(prop['type'], prop['owner']) or prop['type']
        return None

    def constant_value(self,expression,owner,depth=0):
        if depth>8:return None
        value=literal(expression)
        if value is not None:return value
        match=re.fullmatch(r'([\w.]+)\.([\w]+)(?:\.(get\w+)\(\))?',expression.strip())
        if not match:return None
        type_owner=self.type_owner(match[1],owner)
        field=self.fields.get((type_owner,match[2]))
        if not field:return None
        if not match[3]:return self.constant_value(field['initializer'],type_owner,depth+1)
        methods=self.match_methods(type_owner,match[3],[])
        if len(methods)!=1:return None
        returns=[r for r in self.events[methods[0]] if r['kind']=='return']
        if len(returns)!=1:return None
        target=returns[0]['expression'].replace('this.','')
        args=field.get('constructor_arguments',[])
        constructors=[s for s,m in self.methods.items() if m['owner']==type_owner and m['name']=='<init>' and len(m['parameters'])==len(args)]
        if len(constructors)!=1:return None
        constructor=constructors[0]
        for assignment in self.events[constructor]:
            if assignment['kind']=='assignment' and assignment['target'].replace('this.','')==target:
                for index,param in enumerate(self.methods[constructor]['parameters']):
                    if assignment['expression']==param['name']:
                        return literal(args[index])
        return None

    def match_methods(self, owner, name, args):
        if owner not in self.classes or owner in self.duplicates:
            return []
        result = []
        for cls in self.parent_owners(owner):
            for symbol in self.method_lookup.get((cls,name,len(args)),[]):
                method = self.methods[symbol]
                if cls != owner and (name == '<init>' or 'private' in method.get('modifiers','')):
                    continue
                if cls != owner and self.classes[cls]['class_kind']=='INTERFACE' and 'static' in method.get('modifiers',''):
                    continue
                if cls != owner and self.classes[cls]['class_kind'] not in ('INTERFACE','ANNOTATION_TYPE') and self.classes[cls].get('package')!=self.classes[owner].get('package') and not any(
                        visibility in method.get('modifiers','') for visibility in ('public','protected')):
                    continue
                signature = self.parameter_types(symbol)
                # 子类的同签名覆盖不遮蔽父类的其他重载。
                if any(self.parameter_types(other) == signature and
                       cls in self.parent_owners(self.methods[other]['owner']) for other in result):
                    continue
                result.append(symbol)
        return sorted(result)

    def canonical_type(self, name, owner):
        name = re.sub(r'<.*>', '', name or '').replace(' ', '')
        if name.endswith('[]'):
            return self.canonical_type(name[:-2],owner)+'[]'
        name = self.declared_type(name, owner)
        if name in ('String','Object','Boolean','Byte','Short','Character','Integer','Long','Float','Double'):
            name = 'java.lang.'+name
        return name

    def parameter_types(self, symbol):
        method = self.methods[symbol]
        return tuple(self.canonical_type(p['type'], method['owner']) for p in method['parameters'])

    def more_specific(self, left, right):
        left_types,right_types=self.parameter_types(left),self.parameter_types(right)
        widening={'byte':('short','int','long','float','double'),'short':('int','long','float','double'),
                  'char':('int','long','float','double'),'int':('long','float','double'),
                  'long':('float','double'),'float':('double',)}
        def narrower(a,b):
            return a==b or b in widening.get(a,()) or b in self.parent_owners(a) or (
                b=='java.lang.Object' and a not in ('boolean','byte','short','char','int','long','float','double'))
        return left_types!=right_types and all(narrower(a,b) for a,b in zip(left_types,right_types))

    def argument_match(self, expression, symbol, order, expected):
        actual = self.variable_type(expression, symbol, order)
        if expression.strip() == 'null':
            return (0,1) if expected not in ('byte','short','char','int','long','float','double','boolean') else False
        if not actual:
            return None
        actual = self.canonical_type(actual, self.methods[symbol]['owner'])
        if actual == expected:
            return (0,0)
        if actual.endswith('[]') != expected.endswith('[]'):
            return (0,100) if actual.endswith('[]') and expected in (
                'java.lang.Object','Cloneable','java.lang.Cloneable','java.io.Serializable') else False
        widening = {'byte':['short','int','long','float','double'], 'short':['int','long','float','double'],
                    'char':['int','long','float','double'], 'int':['long','float','double'],
                    'long':['float','double'], 'float':['double']}
        if expected in widening.get(actual,[]):
            return (0,1+widening[actual].index(expected))
        wrappers = dict(zip(('boolean','byte','short','char','int','long','float','double'),
                            ('Boolean','Byte','Short','Character','Integer','Long','Float','Double')))
        boxed = {'java.lang.'+v:k for k,v in wrappers.items()}
        if actual in wrappers and expected == 'java.lang.'+wrappers[actual]:
            return (1,0)
        if actual in wrappers and expected == 'java.lang.Object':
            return (1,100)
        if actual in boxed and expected == boxed[actual]:
            return (1,0)
        if actual in self.classes and expected in self.parent_owners(actual):
            return (0,self.parent_owners(actual).index(expected))
        if actual in self.classes and expected in self.classes and self.classes[expected]['class_kind']=='CLASS':
            complete = all(self.type_owner(name, parent) for parent in self.parent_owners(actual)
                           for name in [self.classes[parent].get('extends','')] if name)
            if complete:
                return False
        if expected == 'java.lang.Object' and actual not in wrappers:
            return (0,100)
        primitives = set(wrappers)
        finals = set(boxed)|{'java.lang.String'}
        if actual in primitives or expected in primitives or (actual in finals and expected in finals):
            return False
        # 未加载外部依赖，不能凭类型短名断言可赋值或不可赋值。
        return None

    def read_field(self, row):
        symbol, name = row['method'], row['name']
        owner = self.methods[symbol]['owner']
        receiver = row['receiver']
        if not receiver:
            if any(p['name'] == name for p in self.methods[symbol]['parameters']) or any(
                    r['kind']=='variable' and r['name']==name and r['order']<row['order'] for r in self.events[symbol]):
                return None
            owners = self.parent_owners(owner)
            for parent in owners:
                if (parent,name) in self.fields:
                    return parent,name
            imported = []
            for value in self.classes[owner].get('imports',[]):
                if value.startswith('static '):
                    target, member = value[7:].rsplit('.',1)
                    if member in (name,'*') and (target,name) in self.fields:
                        imported.append((target,name))
            return imported[0] if len(set(imported))==1 else None
        receiver_type = self.variable_type(receiver, symbol, row['order'])
        for parent in self.parent_owners(receiver_type):
            if (parent,name) in self.fields:
                return parent,name
        return None

    def accessor(self, owner, name):
        prefix = 'get' if name.startswith('get') else ('is' if name.startswith('is') else 'set')
        if not name.startswith(prefix) or len(name) <= len(prefix):
            return None
        suffix = name[len(prefix):].casefold()
        for cls in self.parent_owners(owner):
            for field in self.fields_by_owner.get(cls,[]):
                if field['name'].casefold() == suffix:
                    annotations = [a['name'].rsplit('.', 1)[-1] for a in
                                   field.get('annotations', []) + self.classes[cls].get('annotations', [])]
                    if any(a in ('Data', 'Getter', 'Setter', 'Value') for a in annotations):
                        return field
        return None

    def resolve(self, row):
        symbol = row['method']
        receiver = self.variable_type(row['receiver'], symbol, row['order'])
        candidates = self.match_methods(receiver, row['name'], row['arguments'])
        matches, uncertain = {}, []
        for candidate in candidates:
            checks = [self.argument_match(a, symbol, row['order'], t)
                      for a,t in zip(row['arguments'],self.parameter_types(candidate))]
            if any(c is False for c in checks):
                continue
            if any(c is None for c in checks):
                uncertain.append(candidate)
            else:
                matches[candidate] = (max((c[0] for c in checks),default=0),sum(c[1] for c in checks))
        phase = min((value[0] for value in matches.values()),default=None)
        eligible = [s for s in matches if matches[s][0]==phase]
        applicable = [s for s in eligible if not any(self.more_specific(other,s) for other in eligible)]
        original = candidates
        candidates = sorted(applicable+uncertain)
        status, reason, targets = 'unresolved', 'external_or_unknown_receiver', []
        field = None
        if len(candidates) == 1 and not uncertain:
            targets = candidates
            status, reason = 'source_resolved', 'declared_source_target_not_runtime_dispatch'
            target = self.methods[targets[0]]
            if not target['has_body']:
                implementations = [s for s, m in self.methods.items() if m['has_body'] and m['name'] == row['name']
                                   and self.parameter_types(s) == self.parameter_types(targets[0])
                                   and target['owner'] in self.parent_owners(m['owner'])]
                targets += sorted(implementations)
                if implementations:
                    status = 'convention_inferred' if len(implementations) == 1 else 'unresolved'
                    reason = 'single_source_implementation' if len(implementations) == 1 else 'multiple_interface_implementations'
                else:
                    if (target['owner'],target['name']) in self.mapper_methods:
                        status,reason='convention_inferred','mybatis_namespace_statement'
                    else:
                        status, reason = 'unresolved', 'declaration_without_implementation'
        elif candidates:
            targets, reason = candidates, 'argument_type_unresolved' if len(candidates)==1 else 'ambiguous_overload'
        elif original:
            reason = 'incompatible_arguments'
        else:
            field = self.accessor(receiver, row['name'])
            if field:
                status, reason = 'convention_inferred', 'annotated_generated_accessor'
            elif row['name']=='<init>' and receiver in self.classes and not row['arguments']:
                status,reason='convention_inferred','implicit_no_argument_constructor'
        library = short(receiver) in STANDARD_TYPES or row['receiver'] in STANDARD_TYPES
        return dict(source=symbol, targets=targets, receiver_type=receiver, status=status, reason=reason,
                    requires_review=status == 'unresolved' and not library,
                    field=(field['owner']+'#'+field['name']) if field else None,
                    site=row)

    def origins(self, expression, symbol, order, seen=None, depth=0):
        expression = compact(expression)
        seen = set() if seen is None else seen
        token = (symbol, expression, order)
        if token in seen or depth > 8:
            return ['boundary:alias_or_depth:'+expression]
        seen = seen | {token}
        writes = [r for r in self.events[symbol] if r['order'] < order and
                  ((r['kind'] == 'variable' and r['name'] == expression and r['initializer']) or
                   (r['kind'] == 'assignment' and compact(r['target']) == expression))]
        if writes:
            chosen = []
            use_guards = next((r.get('guards',[]) for r in self.events[symbol] if r['order']==order),[])
            for row in writes:
                if any(a.get('kind')=='switch' and b.get('kind')=='switch' and
                       a['switch_start']==b['switch_start'] and
                       set(a.get('entry_labels',[a['label']])).isdisjoint(b.get('entry_labels',[b['label']])) and
                       not a.get('fallthrough') for a in row.get('guards',[]) for b in use_guards):
                    continue
                if not row.get('guards'):
                    chosen = []
                value = row.get('initializer') if row['kind'] == 'variable' else row['expression']
                chosen.extend(self.origins(value, symbol, row['order'], seen, depth+1))
                if any(g.get('kind')=='switch' and g.get('fallthrough') for g in row.get('guards',[])):
                    chosen.append('boundary:switch_fallthrough')
            return sorted(set(chosen))
        for param in self.methods[symbol]['parameters']:
            if expression == param['name'] or expression.startswith(param['name']+'.'):
                return ['parameter:'+param['type']+'@'+param['name']+expression[len(param['name']):]]
        for edge in self.origin_edges.get((symbol, expression), ()):
            if edge['source'] == symbol and compact(edge['site']['expression']) == expression:
                if edge['targets'] and edge['status'] == 'source_resolved':
                    target = edge['targets'][0]
                    returns = [r for r in self.events[target] if r['kind'] == 'return']
                    resolved = []
                    for row in returns:
                        for value in self.origins(row['expression'], target, row['order'], seen, depth+1):
                            for param, argument in zip(self.methods[target]['parameters'], edge['site']['arguments']):
                                prefix = 'parameter:'+param['type']+'@'+param['name']
                                if value.startswith(prefix):
                                    value = '|'.join(self.origins(argument, symbol, order, seen, depth+1))+value[len(prefix):]
                            resolved.append(value)
                    if resolved:
                        return sorted(set(resolved))
                return ['call:'+expression]
        return [expression or '<empty>']


class ProjectAnalysis:
    def __init__(self, name, repo, before, after, rules, limits):
        self.name, self.repo, self.rules = name, str(repo), rules
        self.limits=dict(limits)
        self.limits['depth']=min(self.limits['depth'],1)
        self.limits['methods']=min(self.limits['methods'],100)
        self.graphs = dict(baseline=Graph(before, rules), result=Graph(after, rules))
        self.facts, self.context, self.relations = [], {}, []
        self.context_bytes, self.context_methods = 0, collections.Counter()
        self.context_symbols=set()
        self.source_line_cache = {}
        self.critical_symbols=collections.defaultdict(set)
        self.critical_fields=set()
        self.critical_classes=set()
        self.critical_evidence_omissions=set()
        self.scope_errors=[]
        self.fact_ids = set()

    def evidence(self, side, row):
        graph = self.graphs[side]
        file = next(f for f in graph.index['files'] if f['path'] == row['path'])
        cache_key = (side, row['path'], row['blob'])
        if cache_key not in self.source_line_cache:
            self.source_line_cache[cache_key] = source_lines(file['source'])
        lines = self.source_line_cache[cache_key]
        start, end = row['line'], min(max(row['line'], row['end_line']), len(lines))
        identifier = stable(self.name, graph.index['tree'], row['path'], row['blob'], start, end)
        if identifier in self.context:
            return identifier
        snippet = redact(''.join(lines[start-1:end]))
        count = len(snippet.encode('utf-8'))
        method_key=(side,row['path'],row.get('method'))
        new_method=row['kind']=='method' and method_key not in self.context_symbols
        if self.context_bytes+count > self.limits['context_bytes'] or (
                new_method and self.context_methods[side] >= self.limits['methods']):
            critical=(row['kind']=='method' and row.get('method') in self.critical_symbols[side])
            critical|=row['kind']=='field' and (row.get('owner'),row.get('name')) in self.critical_fields
            critical|=row['kind']=='class' and row.get('owner') in self.critical_classes
            if critical:
                marker='{}:{}:{}'.format(side,row['path'],row.get('method') or row.get('owner') or row.get('name','<source>'))
                self.critical_evidence_omissions.add(marker)
            return None
        self.context_bytes += count
        self.context_methods[side] += new_method
        if row['kind']=='method':self.context_symbols.add(method_key)
        self.context[identifier] = dict(id=identifier, project=self.name, side=side, tree=graph.index['tree'],
                                        path=row['path'], blob=row['blob'], start_line=start, end_line=end,
                                        digest=stable(snippet), snippet=snippet)
        return identifier

    def fact(self, kind, data, locations, classification='source_fact'):
        evidence = sorted(set(e for side, row in locations if row is not None
                              for e in [self.evidence(side, row)] if e))
        if not evidence:
            return None
        data = clean(data)
        identifier = stable(self.name, kind, data, evidence)
        if identifier in self.fact_ids:
            return next(f for f in self.facts if f['id'] == identifier)
        result = dict(id=identifier, kind=kind, classification=classification, data=data, evidence_ids=evidence)
        self.fact_ids.add(identifier); self.facts.append(result)
        return result

    def changed(self):
        old, new = self.graphs['baseline'], self.graphs['result']
        ignored = {symbol for symbol, row in new.methods.items()
                   if symbol not in old.methods and test_source(row['path'])}
        methods = set()
        for owner in sorted(set(old.classes)|set(new.classes)):
            a,b=old.classes.get(owner),new.classes.get(owner)
            if a is None and b is not None and test_source(b['path']):
                continue
            def signature(row):
                return {k:row.get(k) for k in ('imports','annotations','extends','implements','class_kind')} if row else None
            if signature(a)!=signature(b):
                self.critical_classes.add(owner)
                self.fact('class_binding_change',dict(owner=owner,before=signature(a),after=signature(b)),[('baseline',a),('result',b)])
                imports=set((a or {}).get('imports',[]))^set((b or {}).get('imports',[]))
                names={v.rsplit('.',1)[-1] for v in imports}
                structural=any((a or {}).get(k)!=(b or {}).get(k) for k in ('annotations','extends','implements'))
                for graph in (old,new):
                    for symbol in graph.methods_by_owner.get(owner,[]):
                        if symbol not in ignored and (structural or any(re.search(r'\b'+re.escape(n)+r'\b',graph.methods[symbol]['source']) for n in names)):
                            methods.add(symbol)
                            side='baseline' if graph is old else 'result'
                            self.critical_symbols[side].add(symbol)
        for symbol in sorted(set(old.methods) | set(new.methods)):
            if symbol in ignored:
                continue
            a, b = old.methods.get(symbol), new.methods.get(symbol)
            if compact(a.get('source') if a else '') != compact(b.get('source') if b else ''):
                methods.add(symbol)
                for side,graph in (('baseline',old),('result',new)):
                    if symbol in graph.methods:self.critical_symbols[side].add(symbol)
                self.fact('method_change', dict(symbol=symbol, change='modified' if a and b else ('added' if b else 'deleted')),
                          [('baseline', a), ('result', b)])
        changed_fields = []
        for key in sorted(set(old.fields) | set(new.fields)):
            a, b = old.fields.get(key), new.fields.get(key)
            if a is None and b is not None and test_source(b['path']):
                continue
            if compact(a.get('source') if a else '') != compact(b.get('source') if b else ''):
                changed_fields.append(key)
                self.critical_fields.add(key)
                consumers = sorted({r['source'] for graph in (old,new) for r in graph.field_reads+graph.edges
                                    if r['field']==key[0]+'#'+key[1] and r['source'] not in ignored})
                for side,graph in (('baseline',old),('result',new)):
                    self.critical_symbols[side].update(s for s in consumers if s in graph.methods)
                self.fact('field_change', dict(owner=key[0], field=key[1], before=a.get('type') if a else None,
                    after=b.get('type') if b else None, before_initializer=a.get('initializer') if a else None,
                    after_initializer=b.get('initializer') if b else None, consumers=consumers),
                    [('baseline', a), ('result', b)])
                methods.update(s for s in consumers if s not in ignored)
                for graph in (old, new):
                    for edge in graph.edges:
                        if edge['field'] == key[0]+'#'+key[1] and edge['source'] not in ignored:
                            methods.add(edge['source'])
        return methods, changed_fields

    def traverse(self, side, seeds):
        graph = self.graphs[side]
        forward, reverse = collections.defaultdict(set), collections.defaultdict(set)
        for edge in graph.edges:
            for target in edge['targets']:
                forward[edge['source']].add(target)
                reverse[target].add(edge['source'])
        valid_seeds=[s for s in sorted(seeds) if s in graph.methods]
        if len(valid_seeds)>self.limits['methods']:
            self.scope_errors.append('{} changed methods exceed the per-snapshot method limit ({}/{})'.format(
                side,len(valid_seeds),self.limits['methods']))
        valid_seeds=valid_seeds[:self.limits['methods']]
        seen = {s: 0 for s in valid_seeds}
        # 上溯和下探分别进行，避免经共享配置方法反向扩散到无关业务。
        for direction in (reverse,forward):
            visited={s:0 for s in sorted(seeds) if s in graph.methods}
            queue=collections.deque(sorted(visited))
            while queue:
                symbol=queue.popleft();distance=visited[symbol]
                for other in sorted(direction[symbol]):
                    if other in visited:continue
                    if other not in seeds and '/src/test/' in '/'+graph.methods[other]['path'].replace('\\','/'):
                        continue
                    if distance>=self.limits['depth'] or len(seen)>=self.limits['methods']:
                        continue
                    visited[other]=distance+1;seen[other]=min(seen.get(other,distance+1),distance+1);queue.append(other)
        # 明确构造并传出的回调对象按约定展开；不把这些边称为直接调用。
        queue=collections.deque()
        for edge in graph.edges:
            if edge['source'] in seen and edge['reason']=='constructed_object_passed_to_call_not_runtime_dispatch':
                queue.extend((edge['source'],target) for target in edge['targets'])
        while queue:
            parent,target=queue.popleft()
            if target in seen:continue
            if seen[parent]>=self.limits['depth'] or len(seen)>=self.limits['methods']:
                continue
            seen[target]=seen[parent]+1
            queue.extend((target,other) for other in sorted(forward[target]))
        return seen

    def configurations(self, selected):
        bindings = {}
        for side, graph in self.graphs.items():
            reverse_calls=collections.defaultdict(set)
            for call_edge in graph.edges:
                for target in call_edge['targets']:
                    reverse_calls[target].add(call_edge['source'])
            entrypoint_annotations={'RequestMapping','GetMapping','PostMapping','PutMapping','DeleteMapping',
                                    'PatchMapping','DubboService','KafkaListener','RabbitListener','Scheduled',
                                    'EventListener'}
            entrypoints=set()
            for symbol,method in graph.methods.items():
                names={a['name'].rsplit('.',1)[-1] for a in method.get('annotations',[])}
                names.update(a['name'].rsplit('.',1)[-1] for a in
                             graph.classes.get(method['owner'],{}).get('annotations',[]))
                if names & entrypoint_annotations:
                    entrypoints.add(symbol)
            entrypoint_cache={}
            def find_entrypoints(symbol):
                if symbol in entrypoint_cache:return entrypoint_cache[symbol]
                queue=collections.deque([(symbol,0,[symbol])]);seen={symbol};found=[]
                while queue and len(seen)<=self.limits['methods']:
                    current,distance,path=queue.popleft()
                    if current in entrypoints:
                        found.append(dict(symbol=current,distance=distance,path=list(reversed(path))))
                        continue
                    if distance>=self.limits['depth']:continue
                    for caller in sorted(reverse_calls[current]):
                        if caller in seen:continue
                        seen.add(caller);queue.append((caller,distance+1,path+[caller]))
                found=sorted(found,key=lambda row:(row['distance'],row['symbol']))
                entrypoint_cache[symbol]=found
                return found
            current = {}
            for key, field in sorted(graph.fields.items()):
                if test_source(field['path']) and key not in self.graphs['baseline'].fields:
                    continue
                class_properties=[a for a in graph.classes.get(field['owner'],{}).get('annotations',[])
                                  if a['name'].rsplit('.',1)[-1]=='ConfigurationProperties']
                for ann in field['annotations']+class_properties:
                    if ann['name'].rsplit('.', 1)[-1] not in self.rules['config_annotations']:
                        continue
                    value = literal(ann['arguments'].get('value', ann['arguments'].get('prefix', '')))
                    if value is None:
                        value = ann['arguments']
                    binding_kind='field_annotation'
                    default=field.get('initializer','')
                    if ann in class_properties and isinstance(value,str):
                        value=value+'.'+field['name'];binding_kind='configuration_properties_convention'
                    if ann['name'].rsplit('.',1)[-1]=='Value' and isinstance(value,str):
                        placeholder=re.fullmatch(r'\$\{([^}:]+)(?::(.*))?\}',value)
                        if placeholder:value,default=placeholder[1],placeholder[2] or default
                    scope = self.rules['config_scopes'].get(field['owner'], 'unconfigured')
                    consumers = []
                    getter = 'get'+field['name'].casefold()
                    for edge in graph.configuration_edges(field['owner'], field['name'], getter):
                        if edge['source'] not in self.graphs['baseline'].methods and test_source(graph.methods[edge['source']]['path']):
                            continue
                        site = edge['site']
                        matches = edge['field'] == field['owner']+'#'+field['name']
                        matches |= edge['receiver_type'] == field['owner'] and site['name'].casefold() == getter
                        if matches:
                            conditions=[r['condition'] for r in graph.events[edge['source']] if r['kind']=='branch'
                                        and compact(site['expression']) in compact(r['condition'])]
                            use_kinds=('branch','switch','assignment','return','throw','call','construct')
                            usage_sites=[]
                            for event in graph.events[edge['source']]:
                                if event['kind'] not in use_kinds:continue
                                evidence_text=' '.join(str(event.get(name,'')) for name in
                                    ('condition','expression','target','then','else','arguments','guards'))
                                if compact(site['expression']) not in compact(evidence_text):continue
                                usage_sites.append(dict(kind=event['kind'],line=event.get('line'),
                                    expression=event.get('condition') or event.get('expression') or event.get('target',''),
                                    guards=event.get('guards',[])))
                            consumers.append(dict(symbol=edge['source'],
                                read_location=dict(path=graph.methods[edge['source']]['path'],line=site.get('line'),
                                                   expression=site['expression']),
                                entrypoints=find_entrypoints(edge['source']),
                                entrypoint_status='found_within_depth' if find_entrypoints(edge['source']) else 'not_found_within_depth',
                                guards=site['guards'],conditions=conditions,usage_sites=usage_sites,
                                usage_status='indexed' if usage_sites else 'no_direct_use_event_found',
                                condition_constants=[dict(reference=ref,value=graph.constant_value(ref,field['owner']))
                                    for condition in conditions for ref in re.findall(r'[\w.]+\.get\w+\(\)',condition)
                                    if graph.constant_value(ref,field['owner']) is not None]))
                    current[(field['owner'], field['name'], json.dumps(value, sort_keys=True))] = dict(
                        key=value, owner=field['owner'], field=field['name'], scope=scope,
                        binding_kind=binding_kind,default=default,
                        consumers=sorted(consumers, key=lambda x: (x['symbol'], str(x['guards']))), row=field)
            bindings[side] = current
        for key in sorted(set(bindings['baseline']) | set(bindings['result'])):
            a, b = bindings['baseline'].get(key), bindings['result'].get(key)
            used = any(c['symbol'] in selected[side] for side, binding in [('baseline', a), ('result', b)]
                       if binding for c in binding['consumers'])
            if not used and a and b:
                continue
            def summary(binding):
                return {k: v for k, v in binding.items() if k != 'row'} if binding else None
            locations = [('baseline', a['row'] if a else None), ('result', b['row'] if b else None)]
            for side, binding in [('baseline', a), ('result', b)]:
                if binding:
                    locations += [(side, self.graphs[side].methods[c['symbol']]) for c in binding['consumers']
                                  if c['symbol'] in selected[side]]
            self.fact('configuration', dict(before=summary(a), after=summary(b),
                      change='added' if not a else ('removed' if not b else 'retained_or_consumers_changed')), locations)
        return bindings

    def local_facts(self, side, symbols, required_symbols):
        graph = self.graphs[side]
        output = []
        for symbol in sorted(symbols):
            method = graph.methods[symbol]
            self.evidence(side, method)
            for ann in method.get('annotations', []):
                if ann['name'].rsplit('.', 1)[-1] in ('RequestMapping','GetMapping','PostMapping','PutMapping','DeleteMapping','PatchMapping'):
                    self.fact('entrypoint', dict(symbol=symbol, annotation=ann,class_annotations=[dict(owner=owner,
                        annotations=graph.classes[owner]['annotations']) for owner in graph.parent_owners(method['owner'])]), [(side, method)])
            rows = graph.events[symbol]
            for row in rows:
                if row['kind']=='construct':
                    self.fact('constructor_mapping',dict(side=side,symbol=symbol,type=row['type'],
                        arguments=[dict(expression=a,origins=graph.origins(a,symbol,row['order'])) for a in row['arguments']],
                        status='source_arguments_not_runtime_mapping'),[(side,method)])
                if row['kind']=='assignment':
                    target=row['target'];name=target.replace('this.','')
                    field=graph.fields.get((method['owner'],name))
                    shadowed=any(p['name']==name for p in method['parameters']) or any(r['kind']=='variable' and r['name']==name for r in rows)
                    if field and (target.startswith('this.') or not shadowed):
                        aliases=[graph.constant_value(a['arguments'].get('name',a['arguments'].get('value','')),method['owner'])
                                 for a in field['annotations'] if a['name'].rsplit('.',1)[-1] in self.rules['aliases']]
                        fact=self.fact('field_source',dict(side=side,symbol=symbol,receiver='this',receiver_type=method['owner'],field=next((a for a in aliases if a),name),
                            expression=row['expression'],origins=graph.origins(row['expression'],symbol,row['order']),guards=row['guards']),[(side,method)])
                        if fact:output.append(fact)
                if row['kind'] == 'branch' and side == 'result' and symbol in required_symbols:
                    self.fact('branch', dict(symbol=symbol, condition=row['condition'],
                              then=row['then'], otherwise=row['else']), [(side, method)])
                if row['kind']=='switch' and side=='result' and symbol in required_symbols:
                    self.fact('switch_branch',dict(symbol=symbol,expression=row['expression'],cases=row['cases']),[(side,method)])
                if row['kind'] == 'call':
                    name, args = row['name'], row['arguments']
                    receiver=graph.variable_type(row['receiver'],symbol,row['order'])
                    if name=='copyProperties' and receiver=='org.springframework.beans.BeanUtils' and len(args)>=2:
                        source_type=graph.variable_type(args[0],symbol,row['order']);target_type=graph.variable_type(args[1],symbol,row['order'])
                        common=sorted(set(f['name'] for f in graph.fields_by_owner.get(source_type,[])) &
                                      set(f['name'] for f in graph.fields_by_owner.get(target_type,[])))
                        self.fact('object_copy',dict(side=side,symbol=symbol,source=args[0],target=args[1],source_type=source_type,
                            target_type=target_type,common_declared_fields=common,status='convention_inferred',
                            limitation='Bean property convention; external fields and runtime accessors are not verified'),[(side,method)],'risk_candidate')
                    if side=='result' and symbol in required_symbols and name=='contains' and len(args)==1:
                        self.fact('membership_filter',dict(symbol=symbol,collection=row['receiver'],
                                  member=args[0],usage=row['usage']),[(side,method)])
                    field = None
                    if name.startswith('set') and len(args) == 1:
                        field = name[3:4].lower()+name[4:]
                    elif name in ('put','putIfAbsent') and len(args) >= 2 and isinstance(literal(args[0]), str):
                        field = literal(args[0])
                    elif '.builder(' in row['receiver'] and len(args) == 1:
                        field = name
                    if field:
                        expression = args[-1]
                        origins = graph.origins(expression, symbol, row['order'])
                        aliases = []
                        receiver = graph.variable_type(row['receiver'], symbol, row['order'])
                        for declaration in graph.fields_by_owner.get(receiver,[]):
                            if declaration['name'].casefold() == field.casefold():
                                aliases += [graph.constant_value(a['arguments'].get('name', a['arguments'].get('value', '')),declaration['owner'])
                                            for a in declaration['annotations']
                                            if a['name'].rsplit('.',1)[-1] in self.rules['aliases']]
                        fact = self.fact('field_source', dict(side=side, symbol=symbol, receiver=row['receiver'], receiver_type=receiver,
                                        field=next((a for a in aliases if a), field), expression=expression,
                                        origins=origins, guards=row['guards']), [(side, method)])
                        if fact:
                            output.append(fact)
                if row['kind'] == 'boundary' and side == 'result' and symbol in required_symbols:
                    self.fact('flow_boundary', dict(symbol=symbol, reason=row['reason'], expression=row['expression']),
                              [(side, method)], 'risk_candidate')
            returns = [r for r in rows if r['kind'] == 'return']
            if side == 'result' and returns:
                sources = [dict(expression=r['expression'], guards=r['guards'],
                                origins=graph.origins(r['expression'], symbol, r['order'])) for r in returns]
                fact = self.fact('return_sources', dict(symbol=symbol, returns=sources), [(side, method)])
                expressions = {compact(r['expression']) for r in returns if r['expression'] not in ('null','')}
                assignments = [r for r in rows if r['kind'] in ('assignment','variable')]
                returned_variables={r['expression'] for r in returns if re.fullmatch(r'\w+',r['expression']) and r['expression']!='null'}
                overwritten=[]
                for variable in sorted(returned_variables):
                    writes=[a for a in assignments if (a.get('target')==variable or a.get('name')==variable)
                            and a.get('initializer',a.get('expression','')) not in ('','null')]
                    if len(writes)>1 and any(not a.get('guards') for a in writes[1:]):overwritten.append(variable)
                if overwritten or (returned_variables and len(expressions)>1 and any('(' in x for x in expressions)):
                    self.fact('return_override', dict(symbol=symbol, returns=sources,
                              reassigned_return_variables=overwritten), [(side, method)], 'risk_candidate')
        return output

    def argument_changes(self, changed):
        old, new = self.graphs['baseline'], self.graphs['result']
        for symbol in sorted(changed & set(old.methods) & set(new.methods)):
            calls = []
            for graph in (old, new):
                groups = collections.defaultdict(list)
                for row in graph.events[symbol]:
                    if row['kind'] == 'call':
                        groups[(row['name'], compact(row['receiver']))].append(row)
                calls.append(groups)
            for key in sorted(set(calls[0]) & set(calls[1])):
                if len(calls[0][key]) != len(calls[1][key]):
                    continue
                for a, b in zip(calls[0][key], calls[1][key]):
                    for position, (previous, current) in enumerate(zip(a['arguments'], b['arguments'])):
                        if compact(previous) == compact(current):
                            continue
                        self.fact('argument_source_change', dict(symbol=symbol, call=key[0], argument_index=position,
                                  before=previous, after=current,
                                  before_origins=old.origins(previous,symbol,a['order']),
                                  after_origins=new.origins(current,symbol,b['order'])),
                                  [('baseline',old.methods[symbol]),('result',new.methods[symbol])])

    def run(self):
        changed, fields = self.changed()
        selected = {side: self.traverse(side, changed) for side in self.graphs}
        required_symbols=set(changed)
        previous_calls={(e['source'],e['site']['name'],compact(e['site']['receiver']),tuple(compact(a) for a in e['site']['arguments'])) for e in self.graphs['baseline'].edges}
        for edge in self.graphs['result'].edges:
            if edge['source'] in changed and (edge['source'],edge['site']['name'],compact(edge['site']['receiver']),tuple(compact(a) for a in edge['site']['arguments'])) not in previous_calls:
                required_symbols.update(edge['targets'])
        for side in selected:
            self.critical_symbols[side].update(selected[side])
        configured=set(self.rules['config_annotations'])|set(self.rules['aliases'])|{'Data','Getter','Setter','NotBlank','NotNull','Length','Size','Pattern'}
        for side,graph in self.graphs.items():
            for key in fields:
                row=graph.fields.get(key)
                if not row:continue
                unknown=[a['name'] for a in row['annotations'] if a['name'].rsplit('.',1)[-1] not in configured]
                if unknown:
                    self.fact('annotation_binding_unconfigured',dict(owner=row['owner'],field=row['name'],annotations=unknown),[(side,row)],'risk_candidate')
        self.configurations(selected)
        for side, graph in self.graphs.items():
            referenced_names = set()
            for symbol in selected[side]:
                for event in graph.events[symbol]:
                    referenced_names.update(re.findall(r'\b\w+\b',event.get('condition','')+' '+event.get('expression','')))
            for edge in graph.edges:
                if edge['source'] not in selected[side]:
                    continue
                method = graph.methods[edge['source']]
                evidence = self.evidence(side, method)
                if not evidence:
                    continue
                site = {k: v for k, v in edge['site'].items() if k not in ('blob','order','path')}
                if len(edge['targets'])==1:
                    target=graph.methods[edge['targets'][0]]
                    site['parameter_bindings']=[dict(parameter=param['name'],declared_type=param['type'],expression=arg,
                        origins=graph.origins(arg,edge['source'],edge['site']['order']))
                        for param,arg in zip(target['parameters'],site['arguments'])]
                direct_relation=edge['source'] in changed or any(target in changed for target in edge['targets'])
                relation_in_scope=direct_relation and (edge['status']!='unresolved' or relation_affects_review_scope(
                    edge,graph,self.rules.get('relation_review_tokens',())))
                if relation_in_scope:
                    self.relations.append(dict(id=stable(self.name, side, edge['source'], site), side=side,
                        source=edge['source'], targets=edge['targets'], status=edge['status'], reason=edge['reason'],
                        receiver_type=edge['receiver_type'], field=edge['field'], evidence_ids=[evidence], site=clean(site)))
                if (side == 'result' and edge['source'] in changed and edge['requires_review'] and
                        relation_in_scope and not standalone_diagnostic_log(site)):
                    self.fact('unresolved_relation', dict(symbol=edge['source'], call=site,
                              receiver_type=edge['receiver_type'],reason=edge['reason'],
                              candidates=edge['targets']), [('result',method)], 'risk_candidate')
            for row in graph.index['events']:
                if row['kind'] == 'mapper':
                    linked = [s for s in selected[side] if s.startswith(row['owner']+'#'+row['name']+'(')]
                    if linked:
                        self.fact('mapper_statement', dict(side=side, namespace=row['owner'], name=row['name'],
                                  statement=row['statement'], source=row['source'], dynamic=row['dynamic'],
                                  mappings=row['mappings'], linked_symbols=linked), [(side,row)])
                if row['kind'] == 'field' and row.get('initializer') and row['name'] in referenced_names:
                    if test_source(row['path']) and (row['owner'],row['name']) not in self.graphs['baseline'].fields:
                        continue
                    self.fact('constant_or_collection', dict(side=side, owner=row['owner'], name=row['name'],
                              initializer=row['initializer'], references=[dict(reference=ref,value=graph.constant_value(ref,row['owner']))
                              for ref in re.findall(r'[\w.]+\.getValue\(\)',row['initializer'])],
                              literal_members=[literal(s) for s in re.findall(r'"(?:\\.|[^"\\])*"',row['initializer'])]),[(side,row)])
        self.local_facts('baseline', selected['baseline'], required_symbols)
        self.local_facts('result', selected['result'], required_symbols)
        self.argument_changes(changed)
        return dict(name=self.name, repository=self.repo,
            snapshots={side:graph.index['tree'] for side,graph in self.graphs.items()},
            facts=sorted(self.facts,key=lambda f:f['id']),relations=sorted(self.relations,key=lambda r:r['id']),
            scope_errors=sorted(set(self.scope_errors)),
            critical_evidence_omissions=sorted(self.critical_evidence_omissions),
            stats={side:dict(graph.index['stats'],selected_methods=len(selected[side])) for side,graph in self.graphs.items()}), list(self.context.values())
