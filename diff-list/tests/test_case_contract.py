import copy
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))

from case_contract import PURPOSES,PURPOSE_NAMES,coverage_matrix,resolve_case_sources,validate_cases


def analysis():
    return dict(analysis_id='analysis-1',projects=[dict(name='sample',facts=[
        dict(id='fact-1',kind='method_change',data={'symbol':'sample.Flow#run()'},evidence_ids=['evidence-1']),
        dict(id='fact-2',kind='method_change',data={'symbol':'sample.Flow#other()'},evidence_ids=['evidence-2'])
    ])])


def review():
    return dict(analysis_id='analysis-1',review_schema_version=4,projects=[dict(
        name='sample',
        fact_catalog=[
            dict(id='fact-1',kind='method_change',evidence_ids=['evidence-1']),
            dict(id='fact-2',kind='method_change',evidence_ids=['evidence-2'])
        ],
        review_facets=[dict(id='RF-1',type='owner',anchor='sample.Flow',
                            unit_ids=['RU-1','RU-2'],unit_count=2)],
        navigation_facets=[dict(id='RF-1',type='owner',anchor='sample.Flow',
                                unit_ids=['RU-1','RU-2'],unit_count=2,
                                navigation_priority=20,navigation_reason='同一变化类')],
        review_units=[
            dict(id='RU-1',scope='method',symbol='sample.Flow#run()',
                 change_summary=[dict(kind='method_change',fact_id='fact-1',details={'change':'modified'})],
                 direct_fact_ids=['fact-1'],context_fact_ids=[],
                 fact_ids=['fact-1'],relation_ids=[],related_symbols=[],facet_ids=['RF-1'],navigation_facet_ids=['RF-1']),
            dict(id='RU-2',scope='method',symbol='sample.Flow#other()',
                 change_summary=[dict(kind='method_change',fact_id='fact-2',details={'change':'modified'})],
                 direct_fact_ids=['fact-2'],context_fact_ids=[],
                 fact_ids=['fact-2'],relation_ids=[],related_symbols=[],facet_ids=['RF-1'],navigation_facet_ids=['RF-1'])
        ])])


def cases():
    groups={purpose:[] for purpose in PURPOSES}
    groups['acceptance'].append(dict(
        case_id='A-001',
        title='验证修改后的业务行为',
        preconditions='准备有效业务数据',
        steps=['发起请求','检查响应'],
        expected=['响应字段使用修改后的规则'],
        observable_evidence=[dict(surface='接口',target='返回体',assertion='status=SUCCESS')],
        review_unit_ids=['RU-1']))
    return dict(schema_version=4,analysis_id='analysis-1',purpose_cases=groups)


class CaseContractTest(unittest.TestCase):
    def test_four_purposes_are_fixed(self):
        self.assertEqual(PURPOSES,('acceptance','core_regression','related_regression','boundary'))
        self.assertEqual(len(PURPOSE_NAMES),4)

    def test_current_cases_pass_and_sources_expand_from_review_unit(self):
        self.assertEqual(len(validate_cases(cases(),analysis(),review())),1)
        resolved=resolve_case_sources(cases(),analysis(),review())
        self.assertEqual(resolved['A-001']['fact_ids'],['fact-1'])
        self.assertEqual(resolved['A-001']['evidence_ids'],['evidence-1'])

    def test_only_schema_version_four_is_accepted(self):
        data=cases();data['schema_version']=3
        with self.assertRaisesRegex(ValueError,'schema_version=4'):
            validate_cases(data,analysis(),review())

    def test_old_review_schema_is_rejected(self):
        old=copy.deepcopy(review());old['review_schema_version']=2
        with self.assertRaisesRegex(ValueError,'review_schema_version=4'):
            validate_cases(cases(),analysis(),old)

    def test_coverage_matrix_reports_uncovered_units_without_failing_cases(self):
        matrix=coverage_matrix(cases(),analysis(),review())
        self.assertEqual(matrix['summary']['unit_count'],2)
        self.assertEqual(matrix['summary']['covered_unit_count'],1)
        self.assertEqual(matrix['uncovered_unit_ids'],['RU-2'])
        self.assertEqual(matrix['summary']['partially_covered_facet_count'],1)
        self.assertEqual(matrix['partially_covered_facet_ids'],['RF-1'])
        self.assertEqual(matrix['summary']['pending_review_count'],1)
        self.assertEqual(matrix['pending_review'][0]['unit_id'],'RU-2')
        self.assertEqual(matrix['pending_review'][0]['high_value_facets'][0]['facet_id'],'RF-1')
        self.assertEqual(matrix['pending_review'][0]['candidate_case_ids'],['A-001'])
        self.assertIn('待复核',matrix['note'])

    def test_review_unit_direct_context_contract_is_strict(self):
        broken=copy.deepcopy(review())
        broken['projects'][0]['review_units'][0]['context_fact_ids']=['fact-1']
        with self.assertRaisesRegex(ValueError,'direct/context facts overlap'):
            validate_cases(cases(),analysis(),broken)

    def test_coverage_matrix_has_current_schema_identity(self):
        matrix=coverage_matrix(cases(),analysis(),review())
        self.assertEqual(matrix['coverage_schema_version'],2)
        self.assertEqual(matrix['review_schema_version'],4)
        self.assertEqual(matrix['analysis_id'],'analysis-1')

    def test_unknown_review_unit_is_rejected(self):
        data=cases();data['purpose_cases']['acceptance'][0]['review_unit_ids']=['RU-missing']
        with self.assertRaisesRegex(ValueError,'unknown review unit'):
            validate_cases(data,analysis(),review())

    def test_old_fact_and_evidence_fields_are_rejected(self):
        data=cases();case=data['purpose_cases']['acceptance'][0]
        case['fact_ids']=['fact-1']
        case['evidence_ids']=['evidence-1']
        with self.assertRaisesRegex(ValueError,'unsupported fields'):
            validate_cases(data,analysis(),review())

    def test_extra_fields_and_duplicate_case_ids_are_rejected(self):
        data=cases();data['purpose_cases']['acceptance'][0]['extra']='x'
        with self.assertRaisesRegex(ValueError,'unsupported fields'):
            validate_cases(data,analysis(),review())
        data=cases();data['purpose_cases']['acceptance'].append(copy.deepcopy(data['purpose_cases']['acceptance'][0]))
        with self.assertRaisesRegex(ValueError,'duplicate or multiline case_id'):
            validate_cases(data,analysis(),review())


if __name__=='__main__':
    unittest.main()
