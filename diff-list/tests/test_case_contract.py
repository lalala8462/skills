import copy
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))

from case_contract import PURPOSES,PURPOSE_NAMES,validate_cases


def analysis():
    return dict(analysis_id='analysis-1',projects=[dict(name='sample',facts=[
        dict(id='fact-1',kind='method_change',data={'symbol':'sample.Flow#run()'},evidence_ids=['evidence-1'])
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
        fact_ids=['fact-1'],
        evidence_ids=['evidence-1']))
    return dict(schema_version=3,analysis_id='analysis-1',purpose_cases=groups)


class CaseContractTest(unittest.TestCase):
    def test_four_purposes_are_fixed(self):
        self.assertEqual(PURPOSES,('acceptance','core_regression','related_regression','boundary'))
        self.assertEqual(len(PURPOSE_NAMES),4)

    def test_valid_direct_cases_pass(self):
        self.assertEqual(len(validate_cases(cases(),analysis())),1)

    def test_only_schema_version_three_is_accepted(self):
        data=cases();data['schema_version']=2
        with self.assertRaisesRegex(ValueError,'schema_version=3'):
            validate_cases(data,analysis())

    def test_unknown_fact_and_unrelated_evidence_are_rejected(self):
        data=cases();data['purpose_cases']['acceptance'][0]['fact_ids']=['missing']
        with self.assertRaisesRegex(ValueError,'unknown case fact'):
            validate_cases(data,analysis())
        data=cases();data['purpose_cases']['acceptance'][0]['evidence_ids']=['other']
        with self.assertRaisesRegex(ValueError,'belong to referenced facts'):
            validate_cases(data,analysis())

    def test_extra_fields_and_duplicate_case_ids_are_rejected(self):
        data=cases();data['purpose_cases']['acceptance'][0]['extra']='x'
        with self.assertRaisesRegex(ValueError,'unsupported fields'):
            validate_cases(data,analysis())
        data=cases();data['purpose_cases']['acceptance'].append(copy.deepcopy(data['purpose_cases']['acceptance'][0]))
        with self.assertRaisesRegex(ValueError,'duplicate or multiline case_id'):
            validate_cases(data,analysis())


if __name__=='__main__':
    unittest.main()
