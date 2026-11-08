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
                                navigation_priority=20,navigation_reason='同一变化类',
                                priority_kind='reading_order_heuristic')],
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
        review_unit_ids=['RU-1'],
        unit_assertions=[dict(unit_id='RU-1',direct_fact_ids=['fact-1'],observation_indexes=[0])]))
    return dict(schema_version=5,analysis_id='analysis-1',purpose_cases=groups)


class CaseContractTest(unittest.TestCase):
    def test_four_purposes_are_fixed(self):
        self.assertEqual(PURPOSES,('acceptance','core_regression','related_regression','boundary'))
        self.assertEqual(len(PURPOSE_NAMES),4)

    def test_current_cases_pass_and_sources_expand_from_review_unit(self):
        self.assertEqual(len(validate_cases(cases(),analysis(),review())),1)
        resolved=resolve_case_sources(cases(),analysis(),review())
        self.assertEqual(resolved['A-001']['fact_ids'],['fact-1'])
        self.assertEqual(resolved['A-001']['evidence_ids'],['evidence-1'])

    def test_only_schema_version_five_is_accepted(self):
        data=cases();data['schema_version']=4
        with self.assertRaisesRegex(ValueError,'schema_version=5'):
            validate_cases(data,analysis(),review())

    def test_old_review_schema_is_rejected(self):
        old=copy.deepcopy(review());old['review_schema_version']=2
        with self.assertRaisesRegex(ValueError,'review_schema_version=4'):
            validate_cases(cases(),analysis(),old)

    def test_coverage_matrix_reports_uncovered_units_without_failing_cases(self):
        matrix=coverage_matrix(cases(),analysis(),review())
        self.assertEqual(matrix['summary']['unit_count'],2)
        self.assertEqual(matrix['summary']['referenced_unit_count'],1)
        self.assertEqual(matrix['unit_case_references'][0]['referenced_by_cases'],True)
        self.assertNotIn('declared_case_ids',matrix['unit_case_references'][0])
        self.assertEqual(matrix['unit_case_references'][0]['primary_assertion_links'],[
            dict(case_id='A-001',direct_fact_ids=['fact-1'],observation_indexes=[0])])
        self.assertEqual(matrix['summary']['referenced_facet_count'],1)
        self.assertEqual(matrix['summary']['all_units_referenced_facet_count'],0)
        self.assertNotIn('fully_covered_facet_count',matrix['summary'])
        self.assertEqual(matrix['unreferenced_unit_ids'],['RU-2'])
        self.assertEqual(matrix['summary']['partially_referenced_facet_count'],1)
        self.assertEqual(matrix['partially_referenced_facet_ids'],['RF-1'])
        self.assertEqual(matrix['summary']['pending_review_count'],1)
        self.assertEqual(matrix['pending_review'][0]['unit_id'],'RU-2')
        self.assertEqual(matrix['pending_review'][0]['high_value_facets'][0]['facet_id'],'RF-1')
        self.assertEqual(matrix['pending_review'][0]['candidate_case_ids'],['A-001'])
        self.assertIn('不证明分支或业务规则覆盖',matrix['note'])

    def test_navigation_priority_kind_is_required_not_implicitly_assumed(self):
        broken=copy.deepcopy(review())
        del broken['projects'][0]['navigation_facets'][0]['priority_kind']
        with self.assertRaisesRegex(ValueError,'not a risk score'):
            coverage_matrix(cases(),analysis(),broken)
        broken=copy.deepcopy(review())
        broken['projects'][0]['navigation_facets'][0]['priority_kind']='business_risk_score'
        with self.assertRaisesRegex(ValueError,'not a risk score'):
            coverage_matrix(cases(),analysis(),broken)

    def test_review_unit_direct_context_contract_is_strict(self):
        broken=copy.deepcopy(review())
        broken['projects'][0]['review_units'][0]['context_fact_ids']=['fact-1']
        with self.assertRaisesRegex(ValueError,'direct/context facts overlap'):
            validate_cases(cases(),analysis(),broken)

    def test_coverage_matrix_has_current_schema_identity(self):
        matrix=coverage_matrix(cases(),analysis(),review())
        self.assertEqual(matrix['coverage_schema_version'],5)
        self.assertEqual(matrix['review_schema_version'],4)
        self.assertEqual(matrix['analysis_id'],'analysis-1')

    def test_unknown_review_unit_is_rejected(self):
        data=cases();data['purpose_cases']['acceptance'][0]['review_unit_ids']=['RU-missing']
        with self.assertRaisesRegex(ValueError,'unknown review unit'):
            validate_cases(data,analysis(),review())

    def test_bulk_unit_reference_without_per_unit_primary_fact_is_rejected(self):
        data=cases()
        case=data['purpose_cases']['acceptance'][0]
        case['review_unit_ids'].append('RU-2')
        with self.assertRaisesRegex(ValueError,'every referenced review unit requires'):
            validate_cases(data,analysis(),review())
        case['unit_assertions'].append(
            dict(unit_id='RU-2',direct_fact_ids=['fact-1'],observation_indexes=[0]))
        with self.assertRaisesRegex(ValueError,'direct primary facts'):
            validate_cases(data,analysis(),review())
        case['unit_assertions'][-1]['direct_fact_ids']=['fact-2']
        case['unit_assertions'][-1]['observation_indexes']=[9]
        with self.assertRaisesRegex(ValueError,'observation_indexes'):
            validate_cases(data,analysis(),review())
        case['unit_assertions'][-1]['observation_indexes']=[0]
        result=coverage_matrix(data,analysis(),review())
        self.assertEqual(result['summary']['pending_review_count'],0)
        self.assertIn('不证明分支或业务规则覆盖',result['note'])

    def test_supporting_fact_is_not_a_primary_change_link(self):
        source=analysis()
        source['projects'][0]['facts'][0]['kind']='branch'
        with self.assertRaisesRegex(ValueError,'direct primary facts'):
            validate_cases(cases(),source,review())

    def test_unchanged_configuration_consumer_is_not_primary_change(self):
        source=analysis()
        source['projects'][0]['facts'][0].update(kind='configuration',
            data=dict(change='retained_or_consumers_changed'))
        with self.assertRaisesRegex(ValueError,'direct primary facts'):
            validate_cases(cases(),source,review())

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
