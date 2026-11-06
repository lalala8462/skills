import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))

from delivery_preflight import case_shape_errors, report_errors
from generate_report import AGENT_PLACEHOLDER, initial_report
from review_query import query_material
from review_workbench import build_navigation_facets
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
        self.assertIn('未命中的 analysis/review 材料仍保留',result['note'])

        context_only=[
            {'id':'E-1','snippet':'private String originalTradeNo;'},
            {'id':'E-2','snippet':'if (status == SUCCESS) { /* sourceOnlyMarker */ }'}
        ]
        context_result=query_material(analysis,review,context_only,query='sourceOnlyMarker')
        self.assertEqual(context_result['matched']['fact_ids'],['F-2'])
        self.assertEqual(context_result['matched']['unit_ids'],['RU-1'])
        self.assertEqual([row['id'] for row in context_result['facts']],['F-1','F-2'])

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


if __name__=='__main__':
    unittest.main()
