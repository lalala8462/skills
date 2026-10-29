import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import analyze
from analysis_engine import COMMON_RULES,Graph,ProjectAnalysis
from source_index import JavaParser,DEFAULT_LIMITS,build_index,git_bytes,stable
from validate_analysis import validate


def git(root,*args):
    return subprocess.check_output(['git','-c','core.longpaths=true','-C',str(root),*args],stderr=subprocess.PIPE).decode('utf-8').strip()


def commit(root,files,message):
    for path,text in files.items():
        target=root/path;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(text,encoding='utf-8')
    git(root,'add','.');git(root,'commit','-qm',message)
    return git(root,'rev-parse','HEAD')


def initialize(root):
    root.mkdir();git(root,'init','-q');git(root,'config','user.name','Fixture');git(root,'config','user.email','fixture@example.invalid')


BEFORE='''package sample;
import java.util.*;
import lombok.Data;
@interface FlagKey {String value();}
@Data class ClientSettings { }
@Data class ShopSettings { @FlagKey("OLD_ALIAS") String aliasMode; }
@Data class PayRecord {String tradeNo; String orderNo; String state; boolean placeholder;}
@Data class RefundRequest {String tradeNo; String orderNo;}
@Data class CashierRecord {String tradeNo; boolean closed;}
class Reply {
 String tradeNo;
 void setTradeNo(String v){this.tradeNo=v;}
 static Reply from(PayRecord r){Reply out=new Reply();out.setTradeNo(r.getTradeNo());return out;}
 static Reply from(CashierRecord r){Reply out=new Reply();out.setTradeNo(r.getTradeNo());return out;}
}
interface Store {
 PayRecord find(String no); List<PayRecord> list(String orderNo);
 String findOrigin(String orderNo); void update(String no);
}
class Allowed { static final List<String> VALUES=Arrays.asList("PAID","PARTIAL"); }
class Flow {
 ClientSettings client; ShopSettings shop; Store store; Map<String,String> sink;
 Reply query(PayRecord record) {Reply out=Reply.from(record);return out;}
 Reply finalQuery(PayRecord record,CashierRecord cashier) {
   Reply out=query(record);if(cashier.isClosed())return Reply.from(cashier);return out;
 }
 String original(PayRecord record){return store.findOrigin(record.getOrderNo());}
 void notifyResult(PayRecord record) {
   String tradeNo=record.getTradeNo();
   if("1".equals(shop.getAliasMode()))tradeNo=original(record);
   sink.put("tradeNo",tradeNo); store.update(record.getTradeNo());
 }
 void qrRoute(){if("1".equals(shop.getAliasMode()))store.find("qr");}
 PayRecord refund(RefundRequest req) {
   PayRecord original=store.find(req.getTradeNo());
   if(original.isPlaceholder())return select(req.getOrderNo());return original;
 }
 PayRecord select(String orderNo) {
   if(orderNo==null)return fail("empty order");
   List<PayRecord> records=store.list(orderNo);
   records.removeIf(r -> !Allowed.VALUES.contains(r.getState()));
   if(records.size()!=1)return fail("not unique");return records.get(0);
 }
 PayRecord fail(String message){return null;}
 void refundNotice(PayRecord actual){sink.put("tradeNo",actual.getTradeNo());}
}
'''
AFTER=BEFORE.replace('@Data class ClientSettings { }','@Data class ClientSettings { @FlagKey("NEW_ALIAS") String originalMode; }').replace(
 'Reply out=Reply.from(record);return out;','Reply out=Reply.from(record);if("1".equals(client.getOriginalMode()))out.setTradeNo(original(record));return out;').replace(
 'if("1".equals(shop.getAliasMode()))tradeNo=original(record);','if("1".equals(client.getOriginalMode()))tradeNo=original(record);').replace(
 'return select(req.getOrderNo());','return select(original.getOrderNo());')
MAPPER='''<mapper namespace="sample.Store"><select id="list">select * from records
<where><if test="orderNo != null">order_no = #{orderNo}</if></where></select></mapper>'''


class AnalysisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='diff-list-fixture-')
        cls.base=Path(cls.temp.name)
        cls.source=cls.base/'source';initialize(cls.source)
        cls.before=commit(cls.source,{'src/Small.java':BEFORE,'src/Store.xml':MAPPER},'base')
        cls.after=commit(cls.source,{'src/Small.java':AFTER},'change')
        cls.rules=dict(COMMON_RULES,config_annotations=['FlagKey'],
                       config_scopes={'sample.ClientSettings':'enterprise','sample.ShopSettings':'merchant'})
        cls.rules_path=cls.base/'rules.json';cls.rules_path.write_text(json.dumps(cls.rules),encoding='utf-8')
        cls.serial=0
        cls.analysis,cls.context=cls.analyze([cls.after])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def analyze(cls,revisions,rules=True,limits=None):
        cls.serial+=1
        clone=cls.base/('clone'+str(cls.serial))
        subprocess.check_call(['git','clone','-q','--no-checkout',str(cls.source),str(clone)])
        out=cls.base/('out'+str(cls.serial))
        rules_path=cls.rules_path if rules else None
        if limits:
            rules_path=cls.base/('limits'+str(cls.serial)+'.json')
            rules_path.write_text(json.dumps(dict(cls.rules,limits=limits)),encoding='utf-8')
        with mock.patch.object(analyze,'prepare_repo',return_value=(clone,'fixture')):
            result=analyze.run([dict(repo='https://github.com/example/fixture',revisions=revisions)],out,rules_path)
        context=[json.loads(line) for line in (out/'context.jsonl').read_text(encoding='utf-8').splitlines()]
        return result,context

    def facts(self,kind):
        return [f for f in self.analysis['projects'][0]['facts'] if f['kind']==kind]

    def test_seven_fixture_findings(self):
        configs=self.facts('configuration')
        new=next(f for f in configs if f['data']['after'] and f['data']['after']['key']=='NEW_ALIAS')
        self.assertEqual(new['data']['after']['scope'],'enterprise')
        old=next(f for f in configs if f['data']['after'] and f['data']['after']['key']=='OLD_ALIAS')
        self.assertTrue(any('qrRoute' in c['symbol'] for c in old['data']['after']['consumers']))
        change=next(f for f in self.facts('argument_source_change') if f['data']['call']=='select')
        self.assertEqual(change['data']['before'],'req.getOrderNo()')
        self.assertEqual(change['data']['after'],'original.getOrderNo()')
        self.assertTrue(any('size()' in f['data']['condition'] for f in self.facts('branch')))
        self.assertTrue(any(f['data']['name']=='VALUES' for f in self.facts('constant_or_collection')))
        self.assertTrue(self.facts('field_source_difference'))
        self.assertTrue(any('finalQuery' in f['data']['symbol'] for f in self.facts('return_override')))
        fields=self.facts('field_source')
        self.assertTrue(any('findOrigin' in str(f['data']['origins']) for f in fields if 'notifyResult' in f['data']['symbol']))
        self.assertTrue(any(r['site']['name']=='update' and r['site']['arguments']==['record.getTradeNo()']
                            for r in self.analysis['projects'][0]['relations']))

    def test_snapshot_ignores_unselected_commit(self):
        first=commit(self.source,{'one.txt':'selected'},'first')
        commit(self.source,{'hidden.txt':'must not leak'},'not selected')
        last=commit(self.source,{'two.txt':'selected'},'last')
        analysis,_=self.analyze([first,last])
        project=analysis['projects'][0];tree=project['snapshots']['result']
        names=git(project['analysis_clone'],'ls-tree','-r','--name-only',tree)
        self.assertNotIn('hidden.txt',names)
        self.assertIn('two.txt',names)
        self.assertNotEqual(tree,git(self.source,'rev-parse',last+'^{tree}'))

    def test_conflict_has_no_final_facts(self):
        base=commit(self.source,{'conflict.txt':'A\n'},'base')
        first=commit(self.source,{'conflict.txt':'B\n'},'first')
        commit(self.source,{'conflict.txt':'C\n'},'not selected')
        last=commit(self.source,{'conflict.txt':'D\n'},'last')
        result,_=self.analyze([first,last])
        project=result['projects'][0]
        self.assertTrue(project['hard_errors']);self.assertFalse(project['facts'])

    def test_repeat_is_stable(self):
        other,context=self.analyze([self.after])
        a,b=self.analysis['projects'][0],other['projects'][0]
        for key in ('facts','relations','obligations','boundaries','stats','snapshots'):
            self.assertEqual(a[key],b[key],key)
        self.assertEqual(self.context,context)

    def test_limits_and_unknown_annotations_are_visible(self):
        result,_=self.analyze([self.after],limits={'methods':1})
        self.assertTrue(result['projects'][0]['boundaries'])
        generic,_=self.analyze([self.after],rules=False)
        self.assertFalse(any(f['kind']=='configuration' and f['data'].get('after',{}).get('key')=='NEW_ALIAS'
                             for f in generic['projects'][0]['facts']))
        self.assertTrue(any(f['kind']=='field_change' for f in generic['projects'][0]['facts']))
        self.assertTrue(any(f['kind']=='annotation_binding_unconfigured' for f in generic['projects'][0]['facts']))

    def test_file_limit_is_explicit_and_cannot_silently_pass(self):
        result,context=self.analyze([self.after],limits={'files':1})
        self.assertTrue(result['projects'][0]['boundaries'])
        errors,_=validate(result,context,dict(analysis_id=result['analysis_id'],dispositions=[]),self.empty_cases(result))
        self.assertTrue(errors)

    def test_non_utf8_file_is_reported(self):
        path=self.source/'src/Legacy.java';path.write_bytes(b'//\xff\xfe\nclass Legacy {}')
        git(self.source,'add','.');git(self.source,'commit','-qm','legacy encoding')
        result,_=self.analyze([git(self.source,'rev-parse','HEAD')])
        self.assertTrue(any('decode' in b['reason'] for b in result['projects'][0]['boundaries']))

    def test_zero_net_change_keeps_method_history(self):
        commit(self.source,{'src/Small.java':BEFORE},'reset fixture')
        one=commit(self.source,{'src/Small.java':AFTER},'enable')
        two=commit(self.source,{'src/Small.java':BEFORE},'revert')
        result,_=self.analyze([one,two])
        p=result['projects'][0]
        self.assertEqual(p['snapshots']['baseline'],p['snapshots']['result'])
        self.assertTrue(p['history'])
        self.assertFalse(any(f['kind']=='method_change' for f in p['facts']))

    def test_new_analysis_clone_footer_uses_analysis_clone(self):
        from append_run_dirs import input_clones
        p=self.base/'footer.json'
        p.write_text(json.dumps(self.analysis),encoding='utf-8')
        self.assertEqual(input_clones(p),[self.analysis['projects'][0]['analysis_clone']])

    def test_mapper_is_bound_but_dynamic_sql_not_executed(self):
        statements=self.facts('mapper_statement')
        self.assertTrue(any(f['data']['name']=='list' and f['data']['dynamic'] for f in statements))

    def test_configuration_file_change_cannot_pass_without_coverage(self):
        base=commit(self.source,{'application.properties':'feature=0\npassword=SENSITIVE_VALUE\n'},'config base')
        head=commit(self.source,{'application.properties':'feature=1\npassword=NEW_SENSITIVE_VALUE\n'},'config change')
        result,context=self.analyze([head])
        facts=[f for f in result['projects'][0]['facts'] if f['kind']=='configuration_file_change']
        self.assertTrue(any(f['data']['key']=='feature' for f in facts))
        self.assertNotIn('SENSITIVE_VALUE',json.dumps(context))
        errors,_=validate(result,context,dict(analysis_id=result['analysis_id'],dispositions=[]),self.empty_cases(result))
        self.assertTrue(any('unhandled obligation' in e for e in errors))

    def test_export_is_blocked_before_creating_excel(self):
        import export_test_cases
        directory=self.base/'export-gate';directory.mkdir()
        (directory/'analysis.json').write_text(json.dumps(self.analysis),encoding='utf-8')
        (directory/'context.jsonl').write_text('\n'.join(json.dumps(r) for r in self.context),encoding='utf-8')
        cases,review=self.closed_cases()
        self.all_cases(cases)[0]['coverage_links']=[]
        (directory/'cases.json').write_text(json.dumps(cases),encoding='utf-8')
        (directory/'review.json').write_text(json.dumps(review),encoding='utf-8')
        target=directory/'blocked.xlsx'
        code=export_test_cases.main(['--input',str(directory/'cases.json'),'--output',str(target),
            '--analysis',str(directory/'analysis.json'),'--review',str(directory/'review.json')])
        self.assertEqual(code,2);self.assertFalse(target.exists())

    def test_duplicate_project_names_are_rejected_before_cloning(self):
        spec=dict(name='same',repo='https://github.com/example/fixture',revisions=[self.after])
        with mock.patch.object(analyze,'prepare_repo') as prepare:
            with self.assertRaisesRegex(ValueError,'unique'):
                analyze.run([spec,spec],self.base/'duplicate')
            prepare.assert_not_called()

    def test_manifest_range_uses_one_clone_per_explicit_project(self):
        clones=[]
        for name in ('first','second'):
            target=self.base/name
            subprocess.check_call(['git','clone','-q','--no-checkout',str(self.source),str(target)])
            clones.append((target,name))
        manifest=self.base/'projects.json'
        manifest.write_text(json.dumps({'projects':[dict(name=name,repo='https://github.com/example/'+name,
                            range=dict(base=self.before,head=self.after)) for name in ('first','second')]}),encoding='utf-8')
        with mock.patch.object(analyze,'prepare_repo',side_effect=clones) as prepare:
            code=analyze.main(['--manifest',str(manifest),'--outdir',str(self.base/'manifest-output'),'--rules',str(self.rules_path)])
        self.assertEqual(code,0);self.assertEqual(prepare.call_count,2)
        result=json.loads((self.base/'manifest-output'/'analysis.json').read_text(encoding='utf-8'))
        self.assertEqual([p['name'] for p in result['projects']],['first','second'])
        for project in result['projects']:
            self.assertEqual(project['snapshots']['result'],git(self.source,'rev-parse',self.after+'^{tree}'))

    @staticmethod
    def all_cases(data):
        from test_points import PURPOSES
        return [case for purpose in PURPOSES for case in data['purpose_cases'][purpose]]

    @staticmethod
    def empty_cases(analysis):
        from test_points import compile_plan,PURPOSES
        catalog=compile_plan(dict(version=1,analysis_id=analysis['analysis_id'],requirements=[],rules=[],impacts=[],candidates=[]),analysis)
        return dict(schema_version=2,test_point_catalog=catalog,purpose_cases={p:[] for p in PURPOSES})

    def closed_cases(self,exclude=()):
        from test_points import compile_plan,PURPOSES
        from coverage_constraints import source_contract
        project=self.analysis['projects'][0]
        facts={f['id']:f for f in project['facts']}
        plan=dict(version=1,analysis_id=self.analysis['analysis_id'],requirements=[],rules=[],impacts=[],candidates=[])
        case_rows=[]
        for i,item in enumerate(project['obligations']):
            if item['id'] in exclude:continue
            rule_id='rule-'+str(i);purpose=PURPOSES[i%4]
            plan['rules'].append(dict(id=rule_id,project=project['name'],kind='boundary',subject=item['id'],
                operation='fixture.coverage',core=True,fact_ids=item['fact_ids'],evidence_ids=item['evidence_ids']))
            basis=dict(kind=purpose)
            if purpose=='acceptance':
                basis['requirement_id']='req-'+str(i)
                plan['requirements'].append(dict(id=basis['requirement_id'],rule_ids=[rule_id],
                    source=dict(kind='confirmed_constraint',reference='离线夹具验证规则',statement='验证结构关联完整保留')))
            elif purpose in ('core_regression','related_regression'):
                basis['impact_id']='impact-'+str(i)
                plan['impacts'].append(dict(id=basis['impact_id'],project=project['name'],rule_ids=[rule_id],
                    fact_ids=item['fact_ids'],evidence_ids=item['evidence_ids'],description='离线夹具的源码义务关联'))
            alternatives,constraint=source_contract(item,facts)
            plan['candidates'].append(dict(id='candidate-'+str(i),rule_id=rule_id,
                entry=dict(kind='method',target='fixture.coverage'),conditions=alternatives[0] if alternatives else [],
                observation=dict(surface='接口',target='交易响应及实际调用次数'),
                expected=constraint or dict(kind='equals',value=item['assertion']),purposes=[basis],obligation_ids=[item['id']]))
            messages=item['assertion'].get('errors',[])
            expected='核对指定交易字段和调用次数'+('；'+'；'.join(messages) if messages else '')
            case_rows.append((i,dict(case_id='C'+str(i),title='检查覆盖项'+str(i),
                preconditions='已准备覆盖项中的触发数据',steps=['触发对应分支并检查结果'],expected=[expected],
                observable_evidence=[dict(surface='接口',target='交易响应及实际调用次数',assertion=expected)],
                coverage_links=[dict(obligation_id=item['id'],condition=item['condition'],assertion=item['assertion'],
                    fact_ids=item['fact_ids'],evidence_ids=item['evidence_ids'],observable_evidence_indices=[0])])) )
        catalog=compile_plan(plan,self.analysis)
        points={p['candidate_ids'][0]:p for p in catalog['points']}
        groups={purpose:[] for purpose in PURPOSES}
        for i,case in case_rows:
            point=points['candidate-'+str(i)]
            case['observable_evidence'][0]['constraint']=point['expected']
            case['test_point_links']=[dict(point_id=point['id'],conditions=point['conditions'],
                expected=point['expected'],data_group_ids=[],observable_evidence_indices=[0])]
            groups[point['purpose']].append(case)
        data=json.loads(json.dumps(dict(schema_version=2,test_point_catalog=catalog,purpose_cases=groups)))
        return data,dict(analysis_id=self.analysis['analysis_id'],dispositions=[])

    def test_strict_coverage_and_source_integrity(self):
        cases,review=self.closed_cases()
        errors,_=validate(self.analysis,self.context,review,cases)
        self.assertEqual(errors,[])
        self.all_cases(cases)[0]['coverage_links']=[]
        errors,_=validate(self.analysis,self.context,review,cases)
        self.assertTrue(any('lost source coverage links' in e for e in errors))

    def test_four_purpose_catalog_integrates_with_fixed_source_coverage(self):
        from export_test_cases import normalize,main as export_main
        data,review=self.closed_cases()
        errors,_=validate(self.analysis,self.context,review,data)
        self.assertEqual(errors,[])
        self.assertEqual(sum(len(rows) for rows in normalize(data)['purposes'].values()),len(self.all_cases(self.closed_cases()[0])))
        out=self.base/'four-purpose-delivery';out.mkdir()
        for filename,value in [('analysis.json',self.analysis),('review.json',review),('cases.json',data)]:
            (out/filename).write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
        (out/'context.jsonl').write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in self.context),encoding='utf-8')
        self.assertEqual(export_main(['--input',str(out/'cases.json'),'--output',str(out/'cases.xlsx'),
            '--analysis',str(out/'analysis.json'),'--review',str(out/'review.json')]),0)
        from test_points import PURPOSES,PURPOSE_NAMES
        from validate_report import main as report_main
        counts=[len(data['purpose_cases'][purpose]) for purpose in PURPOSES]
        summary='Excel：cases.xlsx；'+'；'.join(name+'用例数量：'+str(count) for name,count in zip(PURPOSE_NAMES,counts))
        summary+='；总用例数量：'+str(sum(counts))
        content={'## 1. 分析范围':self.analysis['projects'][0]['name']+' '+self.after,
                 '## 5. 测试用例导出':summary,'## 6. 验证情况':'仅完成静态分析与用例设计。'}
        lines=[]
        for line in (ROOT/'assets/diff-list-template.md').read_text(encoding='utf-8').splitlines():
            lines.append(line)
            if line.startswith('#'):lines.extend(['',content.get(line,'无')])
        report=out/'report.md';report.write_text('\n'.join(lines),encoding='utf-8')
        self.assertEqual(report_main([str(report),'--analysis',str(out/'analysis.json'),
            '--review',str(out/'review.json'),'--cases',str(out/'cases.json')]),0)
        data['purpose_cases']['acceptance'].pop()
        errors,_=validate(self.analysis,self.context,review,data)
        self.assertTrue(any('uncovered test points' in e for e in errors))

    def test_wrong_executable_condition_is_rejected_even_with_original_coverage_links(self):
        from test_points import compile_plan
        from export_test_cases import normalize,main as export_main
        data,review=self.closed_cases()
        plan=copy.deepcopy(data['test_point_catalog']['plan'])
        obligations={o['id']:o for o in self.analysis['projects'][0]['obligations']}
        candidate=next(c for c in plan['candidates'] if any(obligations[o]['dimension']=='cardinality' and
                       obligations[o]['condition']['count']=='zero' for o in c['obligation_ids']))
        candidate['conditions']=[dict(subject='records.count',operator='eq',value=5)]
        # 无分析材料时可编译结构目录，但最终带源码校验和导出都必须拒绝。
        catalog=compile_plan(plan)
        point=next(p for p in catalog['points'] if candidate['id'] in p['candidate_ids'])
        case=next(c for rows in data['purpose_cases'].values() for c in rows if
                  c['coverage_links'][0]['obligation_id'] in point['obligation_ids'])
        case['test_point_links'][0].update(point_id=point['id'],conditions=point['conditions'])
        data['test_point_catalog']=catalog
        normalize(data)
        errors,_=validate(self.analysis,self.context,review,data)
        self.assertTrue(any('test conditions do not cover source obligation' in e for e in errors),errors)
        out=self.base/'bad-condition-delivery';out.mkdir()
        for filename,value in [('analysis.json',self.analysis),('review.json',review),('cases.json',data)]:
            (out/filename).write_text(json.dumps(value),encoding='utf-8')
        (out/'context.jsonl').write_text('\n'.join(json.dumps(r) for r in self.context),encoding='utf-8')
        self.assertEqual(export_main(['--input',str(out/'cases.json'),'--output',str(out/'cases.xlsx'),
             '--analysis',str(out/'analysis.json'),'--review',str(out/'review.json')]),2)
        self.assertFalse((out/'cases.xlsx').exists())

    def test_old_case_formats_are_rejected_by_analysis_validation(self):
        review=dict(analysis_id=self.analysis['analysis_id'],dispositions=[])
        for cases in ({'cases':[]},{'focus_cases':[],'regular_cases':[]}):
            errors,_=validate(self.analysis,self.context,review,cases)
            self.assertTrue(any('current four-purpose cases' in e for e in errors),errors)

    def test_coverage_cannot_point_to_another_observation(self):
        data,review=self.closed_cases()
        case=next(c for rows in data['purpose_cases'].values() for c in rows)
        case['observable_evidence'].append(dict(surface='其他',target='无关字段',assertion='该观察与当前测试点无关联'))
        case['coverage_links'][0]['observable_evidence_indices']=[1]
        errors,_=validate(self.analysis,self.context,review,data)
        self.assertTrue(any('corresponding test point' in e for e in errors))

    def test_wrong_fact_condition_and_result_are_rejected(self):
        for field,bad in [('fact_ids',['not-a-fact']),('condition',{'value':'wrong'}),('assertion',{'errors':['wrong']})]:
            cases,review=self.closed_cases()
            self.all_cases(cases)[0]['coverage_links'][0][field]=bad
            errors,_=validate(self.analysis,self.context,review,cases)
            self.assertTrue(any('mismatch' in e for e in errors),field)

    def test_constant_only_change_seeds_readers_and_blocks_empty_coverage(self):
        source='class Limit { static final int MAX=1; boolean allowed(int n){return n<=MAX;} }'
        commit(self.source,{'src/Limit.java':source},'limit baseline')
        head=commit(self.source,{'src/Limit.java':source.replace('MAX=1','MAX=2')},'limit changed')
        result,context=self.analyze([head])
        project=result['projects'][0]
        field=next(f for f in project['facts'] if f['kind']=='field_change' and f['data']['field']=='MAX')
        self.assertEqual(field['data']['after_initializer'],'2')
        self.assertIn('Limit#allowed(int)',field['data']['consumers'])
        self.assertGreater(project['stats']['result']['selected_methods'],0)
        self.assertTrue(any(o['dimension']=='field_behavior' and field['id'] in o['fact_ids'] for o in project['obligations']))
        from test_points import compile_plan,PURPOSES
        catalog=compile_plan(dict(version=1,analysis_id=result['analysis_id'],requirements=[],rules=[],impacts=[],candidates=[]),result)
        cases=dict(schema_version=2,test_point_catalog=catalog,purpose_cases={p:[] for p in PURPOSES})
        errors,_=validate(result,context,dict(analysis_id=result['analysis_id'],dispositions=[]),cases)
        self.assertTrue(any('unhandled obligation' in e for e in errors))

    def test_tampered_source_and_wrong_revision_are_rejected(self):
        cases,review=self.closed_cases()
        for field in ('snippet','tree'):
            context=copy.deepcopy(self.context)
            context[0][field]='bad'
            errors,_=validate(self.analysis,context,review,cases)
            self.assertTrue(any('mismatch' in e or 'verify' in e for e in errors),field)

    def test_exception_requires_evidence_and_retains_model_provenance(self):
        cases,review=self.closed_cases()
        case=next(c for c in self.all_cases(cases) if c['coverage_links'][0]['assertion']['kind']=='review_binding')
        link=case['coverage_links'][0]
        cases,review=self.closed_cases(exclude={link['obligation_id']})
        disposition=dict(obligation_id=link['obligation_id'],status='reviewed_boundary',
            conclusion='已读取接口声明，目标服务未在本次输入仓库中提供。',
            limitations='仅核实静态接口调用，未验证服务发现和运行时路由。',
            evidence_ids=link['evidence_ids'],review_kind='model_source_review')
        review['dispositions']=[disposition]
        errors,_=validate(self.analysis,self.context,review,cases)
        self.assertEqual(errors,[])
        review['dispositions'][0]['evidence_ids']=[]
        errors,_=validate(self.analysis,self.context,review,cases)
        self.assertTrue(any('review evidence' in e for e in errors))


class BindingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.parser=JavaParser()
    @classmethod
    def tearDownClass(cls):cls.parser.close()

    def graph(self,sources):
        parsed=self.parser.parse(sources);events=[]
        for oid,rows in parsed.items():
            for row in rows['events']:events.append(dict(row,path=oid+'.java',blob=oid))
        return Graph(dict(events=events,tree='x',files=[dict(path=k+'.java',blob=k,source=v) for k,v in sources.items()],issues=[],stats={}),COMMON_RULES)

    def test_overloads_and_multiple_implementations_are_not_forced(self):
        graph=self.graph({'a':'''package p; interface Remote{String call(String a);}
class A implements Remote{public String call(String a){return a;}}
class B implements Remote{public String call(String a){return a;}}
class C{Remote r; void run(Object unknown){r.call("x");f(unknown);}
void f(String a){} void f(Integer a){}}'''})
        remote=next(e for e in graph.edges if e['site']['name']=='call')
        overload=next(e for e in graph.edges if e['site']['name']=='f')
        self.assertEqual(remote['status'],'unresolved')
        self.assertEqual(remote['reason'],'multiple_interface_implementations')
        self.assertEqual(overload['reason'],'ambiguous_overload')

    def test_same_name_in_different_packages_does_not_bind_by_name(self):
        graph=self.graph({'a':'package one; class Duplicate{void f(){}}',
                          'b':'package two; class Duplicate{void f(){}}',
                          'c':'package three; class User{Duplicate d;void go(){d.f();}}'})
        self.assertFalse(next(e for e in graph.edges if e['site']['name']=='f')['targets'])

    def test_inherited_overload_and_override_select_the_declared_signature(self):
        graph=self.graph({'inherit':'''class Parent {void choose(int x){} void same(int x){}}
class Child extends Parent {void choose(String x){} @Override void same(int x){}
void run(){choose(1);choose("x");same(1);}}'''})
        edges=[e for e in graph.edges if e['source']=='Child#run()']
        self.assertEqual([e['targets'] for e in edges],
                         [['Parent#choose(int)'],['Child#choose(String)'],['Child#same(int)']])
        self.assertTrue(all(e['status']=='source_resolved' for e in edges))

    def test_single_incompatible_or_unknown_argument_is_not_resolved(self):
        graph=self.graph({'single':'''class A {void choose(String x){} void run(Object value){choose(1);choose(value);}}'''})
        edges=[e for e in graph.edges if e['site']['name']=='choose']
        self.assertEqual([e['status'] for e in edges],['unresolved','unresolved'])
        self.assertEqual(edges[0]['reason'],'incompatible_arguments')
        self.assertEqual(edges[1]['reason'],'argument_type_unresolved')

    def test_primitive_widening_and_qualified_parameter_names(self):
        graph=self.graph({'wide':'class A{void f(long n){}void f(double n){}void run(){f(1);}}',
                          'one':'package one; class Value {}',
                          'two':'package two; class Value {}',
                          'user':'class U{void f(one.Value a){}void f(two.Value a){}void run(one.Value a){f(a);}}'})
        self.assertEqual(next(e for e in graph.edges if e['source']=='A#run()')['targets'],['A#f(long)'])
        self.assertEqual(next(e for e in graph.edges if e['source']=='U#run(one.Value)')['targets'],['U#f(one.Value)'])

    def test_incomparable_overloads_are_not_ranked_by_distance_or_parent_order(self):
        graph=self.graph({'partial':'''interface Left{} interface Right{} class Both implements Left,Right{}
class A{void f(int a,double b){}void f(long a,long b){}void g(Left a){}void g(Right a){}
void run(Both b){f(1,1);g(b);}}'''})
        edges=[e for e in graph.edges if e['source']=='A#run(Both)']
        self.assertTrue(all(e['status']=='unresolved' and e['reason']=='ambiguous_overload' for e in edges))

    def test_array_parameters_do_not_collapse_to_their_component_type(self):
        graph=self.graph({'array':'class Value{}class A{void f(Value v){}void f(Value[] v){}void run(Value[] values){f(values);}}'})
        edge=next(e for e in graph.edges if e['source']=='A#run(Value[])')
        self.assertEqual(edge['targets'],['A#f(Value[])'])
        self.assertEqual(edge['status'],'source_resolved')

    def test_inaccessible_inherited_overload_does_not_hide_public_boxing_target(self):
        graph=self.graph({'parent':'package p; public class Parent{void f(int a){}public void f(Object a){}}',
                          'child':'package q; class Child extends p.Parent{void f(String a){}void run(){f(1);}}',
                          'api':'package api; public interface Api{default void ping(int n){}static void pong(int n){}}',
                          'client':'package use;class Client implements api.Api{void pong(Object n){}void run(){ping(1);pong(1);}}'})
        edge=next(e for e in graph.edges if e['source']=='q.Child#run()')
        self.assertEqual(edge['targets'],['p.Parent#f(Object)'])
        self.assertEqual(edge['status'],'source_resolved')
        self.assertEqual(next(e for e in graph.edges if e['source']=='use.Client#run()')['targets'],['api.Api#ping(int)'])
        self.assertEqual(next(e for e in graph.edges if e['site']['name']=='pong')['targets'],['use.Client#pong(Object)'])

    def test_direct_field_reads_preserve_scope_and_static_imports(self):
        graph=self.graph({'fields':'''package p; class Parent{int limit;}
class Child extends Parent{int run(int limit){return limit+this.limit;} int direct(){return limit;}}''',
                          'constant':'package q; class Constants{static final int MAX=2;}',
                          'user':'package p; import static q.Constants.MAX; class User{int read(){return MAX+q.Constants.MAX;}}'})
        reads=graph.field_reads
        self.assertEqual(len([r for r in reads if r['source']=='p.Child#run(int)']),1)
        self.assertTrue(any(r['source']=='p.Child#direct()' and r['field']=='p.Parent#limit' for r in reads))
        self.assertEqual(len([r for r in reads if r['field']=='q.Constants#MAX']),2)

    def test_field_without_resolvable_consumers_still_requires_coverage(self):
        before=self.graph({'field':'class Flag{int enabled=0;}'}).index
        after=self.graph({'field':'class Flag{int enabled=1;}'}).index
        engine=ProjectAnalysis('sample','unused',before,after,COMMON_RULES,DEFAULT_LIMITS)
        engine.changed()
        self.assertTrue(any(o['dimension']=='field_behavior' for o in engine.obligations))

    def test_switch_merges_origins_and_preserves_case_guards(self):
        graph=self.graph({'switch':'''class Sample{void setNo(String n){}
void run(int kind,String first,String second){String value;switch(kind){case 1:value=first;break;default:value=second;}setNo(value);}}'''})
        call=next(e['site'] for e in graph.edges if e['site']['name']=='setNo')
        self.assertEqual(graph.origins('value',call['method'],call['order']),
                         ['parameter:String@first','parameter:String@second'])
        assignments=[r for r in graph.events[call['method']] if r['kind']=='assignment']
        self.assertEqual([r['guards'][0]['label'] for r in assignments],['1','default'])
        engine=ProjectAnalysis('sample','unused',copy.deepcopy(graph.index),copy.deepcopy(graph.index),COMMON_RULES,DEFAULT_LIMITS)
        engine.local_facts('result',{call['method']},{call['method']})
        self.assertEqual({o['condition']['label'] for o in engine.obligations if o['dimension']=='switch_case'},{'1','default'})

    def test_switch_internal_use_and_later_overwrite(self):
        graph=self.graph({'switch':'''class Sample{void take(String s){} void run(int kind,String a,String b,String c){
String value;switch(kind){case 1:value=a;take(value);break;default:value=b;take(value);}value=c;take(value);}}'''})
        calls=[e['site'] for e in graph.edges if e['site']['name']=='take']
        self.assertEqual([graph.origins('value',c['method'],c['order']) for c in calls],
                         [['parameter:String@a'],['parameter:String@b'],['parameter:String@c']])

    def test_switch_fallthrough_is_an_explicit_boundary(self):
        graph=self.graph({'fall':'class A{void run(int x){String v="";switch(x){case 1:v="a";default:v="b";}System.out.println(v);}}'})
        self.assertTrue(any(r['kind']=='boundary' and r['reason']=='switch_fallthrough' for r in graph.index['events']))
        call=next(e['site'] for e in graph.edges if e['site']['name']=='println')
        self.assertIn('boundary:switch_fallthrough',graph.origins('v',call['method'],call['order']))

    def test_switch_grouped_labels_keep_all_entry_values(self):
        graph=self.graph({'group':'class A{void run(int x){String v;switch(x){case 1:case 2:v="yes";break;default:v="no";}}}'})
        assignment=next(r for r in graph.index['events'] if r['kind']=='assignment')
        self.assertEqual(assignment['guards'][0]['entry_labels'],['1','2'])
        self.assertFalse(any(r['kind']=='boundary' and r['reason']=='switch_fallthrough' for r in graph.index['events']))

    def test_parse_failure_is_not_empty_success(self):
        parsed=self.parser.parse({'broken':'class Broken { void f( {'})
        self.assertTrue(parsed['broken']['error'])

    def test_chinese_source_and_enum_constant_resolution(self):
        graph=self.graph({'utf8':'''package p; enum Switch { ON("1","开启"),OFF("0","关闭");
 final String value; Switch(String value,String label){this.value=value;} String getValue(){return value;} }
 class Holder {String value="中文";void run(){System.out.println(value);}}'''})
        self.assertEqual(graph.constant_value('Switch.ON.getValue()','p.Holder'),'1')
        self.assertEqual(graph.constant_value('Switch.OFF.getValue()','p.Holder'),'0')

    def test_condition_getter_does_not_become_its_own_false_guard(self):
        graph=self.graph({'guard':'class A{boolean flag(){return true;}void run(){if(flag())System.out.println("x");}}'})
        self.assertEqual(next(e for e in graph.edges if e['site']['name']=='flag')['site']['guards'],[])
        self.assertEqual(next(e for e in graph.edges if e['site']['name']=='println')['site']['guards'][0]['negated'],False)

    def test_configuration_properties_and_value_defaults(self):
        graph=self.graph({'cfg':'''package p; @ConfigurationProperties(prefix="sample") class Settings{String enabled;}
 class Other{@Value("${sample.timeout:30}") String timeout;}'''})
        index=graph.index
        # 为纯解析样本补充最小源码文件材料。
        source='package p; @ConfigurationProperties(prefix="sample") class Settings{String enabled;}\nclass Other{@Value("${sample.timeout:30}") String timeout;}'
        for e in index['events']:e['path']='cfg.java'
        index['files']=[dict(path='cfg.java',blob='cfg',source=source)]
        engine=ProjectAnalysis('p','unused',copy.deepcopy(index),copy.deepcopy(index),COMMON_RULES,DEFAULT_LIMITS)
        bindings=engine.configurations({'baseline':{},'result':{}})
        values=list(bindings['result'].values())
        self.assertTrue(any(b['key']=='sample.enabled' for b in values))
        self.assertTrue(any(b['key']=='sample.timeout' and b['default']=='30' for b in values))

    def test_cross_project_contract_matching_is_only_inferred(self):
        from analyze import cross_project_links
        consumer=self.graph({'c':'package p; interface Remote{String get(String x);}class User{Remote remote;void run(){remote.get("x");}}'})
        provider=self.graph({'p':'package p; interface Remote{String get(String x);}class Server implements Remote{public String get(String x){return x;}}'})
        edge=next(e for e in consumer.edges if e['site']['name']=='get')
        relations=[dict(id='r',side='result',source=edge['source'],targets=edge['targets'],site=edge['site'])]
        links=cross_project_links([dict(name='consumer',relations=relations),dict(name='provider',relations=[])],[consumer,provider])
        self.assertEqual(len(links),1)
        self.assertEqual(links[0]['status'],'convention_inferred')
        self.assertIn('not_deployment',links[0]['reason'])

    def test_credential_literals_are_redacted_in_facts_and_context(self):
        from analysis_engine import clean,redact
        self.assertNotIn('SENSITIVE_VALUE',redact('String secret = "SENSITIVE_VALUE";'))
        self.assertNotIn('SENSITIVE_VALUE',json.dumps(clean(dict(name='secret',initializer='"SENSITIVE_VALUE"'))))

    def test_git_blob_failure_is_a_hard_error(self):
        from source_index import batch_blobs
        with mock.patch('source_index.subprocess.run',return_value=mock.Mock(returncode=0,stdout=b'bad missing\n')):
            with self.assertRaisesRegex(ValueError,'Missing Git blob'):
                batch_blobs('unused',['bad'])

    def test_constructor_fields_and_explicit_spring_copy_are_indexed(self):
        graph=self.graph({'copy':'''package p; import org.springframework.beans.BeanUtils;
class From{String identity;}class To{String identity;To(From source){this.identity=source.identity;}}
class Convert{To run(From source){To target=new To(source);BeanUtils.copyProperties(source,target);return target;}}'''})
        engine=ProjectAnalysis('p','unused',copy.deepcopy(graph.index),copy.deepcopy(graph.index),COMMON_RULES,DEFAULT_LIMITS)
        engine.local_facts('result',set(graph.methods),set(graph.methods))
        self.assertTrue(any(f['kind']=='object_copy' and f['data']['common_declared_fields']==['identity'] for f in engine.facts))
        self.assertTrue(any(f['kind']=='field_source' and f['data']['receiver']=='this' and f['data']['field']=='identity' for f in engine.facts))
        constructors=[e for e in graph.edges if e['site']['kind']=='construct']
        self.assertTrue(any(e['targets'] and e['status']=='source_resolved' for e in constructors))

    def test_windows_crlf_positions_preserve_source_lines(self):
        from source_index import source_lines
        source='class A {\r\n String name="中文";\r\n void run(){name="结束";}\r\n}\r\n'
        graph=self.graph({'crlf':source})
        method=next(m for m in graph.methods.values() if m['name']=='run')
        self.assertEqual(method['line'],3)
        self.assertEqual(len(source_lines(source)),4)

    def test_constructed_callbacks_expand_only_as_inferred_relations(self):
        graph=self.graph({'async':'''package p;
interface Transport{void post(Base callback);} interface Executor{void execute(Runnable runnable);}
abstract class Base{abstract void onDone();}
class Main{Forwarder helper;void send(){Callback callback=new Callback();helper.forward(callback);}}
class Forwarder{Transport transport;void forward(Base callback){transport.post(callback);}}
class Callback extends Base{Executor executor;@Override public void onDone(){executor.execute(new Task());}}
class Task implements Runnable{@Override public void run(){System.out.println("persist result");}}
'''})
        engine=ProjectAnalysis('p','unused',copy.deepcopy(graph.index),copy.deepcopy(graph.index),COMMON_RULES,DEFAULT_LIMITS)
        selected=engine.traverse('result',{'p.Main#send()'})
        self.assertIn('p.Callback#onDone()',selected)
        self.assertIn('p.Task#run()',selected)
        callbacks=[e for e in graph.edges if e['reason']=='constructed_object_passed_to_call_not_runtime_dispatch']
        self.assertEqual(len(callbacks),2)
        self.assertTrue(all(e['status']=='convention_inferred' for e in callbacks))

    def test_anonymous_classes_in_distinct_methods_keep_distinct_owners(self):
        graph=self.graph({'anon':'''class A {
void first(){Runnable a=new Runnable(){public void run(){}};}
void second(){Runnable a=new Runnable(){public void run(){}};}
}'''})
        self.assertFalse(graph.duplicates)
        self.assertEqual(len([s for s,m in graph.methods.items() if m['name']=='run']),2)


if __name__=='__main__':unittest.main()
