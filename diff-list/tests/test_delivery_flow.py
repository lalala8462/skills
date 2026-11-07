import json
import io
from types import SimpleNamespace
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
from generate_report import AGENT_PLACEHOLDER, AGENT_HEADINGS, DRAFT_MARKER, DRAFT_STATUS, initial_report, refresh_report
import review_query as query_tool
from review_query import query_material
from analysis_engine import VERSION
from validate_report import without_draft_status
from review_workbench import build_navigation_facets
import run_delivery as delivery
from run_delivery import _close_current_agent_phase, _record_stage_attempt


class DeliveryFlowTest(unittest.TestCase):
    def test_navigation_facets_keep_high_value_and_hide_common_calls(self):
        units=[
            dict(id='RU-1',symbol='sample.InventoryService#add()'),
            dict(id='RU-2',symbol='sample.InventoryService#remove()')
        ]
        facets=[
            dict(id='RF-config',type='config',anchor='inventory.threshold',
                 unit_ids=['RU-1','RU-2'],unit_count=2),
            dict(id='RF-owner',type='owner',anchor='sample.InventoryService',
                 unit_ids=['RU-1','RU-2'],unit_count=2),
            dict(id='RF-business',type='direct_call',anchor='sample.StockManager#reserve()',
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

    def test_report_template_requires_real_existing_feature_impact_decision(self):
        template=(ROOT/'assets'/'diff-list-template.md').read_text(encoding='utf-8')
        sha='a'*40
        analysis={'projects':[{'name':'sample','facts':[],
                              'git':{'selection':[{'type':'commit','sha':sha}]}}]}
        report=initial_report(template,analysis)
        self.assertIn('**既有功能影响：** '+AGENT_PLACEHOLDER,report)
        errors=report_errors(report,template)
        self.assertTrue(any('既有功能影响' in error for error in errors))
        completed=report.replace(
            '**既有功能影响：** '+AGENT_PLACEHOLDER,
            '**既有功能影响：** 已有退款查询流程不变')
        errors=report_errors(completed,template)
        self.assertFalse(any('既有功能影响' in error for error in errors))
        self.assertEqual(completed.count('**既有功能影响：**'),1)

    def test_report_refresh_preserves_existing_function_impact(self):
        template=(ROOT/'assets'/'diff-list-template.md').read_text(encoding='utf-8')
        sha='a'*40
        analysis={'projects':[{'name':'sample','facts':[],
                              'git':{'selection':[{'type':'commit','sha':sha}]}}]}
        original=initial_report(template,analysis)
        edited=original.replace(
            '**既有功能影响：** '+AGENT_PLACEHOLDER,
            '**既有功能影响：** 原有退款查询接口受影响，应验证查询结果')
        for heading in AGENT_HEADINGS:
            if heading=='## 4. 特殊逻辑与测试关注点':
                continue
            edited=edited.replace(heading+'\n\n'+AGENT_PLACEHOLDER,
                                  heading+'\n\n无',1)
        cases={'purpose_cases':{
            'acceptance':[],'core_regression':[],
            'related_regression':[],'boundary':[]}}
        refreshed=refresh_report(edited,template,analysis,cases,None)
        self.assertIn('**既有功能影响：** 原有退款查询接口受影响',refreshed)
        self.assertNotIn(AGENT_PLACEHOLDER,refreshed)
        self.assertEqual(refreshed.count('**既有功能影响：**'),1)
        self.assertEqual(refreshed.count('报告状态：未完成'),1)
        # Agent 抄入业务正文时没有保留机器标记，刷新自动恢复。
        raw=edited.replace(DRAFT_MARKER+'\n'+DRAFT_STATUS,'')
        self.assertFalse(any('草稿标记' in issue for issue in report_errors(raw,template)))
        restored=refresh_report(raw,template,analysis,cases,None)
        self.assertEqual(restored.count(DRAFT_MARKER),1)
        self.assertIn('**既有功能影响：** 原有退款查询接口受影响',restored)
        self.assertEqual(without_draft_status(restored).count(DRAFT_MARKER),0)
        self.assertEqual(without_draft_status(raw),raw)
        with self.assertRaisesRegex(ValueError,'重复'):
            without_draft_status(restored+'\n'+DRAFT_MARKER+'\n'+DRAFT_STATUS)
        with self.assertRaisesRegex(ValueError,'不一致'):
            without_draft_status(DRAFT_MARKER)


    def test_coverage_wait_uses_analysis_authoring_not_old_phase(self):
        with tempfile.TemporaryDirectory() as td:
            run=Path(td)/'run';work=run/'.work';work.mkdir(parents=True)
            state_path=work/'run-state.json'
            state=dict(run_schema_version=delivery.RUN_SCHEMA_VERSION,
                run_dir=str(run.resolve()),status='waiting_coverage_review',
                waiting_started_epoch=time.time(),
                current_agent_phase='analysis_authoring',
                current_agent_phase_started_epoch=time.time(),stages={})
            delivery._write_state(state_path,state)
            with self.assertRaisesRegex(ValueError,'coverage'):
                delivery._switch_agent_phase(str(run),'source_review')
            self.assertEqual(delivery._switch_agent_phase(str(run),'analysis_authoring'),0)

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
                    'direct_fact_ids':['F-1'],'context_fact_ids':['F-2'],
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

        # 默认只返回轻量索引；详细 facts 按页读取，不能静默截断。
        args=SimpleNamespace(fact=None,evidence=None,audit=False,overview=False,
            query='originalTradeNo',unit=None,view='index',limit=1,cursor=None,
            source_lines=8)
        compact=query_tool._render_request(args,analysis,review,context)
        self.assertNotIn('facts',compact)
        self.assertEqual(compact['page']['total'],1)
        self.assertEqual(compact['items'][0]['unit_id'],'RU-1')
        self.assertEqual(compact['items'][0]['direct_fact_ids'],['F-1'])
        self.assertEqual(compact['items'][0]['direct_evidence_ids'],['E-1'])
        self.assertEqual(compact['items'][0]['context_fact_count'],1)
        args.view='facts'
        first_page=query_tool._render_request(args,analysis,review,context)
        self.assertEqual(first_page['page']['total'],2)
        self.assertEqual(first_page['page']['returned'],1)
        self.assertFalse(first_page['page']['complete'])
        args.cursor=first_page['page']['next_cursor']
        second_page=query_tool._render_request(args,analysis,review,context)
        self.assertTrue(second_page['page']['complete'])
        self.assertNotEqual(first_page['items'][0]['id'],second_page['items'][0]['id'])
        args.matched_only=True
        args.cursor=None
        exact=query_tool._render_request(args,analysis,review,context)
        self.assertEqual(exact['page']['total'],1)
        self.assertFalse(exact['category_is_exhaustive'])
        self.assertEqual(exact['items'][0]['id'],'F-1')
        self.assertEqual(exact['related_unit_ids'],['RU-1'])
        self.assertEqual(exact['selected_totals']['facts_count'],2)
        args.view='relations'
        with self.assertRaisesRegex(ValueError,'matched-only'):
            query_tool._render_request(args,analysis,review,context)

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
        context=[dict(id='E-'+str(i),snippet='code',
                      project='different' if i==2 else 'sample') for i in (1,2,3,4)]
        result=query_tool.audit_material(analysis,review,context)
        self.assertEqual(result['counts']['missing_seeds'],1)
        self.assertEqual(result['counts']['unassigned_primary_facts'],1)
        self.assertEqual(result['counts']['unindexed_facts'],2)
        self.assertEqual(result['counts']['unresolved_relations'],1)
        self.assertEqual(result['counts']['hard_errors'],1)
        self.assertEqual(result['counts']['unreferenced_evidence'],1)
        self.assertEqual(result['counts']['evidence_project_mismatch'],1)
        self.assertTrue(result['requires_attention'])
        self.assertIn('不证明业务场景完整',result['note'])

    def test_audit_distinguishes_retained_configuration_from_direct_change(self):
        analysis=dict(schema_version=2,analyzer_version=VERSION,analysis_id='a1',
            projects=[dict(name='sample',change_seeds=['sample.Service#changed()'],
                facts=[
                    dict(id='F-context',kind='configuration',data=dict(
                        change='retained_or_consumers_changed',key='test.enabled'),
                        evidence_ids=['E-1']),
                    dict(id='F-direct',kind='configuration_file_change',data=dict(
                        change='modified',key='test.enabled'),evidence_ids=['E-1'])],
                relations=[
                    dict(id='R-context',source='sample.Service#unchanged()',
                         targets=[],status='unresolved',reason='unknown',
                         evidence_ids=['E-1']),
                    dict(id='R-direct',source='sample.Service#changed()',
                         targets=[],status='unresolved',reason='unknown',
                         evidence_ids=['E-1'])],hard_errors=[])],
            cross_project_relations=[])
        review=dict(review_schema_version=4,analysis_id='a1',
            projects=[dict(name='sample',review_units=[],review_facets=[],
                           navigation_facets=[])])
        context=[dict(id='E-1',project='sample',snippet='configuration')]
        audit=query_tool.audit_material(analysis,review,context)
        self.assertEqual(audit['counts']['unassigned_primary_facts'],1)
        self.assertEqual(audit['counts']['unindexed_facts'],2)
        self.assertEqual(audit['scope_tiers']['supporting_unindexed'],1)
        self.assertEqual(audit['scope_tiers']['change_touched_unresolved'],1)
        self.assertEqual(audit['scope_tiers']['supporting_unresolved'],1)
        indexes=query_tool._indexes(analysis,review,context)
        rows=query_tool._audit_rows(analysis,indexes)['unresolved_relations']
        self.assertEqual([row['id'] for row in rows],['R-direct','R-context'])

    def test_background_only_audit_does_not_claim_direct_change_risk(self):
        analysis=dict(schema_version=2,analyzer_version=VERSION,analysis_id='a2',
            projects=[dict(name='sample',change_seeds=[],facts=[
                dict(id='F-context',kind='configuration',data=dict(
                    change='retained_or_consumers_changed'),evidence_ids=['E-1'])],
                relations=[dict(id='R-context',status='unresolved',
                    source='sample.Other#read()',targets=[],evidence_ids=['E-1'])],
                hard_errors=[])],cross_project_relations=[])
        review=dict(review_schema_version=4,analysis_id='a2',
            projects=[dict(name='sample',review_units=[],review_facets=[],
                           navigation_facets=[])])
        context=[dict(id='E-1',project='sample',snippet='existing read')]
        result=query_tool.audit_material(analysis,review,context)
        self.assertFalse(result['requires_attention'])
        self.assertEqual(result['scope_tiers']['supporting_unresolved'],1)
        self.assertEqual(result['counts']['unresolved_relations'],1)

    def test_lookup_does_not_silently_drop_missing_fact_ids(self):
        analysis=dict(schema_version=2,analyzer_version=VERSION,analysis_id='a1',
            projects=[dict(name='sample',facts=[],relations=[],change_seeds=[])],
            cross_project_relations=[])
        review=dict(review_schema_version=4,analysis_id='a1',
            projects=[dict(name='sample',review_facets=[],navigation_facets=[],
                           review_units=[dict(id='RU-1',scope='method',
                           symbol='sample.Service#run()',fact_ids=['F-missing'],
                           relation_ids=[],facet_ids=[])])])
        audit=query_tool.audit_material(analysis,review,[])
        self.assertGreater(audit['counts']['invalid_unit_references'],0)
        with self.assertRaisesRegex(ValueError,'未知 facts'):
            query_tool.query_material(analysis,review,[],unit_id='RU-1')
        indexed=query_tool._unit_index(dict(review['projects'][0]['review_units'][0],
            _project='sample'),query_tool._indexes(analysis,review,[]))
        self.assertEqual(indexed['invalid_reference_count'],1)

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
        valid_cursor=query_tool._paginate(rows,'a1','facts',2,None)[1]['next_cursor']
        with self.assertRaisesRegex(ValueError,'cursor'):
            query_tool._paginate(rows,'a1','relations',2,valid_cursor)
        altered=[dict(row,changed=True) if row['id']=='F-2' else row for row in rows]
        with self.assertRaisesRegex(ValueError,'cursor'):
            query_tool._paginate(altered,'a1','facts',2,valid_cursor)

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
                waiting_reason='initial_business_review',current_agent_phase='analysis_authoring',
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
            self.assertGreaterEqual(after['stages']['agent_analysis_authoring']['elapsed_seconds'],4.9)
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

    def test_real_preflight_failure_preserves_script_attempt_and_rework(self):
        with tempfile.TemporaryDirectory() as td:
            run=Path(td)/'run';work=run/'.work';work.mkdir(parents=True)
            start=time.time()-0.2
            state=dict(run_schema_version=delivery.RUN_SCHEMA_VERSION,
                run_dir=str(run.resolve()),status='waiting_agent',
                waiting_started_epoch=start,waiting_started_at=delivery._iso(start),
                waiting_reason='initial_business_review',current_agent_phase='source_review',
                current_agent_phase_started_epoch=start,
                current_agent_phase_started_at=delivery._iso(start),stages={})
            state_path=work/'run-state.json'
            delivery._write_state(state_path,state)
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError,'preflight'):
                    delivery._resume(str(run))
            after=json.loads(state_path.read_text(encoding='utf-8'))
            self.assertEqual(after['status'],'waiting_rework')
            self.assertEqual(after['stages']['preflight']['status'],'failed')
            self.assertEqual(len(after['stages']['preflight']['attempts']),1)
            self.assertNotEqual(after['stages']['preflight']['returncode'],0)
            self.assertEqual(after['current_agent_phase'],'rework')
            self.assertGreater(after['stages']['agent_between_steps']['elapsed_seconds'],0)

    def test_agent_rework_phase_cannot_be_misclassified(self):
        with tempfile.TemporaryDirectory() as td:
            run=Path(td)/'run';work=run/'.work';work.mkdir(parents=True)
            state_path=work/'run-state.json'
            state=dict(run_schema_version=delivery.RUN_SCHEMA_VERSION,
                run_dir=str(run.resolve()),status='waiting_rework',
                waiting_started_epoch=time.time(),current_agent_phase='rework',
                current_agent_phase_started_epoch=time.time(),stages={})
            delivery._write_state(state_path,state)
            with self.assertRaisesRegex(ValueError,'修复阶段'):
                delivery._switch_agent_phase(str(run),'analysis_authoring')
            after=json.loads(state_path.read_text(encoding='utf-8'))
            self.assertEqual(after['current_agent_phase'],'rework')
            self.assertNotIn('agent_analysis_authoring',after['stages'])

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

    def test_obsolete_timer_phase_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            run=Path(td)/'run';work=run/'.work';work.mkdir(parents=True)
            state_path=work/'run-state.json'
            state=dict(run_schema_version=delivery.RUN_SCHEMA_VERSION,
                run_dir=str(run.resolve()),status='waiting_agent',
                waiting_started_epoch=time.time(),stages={})
            delivery._write_state(state_path,state)
            with self.assertRaisesRegex(ValueError,'未知 Agent 阶段'):
                delivery._switch_agent_phase(str(run),'business_reasoning')

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

    def test_default_run_directory_is_outside_current_project(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict('os.environ',{'LOCALAPPDATA':td}), \
                 mock.patch.object(delivery,'_prepare',return_value=0) as prepare:
                self.assertEqual(delivery.main(['--repo','example','--rev','sha']),0)
                args=prepare.call_args.args[0]
                self.assertEqual(Path(args.runs_root),
                                 Path(td)/'diff-list'/'deliveries')

    def test_finalize_keeps_work_files_and_cleans_clones_after_success(self):
        with tempfile.TemporaryDirectory() as td:
            run=Path(td)/'result';work=run/'.work';work.mkdir(parents=True)
            source_files={}
            for name in ('coverage.json','test-cases.xlsx','report.md'):
                path=work/name
                path.write_bytes(('sample-'+name).encode('utf-8'))
                source_files[name]=path
            analysis=work/'analysis'/'analysis.json'
            analysis.parent.mkdir()
            analysis.write_text('{}',encoding='utf-8')
            review=work/'review.json';cases=work/'test-cases.json'
            state_path=work/'run-state.json'
            state=dict(run_schema_version=delivery.RUN_SCHEMA_VERSION,
                       run_dir=str(run.resolve()),status='resuming',stages={})
            delivery._write_state(state_path,state)
            with mock.patch.object(delivery.subprocess,'run',
                                   return_value=SimpleNamespace(returncode=0)), \
                 mock.patch.object(delivery,'cleanup_analysis_clones',return_value=2) as cleanup:
                delivery._finalize_stage(state_path,state,run,analysis,review,cases)
            cleanup.assert_called_once_with(analysis)
            for name,source in source_files.items():
                self.assertTrue(source.exists())
                self.assertEqual((run/name).read_bytes(),source.read_bytes())
            final=json.loads(state_path.read_text(encoding='utf-8'))
            self.assertEqual(final['status'],'completed')
            self.assertEqual(final['clones_cleaned'],2)

    def test_publish_failure_keeps_original_work_files_for_resume(self):
        with tempfile.TemporaryDirectory() as td:
            run=Path(td)/'result';work=run/'.work';work.mkdir(parents=True)
            for name in ('coverage.json','test-cases.xlsx','report.md'):
                (work/name).write_text('output',encoding='utf-8')
            analysis=work/'analysis'/'analysis.json';analysis.parent.mkdir()
            analysis.write_text('{}',encoding='utf-8')
            state_path=work/'run-state.json'
            state=dict(run_schema_version=delivery.RUN_SCHEMA_VERSION,
                       run_dir=str(run.resolve()),status='resuming',stages={})
            delivery._write_state(state_path,state)
            with mock.patch.object(delivery.subprocess,'run',
                                   return_value=SimpleNamespace(returncode=0)), \
                 mock.patch.object(delivery.shutil,'copy2',
                                   side_effect=OSError('copy failed')), \
                 mock.patch.object(delivery,'cleanup_analysis_clones') as cleanup:
                with self.assertRaisesRegex(RuntimeError,'finalize'):
                    delivery._finalize_stage(state_path,state,run,analysis,
                                             work/'review.json',work/'test-cases.json')
            cleanup.assert_not_called()
            self.assertEqual(json.loads(state_path.read_text(encoding='utf-8'))['status'],'failed')
            self.assertTrue(all((work/name).exists()
                                for name in ('coverage.json','test-cases.xlsx','report.md')))

    def test_clone_cleanup_failure_is_reported_without_failing_published_delivery(self):
        with tempfile.TemporaryDirectory() as td:
            run=Path(td)/'result';work=run/'.work';work.mkdir(parents=True)
            for name in ('coverage.json','test-cases.xlsx','report.md'):
                (work/name).write_text('output',encoding='utf-8')
            analysis=work/'analysis'/'analysis.json'
            analysis.parent.mkdir()
            analysis.write_text('{}',encoding='utf-8')
            state_path=work/'run-state.json'
            state=dict(run_schema_version=delivery.RUN_SCHEMA_VERSION,
                       run_dir=str(run.resolve()),status='resuming',stages={})
            delivery._write_state(state_path,state)
            with mock.patch.object(delivery.subprocess,'run',
                                   return_value=SimpleNamespace(returncode=0)), \
                 mock.patch.object(delivery,'cleanup_analysis_clones',
                                   side_effect=ValueError('unsafe path')), \
                 contextlib.redirect_stderr(io.StringIO()):
                delivery._finalize_stage(state_path,state,run,analysis,
                                         work/'review.json',work/'test-cases.json')
            finished=json.loads(state_path.read_text(encoding='utf-8'))
            self.assertEqual(finished['status'],'completed')
            self.assertIn('unsafe path',finished['clone_cleanup_error'])
            self.assertTrue((run/'report.md').exists())

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
