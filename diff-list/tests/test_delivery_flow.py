import json
import io
import contextlib
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))

from delivery_preflight import case_shape_errors, report_errors
from generate_report import AGENT_PLACEHOLDER, initial_report
import review_query as query_tool
from review_query import query_material
from analysis_engine import VERSION
from review_workbench import build_navigation_facets
import run_delivery as delivery
from run_delivery import _close_current_agent_phase, _record_stage_attempt


class DeliveryFlowTest(unittest.TestCase):
    def test_navigation_facets_keep_high_value_and_hide_common_calls(self):
        units=[
            dict(id='RU-1',symbol='sample.PayService#pay()'),
            dict(id='RU-2',symbol='sample.PayService#refund()')
        ]
        facets=[
            dict(id='RF-config',type='config',anchor='pay.timeout',
                 unit_ids=['RU-1','RU-2'],unit_count=2),
            dict(id='RF-owner',type='owner',anchor='sample.PayService',
                 unit_ids=['RU-1','RU-2'],unit_count=2),
            dict(id='RF-business',type='direct_call',anchor='sample.OrderManager#lock()',
                 unit_ids=['RU-1','RU-2'],unit_count=2),
            dict(id='RF-common',type='direct_call',anchor='sample.common.StringUtils#trim()',
                 unit_ids=['RU-1','RU-2'],unit_count=2)
        ]
        selected=build_navigation_facets(facets,units)
        ids=[row['id'] for row in selected]
        self.assertEqual(ids,['RF-config','RF-owner','RF-business'])
        self.assertNotIn('RF-common',units[0]['navigation_facet_ids'])
        self.assertEqual(units[0]['navigation_facet_ids'],['RF-business','RF-config','RF-owner'])

    def test_preflight_rejects_graph_td_before_report_generation(self):
        template=(ROOT/'assets'/'diff-list-template.md').read_text(encoding='utf-8')
        sha='a'*40
        analysis={'projects':[{'name':'sample','facts':[],
                              'git':{'selection':[{'type':'commit','sha':sha}]}}]}
        report=initial_report(template,analysis)
        replacements={
            '### 2.1 需求改动':'修改退款规则',
            '### 2.2 表字段 DDL 修改':'无',
            '### 2.3 表配置 DML 准备清单':'无',
            '### 3.1 整体流程说明':'退款请求进入状态判断',
            '### 3.2 Mermaid 流程图':'~~~mermaid\ngraph TD\n A-->B\n~~~',
            '## 4. 特殊逻辑与测试关注点':'无'
        }
        for heading,value in replacements.items():
            report=report.replace(heading+'\n\n'+AGENT_PLACEHOLDER,heading+'\n\n'+value,1)
        errors=report_errors(report,template)
        self.assertTrue(any('graph TD' in error for error in errors))

    def test_preflight_collects_multiple_case_shape_errors(self):
        data=dict(purpose_cases=dict(
            acceptance=[dict(case_id='',title='',preconditions='',
                             steps=[],expected=[],observable_evidence=[],review_unit_ids=[])],
            core_regression=[],
            related_regression=[],
            boundary=[]))
        errors=case_shape_errors(data)
        self.assertGreaterEqual(len(errors),6)
        self.assertTrue(any('case_id' in error for error in errors))
        self.assertTrue(any('steps' in error for error in errors))
        self.assertTrue(any('observable_evidence' in error for error in errors))

    def test_review_query_expands_selected_unit_without_business_grouping(self):
        analysis={
            'schema_version':2,
            'analyzer_version':VERSION,
            'analysis_id':'fixture-analysis',
            'projects':[{
                'name':'sample',
                'facts':[
                    {'id':'F-1','kind':'field_change',
                     'data':{'field':'originalTradeNo'},'evidence_ids':['E-1']},
                    {'id':'F-2','kind':'branch',
                     'data':{'condition':'status == SUCCESS'},'evidence_ids':['E-2']}
                ],
                'relations':[]
            }],
            'cross_project_relations':[]
        }
        review={
            'review_schema_version':4,
            'analysis_id':'fixture-analysis',
            'projects':[{
                'name':'sample',
                'review_units':[{
                    'id':'RU-1','symbol':'sample.RefundService#refund()',
                    'change_summary':[],'fact_ids':['F-1','F-2'],'relation_ids':[],
                    'facet_ids':['RF-1']
                }],
                'review_facets':[{
                    'id':'RF-1','type':'field','anchor':'sample.Order#originalTradeNo',
                    'unit_ids':['RU-1']
                }],
                'navigation_facets':[]
            }]
        }
        context=[
            {'id':'E-1','snippet':'private String originalTradeNo;'},
            {'id':'E-2','snippet':'if (status == SUCCESS)'}
        ]
        result=query_material(analysis,review,context,query='originalTradeNo')
        self.assertEqual(result['matched']['unit_ids'],['RU-1'])
        self.assertEqual([row['id'] for row in result['facts']],['F-1','F-2'])
        self.assertEqual([row['id'] for row in result['evidence']],['E-1','E-2'])
        self.assertIn('未命中的事实、关系及源码仍保留',result['note'])

        context_only=[
            {'id':'E-1','snippet':'private String originalTradeNo;'},
            {'id':'E-2','snippet':'if (status == SUCCESS) { /* sourceOnlyMarker */ }'}
        ]
        context_result=query_material(analysis,review,context_only,query='sourceOnlyMarker')
        self.assertEqual(context_result['matched']['fact_ids'],['F-2'])
        self.assertEqual(context_result['matched']['unit_ids'],['RU-1'])
        self.assertEqual([row['id'] for row in context_result['facts']],['F-1','F-2'])


    def test_audit_reports_unassigned_and_unresolved_without_assuming_business_coverage(self):
        analysis=dict(schema_version=2,analyzer_version=VERSION,analysis_id='a1',
            projects=[dict(name='sample',change_seeds=[
                'sample.Service#changed()','sample.Service#missing()'],
                facts=[
                    dict(id='F-1',kind='method_change',evidence_ids=['E-1'],data={}),
                    dict(id='F-2',kind='field_change',evidence_ids=['E-2'],data={}),
                    dict(id='F-3',kind='branch',evidence_ids=['E-3'],data={})],
                relations=[
                    dict(id='R-1',source='sample.Service#changed()',status='unresolved',
                         evidence_ids=['E-1'],reason='unknown_receiver')],
                hard_errors=['changed evidence limit exceeded'])],
            cross_project_relations=[])
        review=dict(review_schema_version=4,analysis_id='a1',
            projects=[dict(name='sample',review_units=[dict(
                id='RU-1',scope='method',symbol='sample.Service#changed()',
                fact_ids=['F-1'],relation_ids=['R-1'],facet_ids=[])],
                review_facets=[],navigation_facets=[])])
        context=[dict(id='E-'+str(i),snippet='code') for i in (1,2,3)]
        result=query_tool.audit_material(analysis,review,context)
        self.assertEqual(result['counts']['missing_seeds'],1)
        self.assertEqual(result['counts']['unassigned_primary_facts'],1)
        self.assertEqual(result['counts']['unindexed_facts'],2)
        self.assertEqual(result['counts']['unresolved_relations'],1)
        self.assertEqual(result['counts']['hard_errors'],1)
        self.assertTrue(result['requires_attention'])
        self.assertIn('不证明业务场景完整',result['note'])

    def test_review_query_pagination_rejects_wrong_cursor_and_returns_all_records(self):
        rows=[dict(id='F-{}'.format(n)) for n in range(5)]
        all_ids=[]
        cursor=None
        for _ in range(3):
            items,page=query_tool._paginate(rows,'a1','facts',2,cursor)
            all_ids.extend(row['id'] for row in items)
            if not page['complete']:
                self.assertTrue(page['next_cursor'])
            cursor=page['next_cursor']
        self.assertEqual(all_ids,['F-0','F-1','F-2','F-3','F-4'])
        self.assertTrue(page['complete'])
        with self.assertRaisesRegex(ValueError,'cursor'):
            query_tool._paginate(rows,'a1','relations',2,
                query_tool._paginate(rows,'a1','facts',2,None)[1]['next_cursor'])

    def test_evidence_expansion_validates_snapshot_before_returning_source(self):
        record=dict(id='E-1',project='sample',side='result',tree='a'*40,
                    blob='b'*40,path='src/App.java',start_line=2,end_line=2,
                    snippet='String status = "OK";')
        idx=dict(evidence={'E-1':record},projects={'sample':{
            'analysis_clone':'repo','snapshots':{'result':'a'*40}}})
        with mock.patch.object(query_tool,'verify_evidence',return_value=[]), \
             mock.patch.object(query_tool,'git_bytes',return_value=
                 b'class App {\nString status = "OK";\n}\n'):
            expanded=query_tool._evidence_source({},idx,'E-1',1)
        self.assertTrue(expanded['snapshot_verified'])
        self.assertIn('String status',expanded['source']['content'])
        with mock.patch.object(query_tool,'verify_evidence',return_value=['mismatch']):
            with self.assertRaisesRegex(ValueError,'固定快照证据校验失败'):
                query_tool._evidence_source({},idx,'E-1',1)

    def test_preflight_failure_starts_rework_interval_and_resume_accounts_for_it(self):
        with tempfile.TemporaryDirectory() as td:
            run=Path(td)/'run';work=run/'.work';work.mkdir(parents=True)
            state_path=work/'run-state.json'
            start=time.time()-5
            state=dict(run_schema_version=delivery.RUN_SCHEMA_VERSION,
                run_dir=str(run.resolve()),status='waiting_agent',
                waiting_started_epoch=start,waiting_started_at=delivery._iso(start),
                waiting_reason='initial_business_review',current_agent_phase='report_edit',
                current_agent_phase_started_epoch=start,
                current_agent_phase_started_at=delivery._iso(start),stages={})
            delivery._write_state(state_path,state)
            def fail(state_file,current,name,commands,cleanup=()):
                current['status']='failed'
                current['failed_stage']=name
                delivery._write_state(state_file,current)
                raise RuntimeError('preflight failed')
            with mock.patch.object(delivery,'_run_stage',side_effect=fail):
                with self.assertRaisesRegex(RuntimeError,'preflight failed'):
                    delivery._resume(str(run))
            after=json.loads(state_path.read_text(encoding='utf-8'))
            self.assertEqual(after['status'],'waiting_rework')
            self.assertEqual(after['current_agent_phase'],'rework')
            self.assertEqual(after['waiting_reason'],'repair_preflight')
            self.assertGreaterEqual(after['stages']['agent_report_edit']['elapsed_seconds'],4.9)
            self.assertGreaterEqual(after['stages']['agent_between_steps']['elapsed_seconds'],4.9)

            # 再次 resume 失败同样会先结算上一段 rework，不会漏记失败后的间隔。
            with mock.patch.object(delivery,'_run_stage',side_effect=fail):
                with self.assertRaisesRegex(RuntimeError,'preflight failed'):
                    delivery._resume(str(run))
            after=json.loads(state_path.read_text(encoding='utf-8'))
            self.assertIn('agent_rework',after['stages'])
            total=after['stages']['agent_between_steps']['elapsed_seconds']
            subphase=sum(after['stages'].get('agent_'+p,{}).get('elapsed_seconds',0)
                         for p in delivery.AGENT_PHASES)
            self.assertAlmostEqual(total,subphase,places=2)

    def test_agent_total_is_inclusive_not_added_to_subphases(self):
        state=dict(stages=dict(
            analyze=dict(elapsed_seconds=5.0,status='completed'),
            agent_source_review=dict(elapsed_seconds=2.0),
            agent_between_steps=dict(elapsed_seconds=2.0,status='completed')))
        buffer=io.StringIO()
        with contextlib.redirect_stdout(buffer):
            delivery._print_timings(state)
        out=buffer.getvalue()
        self.assertIn('SCRIPT_TOTAL: 5.000s',out)
        self.assertIn('AGENT_TOTAL: 2.000s',out)
        self.assertIn('TRACKED_TOTAL: 7.000s',out)

    def test_agent_subphase_timing_is_recorded_separately(self):
        state={
            'stages':{},
            'current_agent_phase':'source_review',
            'current_agent_phase_started_epoch':10.0,
            'current_agent_phase_started_at':'1970-01-01T00:00:10.000Z'
        }
        _close_current_agent_phase(state,12.5)
        self.assertEqual(state['stages']['agent_source_review']['elapsed_seconds'],2.5)
        self.assertNotIn('current_agent_phase',state)

    def test_repeated_stage_timing_is_accumulated(self):
        state={'stages':{}}
        _record_stage_attempt(state,'coverage',10.0,11.25,'completed',0)
        _record_stage_attempt(state,'coverage',20.0,20.75,'completed',0)
        row=state['stages']['coverage']
        self.assertEqual(row['elapsed_seconds'],2.0)
        self.assertEqual(len(row['attempts']),2)
        self.assertEqual(row['status'],'completed')

    def test_stage_timing_rejects_noncurrent_shape(self):
        state={'stages':{'coverage':{
            'started_at':'2026-11-06T00:00:00.000Z',
            'ended_at':'2026-11-06T00:00:01.000Z',
            'elapsed_seconds':1.0,
            'status':'completed',
            'returncode':0
        }}}
        with self.assertRaisesRegex(ValueError,'缺少 attempts'):
            _record_stage_attempt(state,'coverage',20.0,20.75,'completed',0)


if __name__=='__main__':
    unittest.main()
