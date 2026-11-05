import copy
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import export_test_cases as exporter
import test_points as tp
import validate_report


def analysis():
    from analysis_engine import VERSION
    return {'schema_version':1,'analyzer_version':VERSION,'analysis_id': 'fixed-analysis', 'projects': [{'name': 'sample',
        'facts': [{'id': 'fact-1', 'evidence_ids': ['evidence-1']}],
        'obligations': [{'id': 'obligation-1', 'fact_ids': ['fact-1']}]}]}


def plan():
    return {'version': 1, 'analysis_id': 'fixed-analysis',
        'requirements': [{'id': 'requirement-1', 'source': {'kind': 'user_input',
            'reference': '用户确认的选单规则', 'statement': '使用原记录的订单号选择交易'}, 'rule_ids': ['selection']}],
        'rules': [{'id': 'selection', 'project': 'sample', 'kind': 'source',
            'subject': 'selected.orderNo', 'operation': 'refund.select', 'core': True,
            'fact_ids': ['fact-1'], 'evidence_ids': ['evidence-1']}],
        'impacts': [{'id': 'impact-1', 'project': 'sample', 'rule_ids': ['selection'],
            'fact_ids': ['fact-1'], 'evidence_ids': ['evidence-1'],
            'description': '选单参数改变后影响退款交易定位'}],
        'candidates': [{'id': 'candidate-1', 'rule_id': 'selection',
            'entry': {'kind': 'http', 'target': 'POST /refund'},
            'conditions': [{'subject': 'request.orderNo', 'operator': 'ne', 'other': 'record.orderNo'}],
            'observation': {'surface': '数据库', 'target': 'refund.selectedOrder'},
            'expected': {'kind': 'source_is', 'value': 'record.orderNo'},
            'purposes': [{'kind': 'acceptance', 'requirement_id': 'requirement-1'}],
            'obligation_ids': ['obligation-1'],
            'data_groups': [{'request.orderNo': 'A', 'record.orderNo': 'B'}]}]}


def cases(catalog):
    groups = {p: [] for p in tp.PURPOSES}
    for n, point in enumerate(catalog['points']):
        groups[point['purpose']].append({'case_id': 'CASE-{}'.format(n), 'title': '验证选单来源',
            'preconditions': '准备请求单号与原记录单号不同的交易',
            'steps': ['使用目录中的每组数据申请退款', '查询实际退款记录'],
            'expected': ['所选交易的订单号来自原记录'],
            'observable_evidence': [dict(point['observation'], assertion='选中交易的订单号等于原记录订单号',
                constraint=point['expected'])],
            'coverage_links': [dict(obligation_id=o,condition={},assertion={},fact_ids=['fact-1'],
                evidence_ids=['evidence-1'],observable_evidence_indices=[0]) for o in point['obligation_ids']],
            'test_point_links': [{'point_id': point['id'], 'conditions': point['conditions'],
                'expected': point['expected'], 'data_group_ids': [g['id'] for g in point['data_groups']],
                'observable_evidence_indices': [0]}]})
    return json.loads(json.dumps({'schema_version': 2, 'test_point_catalog': catalog, 'purpose_cases': groups}))


class TestPointTest(unittest.TestCase):
    def compile(self, value=None):
        return tp.compile_plan(value or plan(), analysis())

    def test_grouped_case_pairs_each_input_with_its_expected_result(self):
        raw=plan(); first=raw['candidates'][0]
        first['conditions'].append(dict(subject='channel',operator='eq',value='A'))
        first['data_groups'][0]['channel']='A';first['expected']=dict(kind='equals',value='result-A')
        second=copy.deepcopy(first);second['id']='second';second['conditions'][-1]['value']='B'
        second['data_groups'][0]['channel']='B';second['expected']['value']='result-B'
        raw['candidates'].append(second); catalog=self.compile(raw); data=cases(catalog)
        left,right=data['purpose_cases']['acceptance']
        left['observable_evidence'].extend(right['observable_evidence'])
        right['test_point_links'][0]['observable_evidence_indices']=[1]
        left['test_point_links'].extend(right['test_point_links'])
        left['coverage_links'][0]['observable_evidence_indices']=[0,1]
        data['purpose_cases']['acceptance']=[left]
        exporter.normalize(data,analysis())
        preparation,expected=tp.case_material(data,left)
        for n,point in enumerate(catalog['points'],1):
            self.assertIn('第{}组：'.format(n),preparation)
            line=next(line for line in expected.splitlines() if line.startswith('第{}组：'.format(n)))
            self.assertIn(point['expected']['value'],line)
        with tempfile.TemporaryDirectory() as td:
            target=Path(td)/'grouped.xlsx'
            exporter.export(exporter.normalize(data,analysis()),target)
            with zipfile.ZipFile(target) as book:
                sheet=ET.fromstring(book.read('xl/worksheets/sheet1.xml'))
                text='\n'.join(node.text or '' for node in sheet.iter() if node.tag.endswith('}t'))
                self.assertIn(preparation,text)
                self.assertIn(expected,text)

    def test_workbench_keeps_links_but_skeleton_is_not_a_completed_case(self):
        import review_workbench
        source=analysis()
        source['projects'][0]['obligations'][0].update(condition={},assertion={},evidence_ids=['evidence-1'])
        catalog=tp.compile_plan(plan(),source)
        draft=review_workbench.case_skeleton(source,catalog)
        case=draft['purpose_cases']['acceptance'][0]
        self.assertEqual(case['coverage_links'][0]['obligation_id'],'obligation-1')
        self.assertEqual(case['test_point_links'][0]['data_group_ids'],[catalog['points'][0]['data_groups'][0]['id']])
        self.assertEqual(case['steps'],[])
        with self.assertRaises(ValueError):
            exporter.normalize(draft, analysis())

    def test_grouped_review_keeps_all_obligations_and_incoming_calls(self):
        import review_workbench
        source=analysis(); project=source['projects'][0]
        project['facts'][0]['data']={'symbol':'A#run()'}
        project['obligations'][0].update(dimension='branch',condition={'value':True},assertion={},evidence_ids=['evidence-1'])
        project['relations']=[dict(source='B#call()',side='result',status='source_resolved',targets=['A#run()'],
                                   reason='resolved',site={},evidence_ids=['evidence-1'])]
        project['field_label_candidates']=[]
        project['boundaries']=[dict(parent='A#run()',symbol='C#next()',reason='relation_budget')]
        result=review_workbench.grouped(source); group=result['projects'][0]['groups'][0]
        self.assertEqual([o['id'] for o in group['obligations']],['obligation-1'])
        self.assertEqual(group['incoming'][0]['source'],'B#call()')
        self.assertEqual(group['boundaries'][0]['symbol'],'C#next()')
        source['analyzer_version']='old'
        with self.assertRaises(ValueError):
            review_workbench.grouped(source)

    def test_boundary_batches_preserve_sides_reasons_and_individual_obligations(self):
        import review_workbench
        source=analysis(); project=source['projects'][0]
        project['field_label_candidates']=[]; project['relations']=[]; project['boundaries']=[]
        project['facts'][0]['data']={'symbol':'A#run()'}
        original=project['obligations'][0]
        original.update(id='before',dimension='review_analysis_boundary',
                        condition=dict(side='baseline',parent='A#run()',symbol='B#next()',reason='relation_budget'),
                        assertion={},evidence_ids=['before-evidence'])
        after=copy.deepcopy(original); after['id']='after';after['condition']['side']='result'
        after['evidence_ids']=['after-evidence']
        unrelated=copy.deepcopy(after); unrelated['id']='other';unrelated['condition']['parent']='C#run()'
        project['obligations'].extend([after,unrelated])
        result=review_workbench.grouped(source)['projects'][0]
        self.assertEqual(len(result['boundary_review_batches']),1)
        batch=result['boundary_review_batches'][0]
        self.assertEqual(batch['condition']['parent'],'A#run()')
        self.assertEqual({o['obligation_id'] for o in batch['members']},{'before','after'})
        self.assertEqual({tuple(o['evidence_ids']) for o in batch['members']},
                         {('before-evidence',),('after-evidence',)})
        self.assertEqual({o['obligation_id'] for group in result['unpaired_boundaries'] for o in group['members']},
                         {'other'})
        self.assertEqual({o['id'] for group in result['groups'] for o in group['obligations']},
                         {'before','after','other'})

    def test_instance_substitution_keeps_identity_but_retains_all_data(self):
        raw = plan(); other = copy.deepcopy(raw['candidates'][0]); other['id'] = 'candidate-2'
        other['data_groups'] = [{'request.orderNo': 'C', 'record.orderNo': 'D'}]
        raw['candidates'].append(other)
        catalog = self.compile(raw)
        self.assertEqual(len(catalog['points']), 1)
        point = catalog['points'][0]
        self.assertEqual(len(point['data_groups']), 2)
        self.assertEqual(len(point['candidate_ids']), 2)
        self.assertEqual(point['id'], self.compile()['points'][0]['id'])

    def source_plan(self,dimension,condition,data=None,assertion=None):
        raw,source=plan(),analysis()
        fact=source['projects'][0]['facts'][0]
        fact.update(kind='source_fact',data=data or {})
        source['projects'][0]['obligations'][0].update(dimension=dimension,condition=condition,assertion=assertion or {})
        candidate=raw['candidates'][0]
        candidate['data_groups']=[]
        return raw,source,candidate

    def test_cardinality_requires_actual_zero_one_many_predicate(self):
        raw,source,candidate=self.source_plan('cardinality',{'expression':'(records.size() != 1)','count':'zero'})
        for value in (5,False,'0'):
            candidate['conditions']=[dict(subject='records.count',operator='eq',value=value)]
            with self.assertRaisesRegex(ValueError,'do not cover source'):tp.compile_plan(raw,source)
        candidate['conditions']=[dict(subject='records.count',operator='eq',value=0)]
        tp.compile_plan(raw,source)
        source['projects'][0]['obligations'][0]['condition']['count']='many'
        candidate['conditions']=[dict(subject='records.count',operator='ne',value=1)]
        with self.assertRaisesRegex(ValueError,'do not cover source'):tp.compile_plan(raw,source)
        candidate['conditions']=[dict(subject='records.count',operator='ge',value=2)]
        tp.compile_plan(raw,source)
        candidate['conditions'].append(dict(subject='records.count',operator='lt',value=1))
        with self.assertRaisesRegex(ValueError,'contradictory numeric'):tp.compile_plan(raw,source)

    def test_source_role_alias_is_explicit_and_unknown_binding_fails(self):
        raw,source,candidate=self.source_plan('cardinality',{'expression':'records.size()!=1','count':'zero'})
        candidate['conditions']=[dict(subject='selected.count',operator='eq',value=0)]
        with self.assertRaisesRegex(ValueError,'do not cover source'):tp.compile_plan(raw,source)
        candidate['source_bindings']={'records.count':'selected.count'}
        tp.compile_plan(raw,source)
        candidate['source_bindings']['unrelated']='another'
        with self.assertRaisesRegex(ValueError,'unknown source role'):tp.compile_plan(raw,source)

    def test_equivalence_cannot_override_known_source_count(self):
        raw,source,candidate=self.source_plan('cardinality',{'expression':'records.size()!=1','count':'zero'})
        zero=[dict(subject='records.count',operator='eq',value=0)]
        five=[dict(subject='records.count',operator='eq',value=5)]
        candidate['conditions']=zero
        raw['equivalences']=[dict(id='bad-equivalence',rule_id='selection',entry=candidate['entry'],
            alternatives=[zero,five],canonical=five,fact_ids=['fact-1'],evidence_ids=['evidence-1'],reason='不能用等价声明覆盖明确数量')]
        with self.assertRaisesRegex(ValueError,'do not cover source'):tp.compile_plan(raw,source)

    def test_source_change_checks_both_trigger_and_selected_source(self):
        raw,source,candidate=self.source_plan('argument_source',{'variation':'source_values_differ'},
            {'before':'req.getOrderNo()','after':'original.getOrderNo()'})
        candidate['source_bindings']={'req.getOrderNo()':'request.orderNo','original.getOrderNo()':'record.orderNo'}
        tp.compile_plan(raw,source)
        candidate['expected']['value']='request.orderNo'
        with self.assertRaisesRegex(ValueError,'assertion contradicts source'):tp.compile_plan(raw,source)
        candidate['expected']['value']='record.orderNo'
        candidate['conditions'][0]['operator']='eq'
        with self.assertRaisesRegex(ValueError,'do not cover source'):tp.compile_plan(raw,source)

    def test_known_membership_set_rejects_wrong_state_values(self):
        raw,source,candidate=self.source_plan('membership',{'collection':'Allowed.VALUES','present':True},
                                             {'member':'record.getState()'})
        source['projects'][0]['facts'].append(dict(id='constant',kind='constant_or_collection',evidence_ids=['evidence-1'],
            data=dict(side='result',owner='sample.Allowed',name='VALUES',literal_members=['PAID','PARTIAL'],references=[])))
        candidate['source_bindings']={'record.getState()':'record.state'}
        candidate['conditions']=[dict(subject='record.state',operator='in',value=['CLOSED'])]
        with self.assertRaisesRegex(ValueError,'do not cover source'):tp.compile_plan(raw,source)
        candidate['conditions'][0]['value']=['PAID','PARTIAL']
        tp.compile_plan(raw,source)

    def test_branch_requires_real_trigger_and_extracted_error_result(self):
        raw,source,candidate=self.source_plan('branch',{'expression':'(orderNo == null)','value':True},
            assertion={'errors':['empty order']})
        candidate['conditions']=[dict(subject='orderNo',operator='is_null')]
        candidate['expected']=dict(kind='equals',value='other error')
        with self.assertRaisesRegex(ValueError,'assertion contradicts source'):tp.compile_plan(raw,source)
        candidate['expected']['value']='empty order'
        tp.compile_plan(raw,source)
        candidate['conditions']=[dict(subject='source.condition',operator='eq',value={'expression':'(orderNo == null)','value':True})]
        with self.assertRaisesRegex(ValueError,'do not cover source'):tp.compile_plan(raw,source)

    def test_all_four_purposes_have_one_owner_in_fixed_priority(self):
        raw = plan()
        raw['candidates'][0]['purposes'] += [{'kind': 'core_regression', 'impact_id': 'impact-1'},
            {'kind': 'related_regression', 'impact_id': 'impact-1'}, {'kind': 'boundary'}]
        self.assertEqual(self.compile(raw)['points'][0]['purpose'], 'acceptance')
        raw['requirements'] = []
        raw['candidates'][0]['purposes'].pop(0)
        self.assertEqual(self.compile(raw)['points'][0]['purpose'], 'core_regression')
        raw['candidates'][0]['purposes'].pop(0)
        self.assertEqual(self.compile(raw)['points'][0]['purpose'], 'related_regression')
        raw['candidates'][0]['purposes'].pop(0)
        self.assertEqual(self.compile(raw)['points'][0]['purpose'], 'boundary')

    def test_input_order_condition_order_and_set_order_are_stable(self):
        raw = plan(); candidate = raw['candidates'][0]
        candidate['conditions'] += [{'subject': 'record.state', 'operator': 'in', 'value': ['PAID', 'PARTIAL']}]
        other = copy.deepcopy(candidate); other['id'] = 'candidate-2'; raw['candidates'].append(other)
        first = self.compile(raw)
        raw['candidates'].reverse()
        for row in raw['candidates']:
            row['conditions'].reverse()
            next(p for p in row['conditions'] if p['operator'] == 'in')['value'].reverse()
        self.assertEqual(first, self.compile(raw))

    def test_same_rule_under_another_registry_id_does_not_bypass_dedup(self):
        raw = plan(); rule = copy.deepcopy(raw['rules'][0]); rule['id'] = 'another-name'
        raw['rules'].append(rule)
        other = copy.deepcopy(raw['candidates'][0]); other['id'] = 'other'; other['rule_id'] = rule['id']
        other['purposes'] = [{'kind': 'boundary'}]; raw['candidates'].append(other)
        self.assertEqual(len(self.compile(raw)['points']), 1)

    def test_different_entry_condition_and_observation_remain_distinct(self):
        for change in ('entry', 'condition', 'observation'):
            with self.subTest(change=change):
                raw = plan(); other = copy.deepcopy(raw['candidates'][0]); other['id'] = 'other'
                if change == 'entry': other['entry']['target'] = 'POST /refund/notify'
                if change == 'condition':
                    other['conditions'][0]['operator'] = 'eq'; other['data_groups'] = []
                if change == 'observation': other['observation']['target'] = 'notification.orderNo'
                raw['candidates'].append(other)
                self.assertEqual(len(self.compile(raw)['points']), 2)

    def test_conflicting_expected_constraints_fail(self):
        raw = plan(); other = copy.deepcopy(raw['candidates'][0]); other['id'] = 'conflict'
        other['expected']['value'] = 'request.orderNo'; raw['candidates'].append(other)
        with self.assertRaisesRegex(ValueError, 'conflicting expected'):
            self.compile(raw)

    def test_boolean_and_numeric_values_are_not_equal(self):
        raw = plan(); candidate = raw['candidates'][0]
        candidate['conditions'] = [{'subject': 'config.flag', 'operator': 'eq', 'value': 1}]
        candidate['data_groups'] = [{'config.flag': True}]
        with self.assertRaisesRegex(ValueError, 'contradicts condition'):
            self.compile(raw)

    def test_event_order_is_not_sorted(self):
        raw = plan(); raw['candidates'][0]['conditions'] = [
            {'subject': 'events', 'operator': 'sequence', 'value': ['pay', 'notify']}]
        other = copy.deepcopy(raw['candidates'][0]); other['id'] = 'other'
        other['conditions'][0]['value'].reverse(); raw['candidates'].append(other)
        self.assertEqual(len(self.compile(raw)['points']), 2)

    def test_same_and_cross_enterprise_are_not_collapsed(self):
        raw = plan(); raw['candidates'][0]['conditions'] = [
            {'subject': 'request.enterprise', 'operator': 'eq', 'other': 'record.enterprise'}]
        other = copy.deepcopy(raw['candidates'][0]); other['id'] = 'other'
        other['conditions'][0]['operator'] = 'ne'; raw['candidates'].append(other)
        self.assertEqual(len(self.compile(raw)['points']), 2)

    def test_equivalence_requires_explicit_scoped_evidence(self):
        raw = plan(); first = [{'subject': 'records.count', 'operator': 'eq', 'value': 2}]
        second = [{'subject': 'records.count', 'operator': 'eq', 'value': 3}]
        raw['candidates'][0]['conditions'] = first
        other = copy.deepcopy(raw['candidates'][0]); other['id'] = 'other'; other['conditions'] = second
        raw['candidates'].append(other)
        self.assertEqual(len(self.compile(raw)['points']), 2)
        raw['equivalences'] = [{'id': 'multi', 'rule_id': 'selection',
            'entry': raw['candidates'][0]['entry'], 'alternatives': [first, second], 'canonical': first,
            'fact_ids': ['fact-1'], 'evidence_ids': ['evidence-1'], 'reason': '已核实两者均进入同一多笔拒绝规则'}]
        self.assertEqual(len(self.compile(raw)['points']), 1)
        raw['equivalences'][0]['evidence_ids'] = []
        with self.assertRaises(ValueError): self.compile(raw)

    def test_missing_requirement_source_and_coverage_fail(self):
        raw = plan(); raw['requirements'][0]['source']['kind'] = 'source_code'
        with self.assertRaisesRegex(ValueError, 'cannot establish acceptance'): self.compile(raw)
        raw = plan(); raw['candidates'] = []
        with self.assertRaisesRegex(ValueError, 'lacks acceptance coverage'): self.compile(raw)

    def test_reviewed_change_accepts_normal_flow_without_formal_requirement(self):
        raw = plan()
        raw['requirements'][0]['source'] = dict(kind='reviewed_change',
            reference='本次固定修订中已复核的退款选单变更', statement='正常请求使用已定位记录的订单号选择交易')
        candidate = raw['candidates'][0]
        candidate['conditions'][0]['operator'] = 'eq'
        candidate['data_groups'] = [{'request.orderNo': 'A', 'record.orderNo': 'A'}]
        catalog = self.compile(raw)
        self.assertEqual(catalog['points'][0]['purpose'], 'acceptance')
        self.assertEqual(catalog['plan']['requirements'][0]['source']['kind'], 'reviewed_change')
        tp.validate_cases(cases(catalog), analysis())

    def test_wrapped_chinese_text_increases_export_row_height(self):
        short=exporter._sheet([('A','标题','短前提','短步骤','短预期')])
        long=exporter._sheet([('A','标题','长前提'*80,'短步骤','短预期')])
        ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        short_row=ET.fromstring(short).findall('.//s:sheetData/s:row',ns)[1]
        long_row=ET.fromstring(long).findall('.//s:sheetData/s:row',ns)[1]
        self.assertGreater(float(long_row.attrib['ht']),float(short_row.attrib['ht']))
        self.assertLessEqual(float(long_row.attrib['ht']),409)

    def test_reviewed_change_requires_current_analysis_and_evidenced_rules(self):
        raw = plan(); raw['requirements'][0]['source']['kind'] = 'reviewed_change'
        with self.assertRaisesRegex(ValueError, 'requires current analysis'):
            tp.compile_plan(raw)
        raw['rules'][0]['fact_ids'] = []; raw['rules'][0]['evidence_ids'] = []
        with self.assertRaisesRegex(ValueError, 'requires evidenced rules'): self.compile(raw)
        raw = plan(); raw['requirements'][0]['source']['kind'] = 'reviewed_change'
        raw['rules'][0]['evidence_ids'] = ['outside-evidence']
        with self.assertRaisesRegex(ValueError, 'evidence must belong'): self.compile(raw)

    def test_regression_needs_impact_and_core_registration(self):
        raw = plan(); raw['requirements'] = []
        raw['candidates'][0]['purposes'] = [{'kind': 'core_regression', 'impact_id': 'impact-1'}]
        raw['rules'][0]['core'] = False
        with self.assertRaisesRegex(ValueError, 'registered core rule'): self.compile(raw)
        raw['rules'][0]['core'] = True; raw['impacts'][0]['fact_ids'] = ['missing']
        with self.assertRaisesRegex(ValueError, 'source fact'): self.compile(raw)

    def test_wrong_revision_and_project_references_fail(self):
        raw = plan(); raw['analysis_id'] = 'another-analysis'
        with self.assertRaisesRegex(ValueError, 'different analysis'): self.compile(raw)
        raw = plan(); raw['rules'][0]['project'] = 'outside'
        with self.assertRaises(ValueError): self.compile(raw)

    def test_old_or_malformed_versions_require_fresh_material(self):
        for change in ({'analyzer_version':'1.1.0'},{'schema_version':True},{'schema_version':0}):
            source=analysis();source.update(change)
            with self.assertRaisesRegex(ValueError,'regenerated with the current analyzer'):
                tp.compile_plan(plan(),source)
        catalog=self.compile();catalog['compiler_version']='1.1.0'
        with self.assertRaisesRegex(ValueError,'unsupported test point catalog'):
            exporter.normalize(cases(catalog), analysis())

    def test_renderer_rejects_old_normalized_rows_before_creating_excel(self):
        for rows in ([],{'focus':[],'regular':[],'deduplicated':0}):
            with tempfile.TemporaryDirectory() as folder:
                target=Path(folder)/'cases.xlsx'
                with self.assertRaisesRegex(ValueError,'current four-purpose rows'):
                    exporter.export(rows,target)
                self.assertFalse(target.exists())

    def test_unrelated_source_obligation_and_orphan_case_link_fail(self):
        source = analysis()
        source['projects'][0]['facts'].append({'id': 'fact-2', 'evidence_ids': ['evidence-2']})
        source['projects'][0]['obligations'][0]['fact_ids'] = ['fact-2']
        with self.assertRaisesRegex(ValueError, 'unrelated to the registered rule'):
            tp.compile_plan(plan(), source)
        data = cases(self.compile())
        other=copy.deepcopy(data['purpose_cases']['acceptance'][0]['coverage_links'][0])
        other['obligation_id']='another-obligation'
        data['purpose_cases']['acceptance'][0]['coverage_links'].append(other)
        with self.assertRaisesRegex(ValueError, 'must match the case test points'):
            exporter.normalize(data, analysis())

    def test_catalog_tampering_is_rejected(self):
        catalog = self.compile(); catalog['points'][0]['purpose'] = 'boundary'
        with self.assertRaisesRegex(ValueError, 'deterministic compilation'):
            exporter.normalize(cases(catalog), analysis())

    def test_duplicate_point_and_wrong_purpose_fail(self):
        data = cases(self.compile())
        data['purpose_cases']['acceptance'].append(copy.deepcopy(data['purpose_cases']['acceptance'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate test point'): exporter.normalize(data, analysis())
        data = cases(self.compile())
        data['purpose_cases']['boundary'] = data['purpose_cases']['acceptance']
        data['purpose_cases']['acceptance'] = []
        with self.assertRaisesRegex(ValueError, 'wrong purpose'): exporter.normalize(data, analysis())

    def test_deleted_point_data_or_obligation_link_fail(self):
        for change in ('point', 'data', 'obligation', 'assertion'):
            with self.subTest(change=change):
                data = cases(self.compile()); case = data['purpose_cases']['acceptance'][0]
                if change == 'point': data['purpose_cases']['acceptance'] = []
                if change == 'data': case['test_point_links'][0]['data_group_ids'] = []
                if change == 'obligation': case['coverage_links'] = []
                if change == 'assertion': case['observable_evidence'][0]['constraint']['value'] = 'wrong'
                with self.assertRaises(ValueError): exporter.normalize(data, analysis())

    def test_four_sheets_have_five_columns_and_unchanged_observable_evidence(self):
        rows = exporter.normalize(cases(self.compile()), analysis())
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'cases.xlsx'; count = exporter.export(rows, target)
            self.assertEqual(count['total'], 1)
            with zipfile.ZipFile(target) as archive:
                ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                workbook = ET.fromstring(archive.read('xl/workbook.xml'))
                self.assertEqual([n.attrib['name'] for n in workbook.findall('m:sheets/m:sheet', ns)], list(tp.PURPOSE_NAMES))
                for i in range(1, 5):
                    sheet = ET.fromstring(archive.read('xl/worksheets/sheet{}.xml'.format(i)))
                    self.assertEqual(len(sheet.findall('m:sheetData/m:row', ns)[0]), 5)
                self.assertIn('可观察证据', archive.read('xl/worksheets/sheet1.xml').decode('utf-8'))
                self.assertEqual(len(ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))), 5)

    def test_cli_compiles_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / 'plan.json').write_text(json.dumps(plan()), encoding='utf-8')
            (folder / 'analysis.json').write_text(json.dumps(analysis()), encoding='utf-8')
            command = [sys.executable, '-B', str(ROOT / 'scripts/test_points.py'), '--input', str(folder / 'plan.json'),
                '--analysis', str(folder / 'analysis.json'), '--output', str(folder / 'points.json')]
            result = subprocess.run(command, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            before = (folder / 'points.json').read_bytes()
            self.assertEqual(json.loads(before), self.compile())
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 2)
            self.assertEqual(before, (folder / 'points.json').read_bytes())

    def test_report_accepts_four_counts_and_rejects_wrong_sum(self):
        template = (ROOT / 'assets/diff-list-template.md').read_text(encoding='utf-8')
        lines = []
        for line in template.splitlines():
            lines.append(line)
            if line.startswith('#'):
                lines.extend(['', '无'])
        report = '\n'.join(lines).replace('## 1. 分析范围\n\n无', '## 1. 分析范围\n\nsample ' + 'a' * 40)
        summary = 'Excel：cases.xlsx；' + '；'.join(name + '用例数量：1' for name in tp.PURPOSE_NAMES) + '；总用例数量：4'
        report = report.replace('## 5. 测试用例导出\n\n无', '## 5. 测试用例导出\n\n' + summary)
        errors, _ = validate_report.validate(report, template, [])
        self.assertEqual(errors, [])
        errors, _ = validate_report.validate(report.replace('总用例数量：4', '总用例数量：5'), template, [])
        self.assertTrue(any('之和' in e for e in errors))

    def test_report_counts_must_match_case_material(self):
        data = cases(self.compile())
        summary = 'Excel：cases.xlsx；需求验收用例数量：1；既有核心流程回归用例数量：0；关联回归用例数量：0；业务规则与边界验证用例数量：0；总用例数量：1'
        report = '## 5. 测试用例导出\n' + summary
        self.assertEqual(validate_report.validate_case_counts(report, data, analysis()), [])
        errors = validate_report.validate_case_counts(report.replace('需求验收用例数量：1', '需求验收用例数量：2'), data, analysis())
        self.assertTrue(any('与用例材料不一致' in e for e in errors))

    def test_empty_new_input_does_not_export(self):
        raw = plan(); raw['requirements'] = []; raw['candidates'] = []
        with self.assertRaisesRegex(ValueError, 'no test cases'):
            exporter.normalize(cases(self.compile(raw)), analysis())

    def test_conflicting_equivalences_cannot_hide_distinct_conditions(self):
        raw = plan()
        one = [{'subject': 'count', 'operator': 'eq', 'value': 2}]
        two = [{'subject': 'count', 'operator': 'eq', 'value': 3}]
        base = dict(id='one', rule_id='selection', entry=raw['candidates'][0]['entry'],
            alternatives=[one, two], canonical=one, fact_ids=['fact-1'], evidence_ids=['evidence-1'], reason='明确规则')
        other = dict(base, id='two', canonical=two)
        raw['equivalences'] = [base, other]
        with self.assertRaisesRegex(ValueError, 'conflicting equivalence'):
            self.compile(raw)

    def test_export_renders_merged_data_conditions_and_constraints(self):
        raw = plan(); raw['candidates'][0]['data_groups'].append({'request.orderNo': 'C', 'record.orderNo': 'D'})
        rows = exporter.normalize(cases(self.compile(raw)), analysis())['purposes']['acceptance']
        self.assertIn('request.orderNo="A"', rows[0][2])
        self.assertIn('request.orderNo="C"', rows[0][2])
        self.assertIn('不等于', rows[0][2])
        self.assertIn('来源为 "record.orderNo"', rows[0][4])


if __name__ == '__main__':
    unittest.main()
