import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))

from delivery_preflight import case_shape_errors, report_data_errors
from review_workbench import build_navigation_facets


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
        data=dict(
            requirements='修改退款规则',
            ddl='无',
            dml='无',
            flow='退款请求进入状态判断',
            mermaid='~~~mermaid\ngraph TD\n A-->B\n~~~',
            special_rules='无',
            existing_impact='无')
        errors=report_data_errors(data)
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


if __name__=='__main__':
    unittest.main()
