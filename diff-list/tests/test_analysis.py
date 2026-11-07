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
from analysis_engine import COMMON_RULES,Graph,ProjectAnalysis,relation_affects_review_scope
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
        self.assertTrue(any('finalQuery' in f['data']['symbol'] for f in self.facts('return_override')))
        fields=self.facts('field_source')
        self.assertTrue(any('findOrigin' in str(f['data']['origins']) for f in fields if 'notifyResult' in f['data']['symbol']))
        self.assertTrue(any(r['site']['name']=='update' and r['site']['arguments']==['record.getTradeNo()']
                            for r in self.analysis['projects'][0]['relations']))

    def test_added_test_methods_do_not_become_business_seeds(self):
        production='package seed; class Pay { int run(){ return 1; } }'
        old_test='package seed; class PayTest { void existing(){ System.out.println(1); } }'
        commit(self.source,{'module/src/main/java/seed/Pay.java':production,
               'module/src/test/java/seed/PayTest.java':old_test},'test exclusion baseline')
        head=commit(self.source,{'module/src/main/java/seed/Pay.java':production.replace('return 1','return 2'),
            'module/src/test/java/seed/PayTest.java':old_test.replace(' } }',' } void added(){ System.out.println(2); } }'),
            'module/src/test/java/seed/NewTest.java':'package seed; @interface Value { String value(); } class NewTest { @Value("${TEST_ONLY}") String value="1"; void run(){ System.out.println(value); } }'},'test exclusion change')
        result,context=self.analyze([head]); project=result['projects'][0]
        symbols=[f['data'].get('symbol','') for f in project['facts'] if f['kind']=='method_change']
        self.assertTrue(any(s=='seed.Pay#run()' for s in symbols))
        self.assertFalse(any('PayTest#added' in s or 'NewTest#' in s for s in symbols))
        self.assertFalse(any('/src/test/' in e['path'] for e in context))
        self.assertFalse(any('TEST_ONLY' in str(f.get('data')) for f in project['facts']))

    def test_disconnected_same_label_does_not_expand_review_scope(self):
        production='package labels; class Payload { void setZebraCode(String v){} } class Handler { void run(Payload p){ p.setZebraCode("old"); } }'
        other='package labels; class OtherPayload { void setZebraCode(String v){} } class Other { void write(OtherPayload p){ p.setZebraCode("other"); } } class Same { void write(Payload p){ p.setZebraCode("same"); } }'
        commit(self.source,{'label/src/main/java/labels/Handler.java':production,
                            'label/src/main/java/labels/Other.java':other},'label baseline')
        head=commit(self.source,{'label/src/main/java/labels/Handler.java':production.replace('"old"','"new"')},'label change')
        result,_=self.analyze([head]);project=result['projects'][0]
        self.assertFalse(any(r['source']=='labels.Other#write(OtherPayload)' for r in project['relations']))
        self.assertFalse(any(r['source']=='labels.Same#write(Payload)' for r in project['relations']))
        self.assertTrue(any(f['kind']=='method_change' and 'labels.Handler#run' in str(f['data'])
                            for f in project['facts']))

    def test_call_chain_map_source_difference_does_not_create_cross_method_review(self):
        source='''package mapchain; import java.util.Map;
class Handler {
 void changed(Map<String,String> m,String x,String y){m.put("orderNo",x);m.putIfAbsent("refundNo",x);m.putIfAbsent("same",x);m.put("onlyChanged",x);}
 void caller(Map<String,String> m,String x,String y){changed(m,x,y);m.put("orderNo",x);m.putIfAbsent("refundNo",x);m.putIfAbsent("same",x);m.put("onlyCaller",y);}
 void unrelated(Map<String,String> m,String x,String y){m.put("orderNo",x);}
}'''
        commit(self.source,{'map/src/main/java/mapchain/Handler.java':source},'map baseline')
        after=source.replace('m.put("orderNo",x);','m.put("orderNo",y);',1).replace('m.putIfAbsent("refundNo",x);','m.putIfAbsent("refundNo",y);',1)
        head=commit(self.source,{'map/src/main/java/mapchain/Handler.java':after},'map change')
        result,_=self.analyze([head]);project=result['projects'][0]
        self.assertFalse(any(f['kind'] in ('field_source_difference','field_source_binding_candidate')
                             for f in project['facts']))

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
        for key in ('facts','relations','stats','snapshots'):
            self.assertEqual(a[key],b[key],key)
        self.assertEqual(self.context,context)

    def test_default_rules_do_not_assume_project_specific_configuration(self):
        rules,limits=analyze.load_rules()
        self.assertEqual(rules['config_scopes'],{})
        self.assertEqual(rules['relation_review_tokens'],[])
        self.assertNotIn('ParamKey',rules['config_annotations'])
        self.assertEqual(rules['config_annotations'],
                         ['Value','ConfigurationProperties'])
        self.assertGreater(limits['files'],0)

    def test_limits_and_unknown_annotations_are_visible(self):
        result,_=self.analyze([self.after],limits={'methods':1})
        generic,_=self.analyze([self.after],rules=False)
        self.assertFalse(any(f['kind']=='configuration' and f['data'].get('after',{}).get('key')=='NEW_ALIAS'
                             for f in generic['projects'][0]['facts']))
        self.assertTrue(any(f['kind']=='field_change' for f in generic['projects'][0]['facts']))
        self.assertTrue(any(f['kind']=='annotation_binding_unconfigured' for f in generic['projects'][0]['facts']))

    def test_file_limit_is_explicit_and_cannot_silently_pass(self):
        head=commit(self.source,{'src/A.java':'package sample; class A {}',
            'src/Small.java':AFTER.replace('class Flow {','class Flow { String limitMarker(){return "changed";}')},'limit changed source files')
        result,_=self.analyze([head],limits={'files':1})
        project=result['projects'][0]
        self.assertIn('changed Java source could not be fully indexed: src/Small.java',project['hard_errors'])

    def test_non_utf8_file_is_reported(self):
        path=self.source/'src/Legacy.java';path.write_bytes(b'//\xff\xfe\nclass Legacy {}')
        git(self.source,'add','.');git(self.source,'commit','-qm','encoding fixture')
        result,_=self.analyze([git(self.source,'rev-parse','HEAD')])
        project=result['projects'][0]
        self.assertTrue(any('src/Legacy.java' in error for error in project['hard_errors']))

    def test_zero_net_change_keeps_commit_review(self):
        commit(self.source,{'src/Small.java':BEFORE},'reset fixture')
        one=commit(self.source,{'src/Small.java':AFTER},'enable')
        two=commit(self.source,{'src/Small.java':BEFORE},'revert')
        result,_=self.analyze([one,two])
        p=result['projects'][0]
        self.assertEqual(p['snapshots']['baseline'],p['snapshots']['result'])
        self.assertEqual(len(p['git']['commit_review']['commits']),2)
        self.assertTrue(p['git']['commit_review']['review_required'])
        self.assertFalse(any(f['kind']=='method_change' for f in p['facts']))

    def test_mapper_is_bound_but_dynamic_sql_not_executed(self):
        expanded,_=self.analyze([self.after],limits={'depth':3})
        statements=[fact for fact in expanded['projects'][0]['facts']
                    if fact['kind']=='mapper_statement']
        self.assertTrue(any(f['data']['name']=='list' and f['data']['dynamic'] for f in statements))

    def test_configuration_file_change_is_captured_with_redacted_evidence(self):
        base=commit(self.source,{'application.properties':'feature=0\npassword=SENSITIVE_VALUE\n'},'config base')
        head=commit(self.source,{'application.properties':'feature=1\npassword=NEW_SENSITIVE_VALUE\n'},'config change')
        result,context=self.analyze([head])
        facts=[f for f in result['projects'][0]['facts'] if f['kind']=='configuration_file_change']
        self.assertTrue(any(f['data']['key']=='feature' for f in facts))
        self.assertNotIn('SENSITIVE_VALUE',json.dumps(context))

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

    def direct_cases(self,fact=None):
        from case_contract import PURPOSES
        from review_workbench import build_review
        groups={purpose:[] for purpose in PURPOSES}
        if fact is None:
            fact=next(f for f in self.analysis['projects'][0]['facts']
                      if f['kind']=='method_change' and f['evidence_ids'])
        review=build_review(self.analysis)
        unit=next(unit for project in review['projects'] for unit in project['review_units']
                  if fact['id'] in unit['fact_ids'])
        groups['acceptance'].append(dict(
            case_id='DIRECT-001',
            title='验证本次方法行为变化',
            preconditions='准备可触发该方法的有效业务数据',
            steps=['发起对应业务请求','检查接口结果和持久化结果'],
            expected=['本次修改后的行为生效，且相关业务结果可观察'],
            observable_evidence=[dict(surface='接口',target='业务响应',
                                      assertion='响应字段与本次修改后的源码行为一致')],
            review_unit_ids=[unit['id']]))
        return dict(schema_version=4,analysis_id=self.analysis['analysis_id'],purpose_cases=groups),review

    def test_direct_cases_expand_review_unit_source_evidence(self):
        from case_contract import resolve_case_sources
        from export_test_cases import normalize
        data,review=self.direct_cases()
        errors,_=validate(self.analysis,self.context,review,data)
        self.assertEqual(errors,[])
        self.assertEqual(len(normalize(data,self.analysis,review)['purposes']['acceptance']),1)
        resolved=resolve_case_sources(data,self.analysis,review)['DIRECT-001']
        self.assertTrue(resolved['fact_ids'])
        self.assertTrue(resolved['evidence_ids'])
        broken=copy.deepcopy(data);broken['purpose_cases']['acceptance'][0]['review_unit_ids']=['RU-missing']
        errors,_=validate(self.analysis,self.context,review,broken)
        self.assertTrue(any('unknown review unit' in error for error in errors))
        tampered=copy.deepcopy(self.analysis)
        fact_id=resolved['fact_ids'][0]
        target=next(f for f in tampered['projects'][0]['facts'] if f['id']==fact_id)
        target['data']['change']='tampered'
        errors,_=validate(tampered,self.context,review,data)
        self.assertTrue(any('fact identity mismatch' in error for error in errors))

    def test_review_uses_net_changes_and_direct_relations_without_caps(self):
        from analysis_engine import VERSION
        from review_workbench import build_review
        facts=[];relations=[]
        for index in range(12):
            symbol='sample.service.RefundService#run{}()'.format(index)
            fact_id='change-fact-{}'.format(index)
            facts.append(dict(id=fact_id,kind='method_change',classification='source_fact',
                              data=dict(symbol=symbol,change='modified'),evidence_ids=[]))
            relations.append(dict(id='relation-{}'.format(index),side='result',source=symbol,
                                  targets=['sample.StoreDao#queryById(String)'],status='source_resolved',
                                  reason='source_resolved',receiver_type='sample.StoreDao',field=None,
                                  evidence_ids=[],site=dict(name='queryById',receiver='dao',
                                  expression='dao.queryById(value{})'.format(index),arguments=['value{}'.format(index)])))
        analysis=dict(schema_version=2,analyzer_version=VERSION,analysis_id='review-test',
            projects=[dict(name='sample',change_seeds=[fact['data']['symbol'] for fact in facts],facts=facts,relations=relations,hard_errors=[],stats={})],
            cross_project_relations=[])
        compact=build_review(analysis)['projects'][0]
        self.assertEqual(compact['review_summary']['change_seed_count'],12)
        self.assertEqual(compact['review_summary']['review_unit_count'],12)
        self.assertEqual(compact['review_summary']['selected_relation_count'],12)
        self.assertEqual(compact['review_summary']['review_facet_count'],2)
        self.assertEqual(compact['review_summary']['max_facet_size'],12)
        self.assertEqual({facet['type'] for facet in compact['review_facets']},{'direct_call','owner'})
        self.assertTrue(all(facet['unit_count']==12 for facet in compact['review_facets']))
        self.assertTrue(all('flow' not in unit for unit in compact['review_units']))
        self.assertEqual({unit['symbol'] for unit in compact['review_units']},
                         {fact['data']['symbol'] for fact in facts})
        self.assertEqual({fact['id'] for fact in compact['fact_catalog']},
                         {fact['id'] for fact in facts})
        self.assertNotIn('relation_catalog',compact)

    def test_nonpayment_review_does_not_infer_business_and_ignores_retained_config_as_project_change(self):
        from analysis_engine import VERSION
        from review_workbench import build_review
        symbol='sample.inventory.StockService#adjust(int)'
        analysis=dict(schema_version=2,analyzer_version=VERSION,analysis_id='inventory-review',
            projects=[dict(name='inventory',change_seeds=[symbol],facts=[
                dict(id='F-stock',kind='method_change',classification='source_fact',
                     data=dict(symbol=symbol,change='modified'),evidence_ids=[]),
                dict(id='F-unchanged-setting',kind='configuration',classification='source_fact',
                     data=dict(change='retained_or_consumers_changed',before={},after={}),
                     evidence_ids=[])],
                relations=[],hard_errors=[],stats={})],cross_project_relations=[])
        result=build_review(analysis)['projects'][0]
        self.assertEqual(len(result['review_units']),1)
        self.assertEqual(result['review_units'][0]['symbol'],symbol)
        self.assertNotIn('flow',result['review_units'][0])
        self.assertNotIn('F-unchanged-setting',result['review_units'][0]['fact_ids'])
        self.assertEqual(result['review_summary']['selected_relation_count'],0)

    def test_review_facets_do_not_merge_transitively_and_summary_stays_direct(self):
        from analysis_engine import VERSION
        from review_workbench import build_review
        a='sample.A#run()';b='sample.B#run()';c='sample.C#run()'
        facts=[
            dict(id='a-change',kind='method_change',classification='source_fact',
                 data=dict(symbol=a,change='modified'),evidence_ids=['a']),
            dict(id='b-change',kind='method_change',classification='source_fact',
                 data=dict(symbol=b,change='modified'),evidence_ids=['b']),
            dict(id='c-change',kind='method_change',classification='source_fact',
                 data=dict(symbol=c,change='modified'),evidence_ids=['c']),
            dict(id='x-branch',kind='branch',classification='source_fact',
                 data=dict(symbol='sample.X#load()',condition='x>0',then='yes',otherwise='no'),
                 evidence_ids=['x'])
        ]
        relations=[
            dict(id='a-x',side='result',source=a,targets=['sample.X#load()'],status='source_resolved',
                 reason='source_resolved',receiver_type='sample.X',evidence_ids=[],
                 site=dict(name='load',receiver='x',expression='x.load()',arguments=[])),
            dict(id='b-x',side='result',source=b,targets=['sample.X#load()'],status='source_resolved',
                 reason='source_resolved',receiver_type='sample.X',evidence_ids=[],
                 site=dict(name='load',receiver='x',expression='x.load()',arguments=[])),
            dict(id='b-y',side='result',source=b,targets=['sample.Y#save()'],status='source_resolved',
                 reason='source_resolved',receiver_type='sample.Y',evidence_ids=[],
                 site=dict(name='save',receiver='y',expression='y.save()',arguments=[])),
            dict(id='c-y',side='result',source=c,targets=['sample.Y#save()'],status='source_resolved',
                 reason='source_resolved',receiver_type='sample.Y',evidence_ids=[],
                 site=dict(name='save',receiver='y',expression='y.save()',arguments=[]))
        ]
        analysis=dict(schema_version=2,analyzer_version=VERSION,analysis_id='facet-test',
            projects=[dict(name='sample',change_seeds=[a,b,c],facts=facts,relations=relations,
                           hard_errors=[],stats={})],cross_project_relations=[])
        compact=build_review(analysis)['projects'][0]
        call_facets=[facet for facet in compact['review_facets'] if facet['type']=='direct_call']
        pairs={tuple(facet['unit_ids']) for facet in call_facets}
        self.assertEqual(len(pairs),2)
        self.assertTrue(all(len(pair)==2 for pair in pairs))
        self.assertFalse(any(facet['unit_count']==3 for facet in call_facets))
        a_unit=next(unit for unit in compact['review_units'] if unit['symbol']==a)
        self.assertEqual([row['kind'] for row in a_unit['change_summary']],['method_change'])
        self.assertIn('branch',a_unit['context_fact_groups'])

    def test_review_keeps_config_files_and_mapper_facts_without_scope_expansion(self):
        from analysis_engine import VERSION
        from review_workbench import build_review
        changed='sample.Flow#run()';mapper='sample.Store#list(String)'
        facts=[
            dict(id='change',kind='method_change',classification='source_fact',
                 data=dict(symbol=changed,change='modified'),evidence_ids=['method-evidence']),
            dict(id='mapper',kind='mapper_statement',classification='source_fact',
                 data=dict(side='result',namespace='sample.Store',name='list',statement='list',
                           source='select * from records',dynamic=True,mappings=[],linked_symbols=[mapper]),
                 evidence_ids=['mapper-evidence']),
            dict(id='config',kind='configuration_file_change',classification='source_fact',
                 data=dict(path='application.properties',key='feature.enabled',change='modified',
                           before='false',after='true'),evidence_ids=['config-evidence'])
        ]
        relations=[dict(id='relation',side='result',source=changed,targets=[mapper],status='source_resolved',
                        reason='source_resolved',receiver_type='sample.Store',field=None,
                        evidence_ids=['method-evidence'],site=dict(name='list',receiver='store',
                        expression='store.list(orderNo)',arguments=['orderNo']))]
        analysis=dict(schema_version=2,analyzer_version=VERSION,analysis_id='review-supporting-test',
            projects=[dict(name='sample',change_seeds=[changed],facts=facts,relations=relations,hard_errors=[],stats={})],
            cross_project_relations=[])
        compact=build_review(analysis)['projects'][0]
        kinds={fact['kind'] for fact in compact['fact_catalog']}
        self.assertIn('configuration_file_change',kinds)
        self.assertIn('mapper_statement',kinds)
        self.assertEqual(compact['review_summary']['selected_relation_count'],1)
        project_units=[unit for unit in compact['review_units'] if unit['scope']=='project']
        self.assertEqual(len(project_units),1)
        self.assertIn('config',project_units[0]['fact_ids'])

    def test_review_business_names_do_not_filter_net_changes(self):
        from analysis_engine import VERSION
        from review_workbench import build_review
        facts=[]
        for index,symbol in enumerate(('sample.enums.ATypeEnum#mapDao()','sample.ZPayService#aggregatePay()')):
            facts.append(dict(id='change-fact-{}'.format(index),kind='method_change',classification='source_fact',
                              data=dict(symbol=symbol,change='added'),evidence_ids=[]))
        analysis=dict(schema_version=2,analyzer_version=VERSION,analysis_id='review-priority-test',
            projects=[dict(name='sample',change_seeds=[fact['data']['symbol'] for fact in facts],facts=facts,relations=[],hard_errors=[],stats={})],
            cross_project_relations=[])
        compact=build_review(analysis)['projects'][0]
        self.assertEqual({unit['symbol'] for unit in compact['review_units']},
                         {'sample.enums.ATypeEnum#mapDao()','sample.ZPayService#aggregatePay()'})
        self.assertEqual(compact['review_summary']['change_seed_count'],2)

    def test_review_shared_evidence_does_not_expand_unrelated_facts(self):
        from analysis_engine import VERSION
        from review_workbench import build_review
        changed='sample.Pay#run()'
        unrelated='sample.Other#work()'
        facts=[
            dict(id='change',kind='method_change',classification='source_fact',
                 data=dict(symbol=changed,change='modified'),evidence_ids=['shared']),
            dict(id='branch',kind='branch',classification='source_fact',
                 data=dict(symbol=unrelated,condition='x',then='a',otherwise='b'),evidence_ids=['shared'])
        ]
        analysis=dict(schema_version=2,analyzer_version=VERSION,analysis_id='review-shared-evidence',
            projects=[dict(name='sample',change_seeds=[changed],facts=facts,relations=[],hard_errors=[],stats={})],
            cross_project_relations=[])
        compact=build_review(analysis)['projects'][0]
        self.assertEqual([fact['id'] for fact in compact['fact_catalog']],['change'])

    def delivery_report(self,out,data):
        from case_contract import PURPOSES,PURPOSE_NAMES
        counts=[len(data['purpose_cases'][purpose]) for purpose in PURPOSES]
        summary='Excel：cases.xlsx；'+'；'.join(name+'用例数量：'+str(count)
            for name,count in zip(PURPOSE_NAMES,counts))+'；总用例数量：'+str(sum(counts))
        content={'## 1. 分析范围':self.analysis['projects'][0]['name']+' '+self.after,
                 '## 5. 测试用例导出':summary,'## 6. 验证情况':'仅完成静态分析与用例设计。'}
        lines=[]
        for line in (ROOT/'assets/diff-list-template.md').read_text(encoding='utf-8').splitlines():
            lines.append(line)
            if line.startswith('#'):lines.extend(['',content.get(line,'无')])
        report=out/'report.md';report.write_text('\n'.join(lines).replace('【Agent填写：基于完整复核事实形成业务结论；无相关内容时填写“无”】','无'),encoding='utf-8')
        return report

    def test_direct_cases_integrate_with_excel_and_report(self):
        from export_test_cases import main as export_main
        data,review=self.direct_cases()
        errors,_=validate(self.analysis,self.context,review,data)
        self.assertEqual(errors,[])
        out=self.base/'direct-delivery';out.mkdir()
        for filename,value in [('analysis.json',self.analysis),('review.json',review),('cases.json',data)]:
            (out/filename).write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
        (out/'context.jsonl').write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in self.context),encoding='utf-8')
        report=self.delivery_report(out,data)
        from validate_analysis import main as validate_main
        self.assertEqual(validate_main(['--analysis',str(out/'analysis.json'),
            '--review',str(out/'review.json'),'--cases',str(out/'cases.json'),
            '--coverage-output',str(out/'coverage.json')]),0)
        coverage=json.loads((out/'coverage.json').read_text(encoding='utf-8'))
        self.assertGreaterEqual(coverage['summary']['unit_count'],1)
        self.assertIn('unreferenced_unit_ids',coverage)
        self.assertEqual(export_main(['--input',str(out/'cases.json'),'--output',str(out/'cases.xlsx'),
            '--analysis',str(out/'analysis.json'),'--review',str(out/'review.json')]),0)
        from validate_report import main as report_main
        self.assertEqual(report_main([str(report),'--analysis',str(out/'analysis.json'),
            '--review',str(out/'review.json'),'--cases',str(out/'cases.json')]),0)

    def test_non_current_case_formats_are_rejected_by_analysis_validation(self):
        from review_workbench import build_review
        review=build_review(self.analysis)
        for cases in ({'cases':[]},{'purpose_cases':[]}):
            errors,_=validate(self.analysis,self.context,review,cases)
            self.assertTrue(any('invalid test cases' in error for error in errors),errors)

    def test_constant_only_change_seeds_direct_reader_scope(self):
        source='class Limit { static final int MAX=1; boolean allowed(int n){return n<=MAX;} }'
        commit(self.source,{'src/Limit.java':source},'limit baseline')
        head=commit(self.source,{'src/Limit.java':source.replace('MAX=1','MAX=2')},'limit changed')
        result,_=self.analyze([head])
        project=result['projects'][0]
        field=next(f for f in project['facts'] if f['kind']=='field_change' and f['data']['field']=='MAX')
        self.assertEqual(field['data']['after_initializer'],'2')
        self.assertIn('Limit#allowed(int)',field['data']['consumers'])
        self.assertGreater(project['stats']['result']['selected_methods'],0)

    def test_tampered_resolved_source_is_rejected(self):
        from case_contract import resolve_case_sources
        data,review=self.direct_cases()
        context=copy.deepcopy(self.context)
        referenced=resolve_case_sources(data,self.analysis,review)['DIRECT-001']['evidence_ids'][0]
        row=next(item for item in context if item['id']==referenced)
        row['snippet']='bad'
        errors,_=validate(self.analysis,context,review,data)
        self.assertTrue(any('evidence content mismatch' in error for error in errors))



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

    def test_field_without_resolvable_consumers_still_emits_change_fact(self):
        before=self.graph({'field':'class Flag{int enabled=0;}'}).index
        after=self.graph({'field':'class Flag{int enabled=1;}'}).index
        engine=ProjectAnalysis('sample','unused',before,after,COMMON_RULES,DEFAULT_LIMITS)
        engine.changed()
        self.assertTrue(any(f['kind']=='field_change' and f['data']['field']=='enabled' for f in engine.facts))

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
        fact=next(f for f in engine.facts if f['kind']=='switch_branch')
        self.assertEqual(set(fact['data']['cases']),{'1','default'})

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

    def test_missing_indexed_evidence_is_recorded_as_scope_error(self):
        graph=self.graph({'sample':'class Service {void run(){}}'})
        engine=ProjectAnalysis('sample','unused',graph.index,graph.index,COMMON_RULES,DEFAULT_LIMITS)
        missing=dict(path='missing.java',blob='unknown',line=1,end_line=1,kind='method')
        self.assertIsNone(engine.evidence('result',missing))
        self.assertTrue(any('internal evidence source missing' in row
                            for row in engine.scope_errors))

    def test_graph_type_owner_cache_does_not_pin_graph_instances(self):
        import gc
        import weakref
        graph=self.graph({'types':'''package p;
class Base {} class Child extends Base {}
'''})
        child=next(symbol for symbol in graph.classes if symbol.endswith('.Child'))
        original=graph._resolve_type_owner
        with mock.patch.object(graph,'_resolve_type_owner',wraps=original) as resolve:
            self.assertEqual(graph.type_owner('Base',child),'p.Base')
            self.assertEqual(graph.type_owner('Base',child),'p.Base')
            resolve.assert_called_once()
        reference=weakref.ref(graph)
        del original,resolve,graph
        gc.collect()
        self.assertIsNone(reference())

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
        engine=ProjectAnalysis('p','unused',copy.deepcopy(graph.index),copy.deepcopy(graph.index),
                               COMMON_RULES,dict(DEFAULT_LIMITS,depth=2))
        selected=engine.traverse('result',{'p.Main#send()'})
        self.assertIn('p.Callback#onDone()',selected)
        self.assertNotIn('p.Task#run()',selected)
        callbacks=[e for e in graph.edges if e['reason']=='constructed_object_passed_to_call_not_runtime_dispatch']
        self.assertEqual(len(callbacks),2)
        self.assertTrue(all(e['status']=='convention_inferred' for e in callbacks))

    def test_generic_custom_token_only_applies_when_explicitly_supplied(self):
        graph=self.graph({'scope':'''package demo;
class ExampleAdapter {String getValue(){return "a";}}
class Consumer {ExampleAdapter adapter;
 void run(){String value=adapter.getValue();}
}'''})
        edge=next(e for e in graph.edges if e['site']['name']=='getValue')
        self.assertFalse(relation_affects_review_scope(edge,graph))
        self.assertTrue(relation_affects_review_scope(edge,graph,('ExampleAdapter',)))

    def test_inventory_and_access_checks_without_payment_names(self):
        graph=self.graph({'inventory':'''package sample;
class StockStore {String findById(String key){return key;} void save(String key){}}
class StockService {StockStore store;String apply(String key){
 String prior=store.findById(key);store.save(key);return prior;}}
class AccessHelper {String getLabel(){return "ok";}}
class AccessService {AccessHelper helper;String view(){return helper.getLabel();}}
'''})
        by_name={edge['site']['name']:edge for edge in graph.edges}
        self.assertTrue(relation_affects_review_scope(
            dict(by_name['save'],status='unresolved',requires_review=True),graph))
        self.assertTrue(relation_affects_review_scope(
            dict(by_name['findById'],status='unresolved',requires_review=True),graph))
        self.assertFalse(relation_affects_review_scope(
            dict(by_name['getLabel'],status='unresolved',requires_review=True),graph))

    def test_unresolved_relation_scope_keeps_database_effects_and_drops_plain_accessors(self):
        graph=self.graph({'scope':'''package p;
class Helper {String getValue(){return "value";}String convert(String value){return value;}}
interface Reply {void setPayload(String value);}
class StoreDao {void update(String value){} String getById(String id){return id;}}
class Flow {Helper helper;StoreDao store;Reply reply;void run(String value){String ignored=helper.getValue();store.getById(value);helper.convert("NoticeReq refund ExampleSpecialAdapter");store.update(value);}String response(){return helper.getValue();}String helperResult(){return helper.convert("value");}void setResponse(){reply.setPayload("value");}}
class RefundService {Helper helper;String refundStatus(){return helper.getValue();}}
@RestController class RefundController {Helper helper;@PostMapping String handle(){return helper.convert("value");}}
'''})
        accessor=dict(next(e for e in graph.edges if e['source'].startswith('p.Flow#run(') and e['site']['name']=='getValue'),status='unresolved',requires_review=True)
        update=dict(next(e for e in graph.edges if e['site']['name']=='update'),status='unresolved',requires_review=True)
        convert=dict(next(e for e in graph.edges if e['site']['name']=='convert'),status='unresolved',requires_review=True)
        database_read=dict(next(e for e in graph.edges if e['site']['name']=='getById'),status='unresolved',requires_review=True)
        response_setter=dict(next(e for e in graph.edges if e['site']['name']=='setPayload'),status='unresolved',requires_review=True)
        returned=dict(next(e for e in graph.edges if e['source'].startswith('p.Flow#response(') and e['site']['name']=='getValue'),status='unresolved',requires_review=True)
        plain_return=dict(next(e for e in graph.edges if e['source'].startswith('p.Flow#helperResult(')),status='unresolved',requires_review=True)
        endpoint_return=dict(next(e for e in graph.edges if e['source'].startswith('p.RefundController#handle(')),status='unresolved',requires_review=True)
        refund_getter=dict(next(e for e in graph.edges if e['source'].startswith('p.RefundService#refundStatus(')),status='unresolved',requires_review=True)
        self.assertFalse(relation_affects_review_scope(accessor,graph))
        self.assertTrue(relation_affects_review_scope(update,graph))
        self.assertTrue(relation_affects_review_scope(database_read,graph))
        self.assertFalse(relation_affects_review_scope(response_setter,graph))
        self.assertFalse(relation_affects_review_scope(convert,graph,('ExampleSpecialAdapter','NoticeReq')))
        self.assertFalse(relation_affects_review_scope(returned,graph))
        self.assertFalse(relation_affects_review_scope(plain_return,graph))
        self.assertTrue(relation_affects_review_scope(endpoint_return,graph))
        self.assertTrue(relation_affects_review_scope(refund_getter,graph))

    def test_depth_three_reaches_three_resolved_method_hops(self):
        graph=self.graph({'layers':'''package p;
class Fourth {void fourth(){}}
class Third {Fourth f;void third(){f.fourth();}}
class Second {Third t;void second(){t.third();}}
class First {Second s;void first(){s.second();}}
'''})
        default=ProjectAnalysis('p','unused',graph.index,graph.index,COMMON_RULES,DEFAULT_LIMITS)
        expanded=ProjectAnalysis('p','unused',graph.index,graph.index,COMMON_RULES,
                                 dict(DEFAULT_LIMITS,depth=3))
        start={'p.First#first()'}
        self.assertNotIn('p.Fourth#fourth()',default.traverse('result',start))
        self.assertIn('p.Fourth#fourth()',expanded.traverse('result',start))

    def test_explicit_limits_are_not_silently_clamped(self):
        graph=self.graph({'cap':'class A{void run(){}}'})
        engine=ProjectAnalysis('p','unused',graph.index,graph.index,COMMON_RULES,
                               dict(DEFAULT_LIMITS,depth=3,methods=500))
        self.assertEqual(engine.limits['depth'],3)
        self.assertEqual(engine.limits['methods'],500)
        for invalid in ({'depth':4},{'methods':501},{'context_bytes':67108865},
                        {'files':20001},{'total_bytes':536870913},{'file_bytes':4194305}):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError,'limits exceed'):
                    self.analyze([self.after],limits=invalid)
        expanded,_=self.analyze([self.after],limits={'depth':3})
        self.assertEqual(expanded['limits']['depth'],3)
        self.assertNotEqual(self.analysis['analysis_id'],expanded['analysis_id'])

    def test_builder_field_binding_is_covered_as_field_source_not_relation_review(self):
        graph=mock.Mock();graph.events={};graph.origins.return_value=[]
        edge=dict(source='p.Flow#buildRecord()',receiver_type=None,status='unresolved',
                  site=dict(name='fgTradeNo',receiver='TransRecordPo.builder().fgTradeNo(value)',
                            expression='TransRecordPo.builder().fgTradeNo(value)',arguments=['value'],order=1))
        self.assertFalse(relation_affects_review_scope(edge,graph,('NoticeReq',)))

    def test_anonymous_classes_in_distinct_methods_keep_distinct_owners(self):
        graph=self.graph({'anon':'''class A {
void first(){Runnable a=new Runnable(){public void run(){}};}
void second(){Runnable a=new Runnable(){public void run(){}};}
}'''})
        self.assertFalse(graph.duplicates)
        self.assertEqual(len([s for s,m in graph.methods.items() if m['name']=='run']),2)


if __name__=='__main__':unittest.main()
