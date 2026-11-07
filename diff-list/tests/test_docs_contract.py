"""确保文档中的版本、限制、查询分类与实际脚本常量一致。"""
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))

import analyze
import analysis_engine
import source_index
import review_query
import run_delivery
import case_contract
import query_metrics
import generate_report


class DocsContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guide=(ROOT/'docs'/'usage-guide.md').read_text(encoding='utf-8')
        cls.contract=(ROOT/'references'/'analysis-contract.md').read_text(encoding='utf-8')
        cls.readme=(ROOT/'README.md').read_text(encoding='utf-8')
        cls.skill=(ROOT/'SKILL.md').read_text(encoding='utf-8')
        cls.agent=(ROOT/'agents'/'openai.yaml').read_text(encoding='utf-8')

    def test_limits_match_all_defaults_and_hard_maxima(self):
        for key, default in source_index.DEFAULT_LIMITS.items():
            with self.subTest(limit=key):
                maximum=analyze.MAX_LIMITS[key]
                row='| '+chr(96)+key+chr(96)+' | '+str(default)+' | '+str(maximum)+' |'
                self.assertIn(row,self.guide)
        self.assertEqual(set(source_index.DEFAULT_LIMITS),set(analyze.MAX_LIMITS))

    def test_default_rules_must_not_be_guessed_or_trigger_repeat_analysis(self):
        for document in (self.skill,self.guide):
            with self.subTest(document='SKILL' if document is self.skill else 'usage-guide'):
                for requirement in ('默认使用通用规则',
                                    '不要主动搜索、猜测或补传项目专用',
                                    '只有用户明确提供规则文件时',
                                    '不得为尝试或补传规则对同一批 SHA 重复分析'):
                    self.assertIn(requirement,document)

    def test_navigation_and_coverage_are_not_business_scope_filters(self):
        self.assertIn('不得据此裁剪复核范围',self.contract)
        self.assertIn('不证明业务覆盖或分支覆盖',self.contract)

    def test_analyzer_and_schema_versions_match_contract(self):
        self.assertIn('analyzer_version='+analysis_engine.VERSION,self.contract)
        self.assertIn('schema_version=2',self.contract)
        self.assertIn('review_schema_version='+str(case_contract.REVIEW_SCHEMA_VERSION),self.contract)
        self.assertIn('schema_version='+str(case_contract.SCHEMA_VERSION),self.contract)
        self.assertIn('coverage_schema_version=3',self.contract)
        self.assertEqual(query_metrics.RUN_SCHEMA_VERSION,run_delivery.RUN_SCHEMA_VERSION)
        self.assertIn('run_schema_version='+str(run_delivery.RUN_SCHEMA_VERSION),self.contract)

    def test_query_options_audit_modes_and_agent_phases(self):
        for option in review_query.VIEW_CHOICES+review_query.AUDIT_VIEWS:
            with self.subTest(option=option):
                self.assertIn(chr(96)+option+chr(96),self.guide)
        for phase in run_delivery.AGENT_PHASES:
            with self.subTest(phase=phase):
                self.assertIn(chr(96)+phase+chr(96),self.guide)
                self.assertIn(chr(96)+'agent_'+phase+chr(96),self.contract)
        self.assertIn('page.offset',self.contract)

    def test_report_headings_and_hard_rules_are_grounded(self):
        for heading in generate_report.AGENT_HEADINGS+generate_report.MACHINE_HEADINGS:
            with self.subTest(heading=heading):
                self.assertIn(chr(96)+heading+chr(96),self.contract)
        for required in ('禁止 '+chr(96)+'graph TD'+chr(96),
                         'surface', 'target', 'assertion',
                         '待确认', '待核实', '代码依据', '证据编号', '源码行号',
                         'Unix', 'UNC', 'Excel'):
            self.assertIn(required,self.guide)

    def test_stats_fields_and_output_terminology(self):
        for key in query_metrics.summarize_queries('.').keys():
            if key=='read_error':
                continue
            with self.subTest(field=key):
                self.assertIn(chr(96)+key+chr(96),self.contract)
        self.assertIn('终端打印',self.guide)
        self.assertIn('TRACKED_TOTAL = SCRIPT_TOTAL + AGENT_TOTAL',self.guide)
        for text in (self.readme,self.agent):
            for product in ('Markdown','Excel','coverage.json'):
                self.assertIn(product,text)


if __name__=='__main__':
    unittest.main()
