import copy
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))

from generate_report import AGENT_HEADINGS
from review_receipts import empty_receipts, receipt_counts, validate_receipts


def fixture():
    analysis=dict(analysis_id='snapshot-1',projects=[dict(
        name='sample',
        facts=[dict(id='F-1',kind='method_change',
                    evidence_ids=['E-1'],data=dict(symbol='sample.Flow#run()'))],
        relations=[dict(id='R-1',source='sample.Flow#run()',
                        evidence_ids=['E-1'],status='unresolved')])])
    review=dict(analysis_id='snapshot-1',projects=[dict(
        name='sample',review_units=[dict(id='RU-1',scope='method',
            fact_ids=['F-1'],direct_fact_ids=['F-1'],
            relation_ids=['R-1'])])])
    context=[dict(id='E-1',project='sample',snippet='source')]
    report='\n\n'.join(heading+'\n'+(
        '既有功能影响：旧调用方需要回归验证' if heading=='## 4. 特殊逻辑与测试关注点'
        else '业务变化需测试') for heading in AGENT_HEADINGS)
    return analysis,review,context,report


def complete(analysis,review):
    data=empty_receipts(analysis,review)
    for entry in data['sections'].values():
        entry.update(status='assessed',unit_ids=['RU-1'],
                     fact_ids=['F-1'],evidence_ids=['E-1'])
    data['audit_unresolved_by_unit']['RU-1'].update(
        status='assessed',fact_ids=['F-1'],evidence_ids=['E-1'])
    return data


class ReviewReceiptTests(unittest.TestCase):
    def test_template_is_pending_and_checks_analysis_identity(self):
        analysis,review,context,report=fixture()
        data=empty_receipts(analysis,review)
        self.assertEqual(receipt_counts(data)['section_pending'],7)
        self.assertEqual(receipt_counts(data)['audit_unresolved_unit_pending'],1)
        with self.assertRaisesRegex(ValueError,'尚未完成'):
            validate_receipts(data,analysis,review,context,report)

    def test_completed_receipts_are_structurally_valid(self):
        analysis,review,context,report=fixture()
        data=complete(analysis,review)
        result=validate_receipts(data,analysis,review,context,report)
        self.assertEqual(result['section_pending'],0)
        self.assertEqual(result['audit_unresolved_unit_pending'],0)

    def test_wrong_unit_fact_or_evidence_is_rejected(self):
        analysis,review,context,report=fixture()
        broken=complete(analysis,review)
        broken['sections'][AGENT_HEADINGS[0]]['fact_ids']=['F-other']
        with self.assertRaisesRegex(ValueError,'缺少属于自身的 fact'):
            validate_receipts(broken,analysis,review,context,report)
        broken=complete(analysis,review)
        broken['sections'][AGENT_HEADINGS[0]]['evidence_ids']=['E-other']
        with self.assertRaisesRegex(ValueError,'缺少关联 evidence'):
            validate_receipts(broken,analysis,review,context,report)
        broken=complete(analysis,review)
        broken['sections'][AGENT_HEADINGS[0]]['unit_ids']=['RU-other']
        with self.assertRaisesRegex(ValueError,'不存在的 unit'):
            validate_receipts(broken,analysis,review,context,report)

    def test_explicit_no_impact_still_needs_source_reference(self):
        analysis,review,context,report=fixture()
        report=report.replace('既有功能影响：旧调用方需要回归验证','既有功能影响：无')
        data=complete(analysis,review)
        self.assertEqual(validate_receipts(data,analysis,review,context,report)['section_pending'],0)
        data['sections']['existing_impact']=dict(status='not_applicable',
                                                   unit_ids=[],fact_ids=[],evidence_ids=[])
        with self.assertRaisesRegex(ValueError,'报告是否为'):
            validate_receipts(data,analysis,review,context,report)

    def test_deferred_is_explicitly_unverified_without_fake_evidence(self):
        analysis,review,context,report=fixture()
        data=complete(analysis,review)
        row=data['audit_unresolved_by_unit']['RU-1']
        row.update(status='deferred',fact_ids=[],evidence_ids=[])
        counts=validate_receipts(data,analysis,review,context,report)
        self.assertEqual(counts['audit_unresolved_unit_deferred'],1)
        self.assertEqual(counts['audit_unresolved_unit_pending'],0)
        row['fact_ids']=['F-1']
        with self.assertRaisesRegex(ValueError,'deferred 不得伪装'):
            validate_receipts(data,analysis,review,context,report)

    def test_missing_audit_disposition_or_invalid_relation_is_rejected(self):
        analysis,review,context,report=fixture()
        broken=complete(analysis,review)
        broken['audit_unresolved_by_unit'].clear()
        with self.assertRaisesRegex(ValueError,'分析范围不一致'):
            validate_receipts(broken,analysis,review,context,report)
        broken=complete(analysis,review)
        broken['audit_unresolved_by_unit']['RU-1']['relation_ids']=['R-other']
        with self.assertRaisesRegex(ValueError,'关系 ID'):
            validate_receipts(broken,analysis,review,context,report)

    def test_no_section_must_have_not_applicable_status_without_fake_source(self):
        analysis,review,context,report=fixture()
        heading=AGENT_HEADINGS[1]
        report=report.replace(heading+'\n业务变化需测试',heading+'\n无')
        broken=complete(analysis,review)
        with self.assertRaisesRegex(ValueError,'报告是否为'):
            validate_receipts(broken,analysis,review,context,report)
        broken['sections'][heading]=dict(status='not_applicable',
                                           unit_ids=[],fact_ids=[],evidence_ids=[])
        self.assertEqual(validate_receipts(broken,analysis,review,context,report)['section_pending'],0)


if __name__=='__main__':
    unittest.main()
