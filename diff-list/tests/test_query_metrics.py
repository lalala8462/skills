import contextlib
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))

from analysis_engine import VERSION
import query_metrics
import review_query
import run_delivery


class QueryMetricsTest(unittest.TestCase):
    def _fixture(self,root):
        run=Path(root)/'run'
        work=run/'.work'
        analysis_dir=work/'analysis'
        analysis_dir.mkdir(parents=True)
        analysis=analysis_dir/'analysis.json'
        review=work/'review.json'
        context=analysis_dir/'context.jsonl'
        analysis.write_text(json.dumps(dict(
            schema_version=2,analyzer_version=VERSION,analysis_id='analysis-fixture',
            projects=[dict(name='sample',change_seeds=['demo.Service#pay()'],
                hard_errors=[],facts=[],relations=[])],
            cross_project_relations=[],context_file='context.jsonl'
        )),encoding='utf-8')
        review.write_text(json.dumps(dict(
            review_schema_version=4,analysis_id='analysis-fixture',
            projects=[dict(name='sample',review_units=[dict(
                id='RU-1',scope='method',symbol='demo.Service#pay()',
                fact_ids=[],relation_ids=[],facet_ids=[],change_summary=[])],
                review_facets=[],navigation_facets=[])]
        )),encoding='utf-8')
        context.write_text('',encoding='utf-8')
        state_path=work/'run-state.json'
        run_delivery._write_state(state_path,dict(
            run_schema_version=run_delivery.RUN_SCHEMA_VERSION,
            run_dir=str(run.resolve()),status='waiting_agent',stages={}))
        return run,analysis,review,state_path

    def test_query_metrics_record_success_failure_repeat_and_output_size(self):
        with tempfile.TemporaryDirectory() as td:
            run,analysis,review,state_path=self._fixture(td)
            base=['--analysis',str(analysis),'--review',str(review)]
            with contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(review_query.main(base+['--overview']),0)
                self.assertEqual(review_query.main(base+['--overview']),0)
                self.assertEqual(review_query.main(base+['--query','privateValueOnly']),0)
                self.assertEqual(review_query.main(base+['--audit']),0)
                self.assertEqual(review_query.main(base+['--unit','RU-unknown']),2)
            metrics_file=run/'.work'/'query-metrics.jsonl'
            data=metrics_file.read_text(encoding='utf-8')
            self.assertNotIn('privateValueOnly',data)
            summary=query_metrics.summarize_queries(run/'.work')
            self.assertEqual(summary['calls'],5)
            self.assertEqual(summary['successes'],4)
            self.assertEqual(summary['failures'],1)
            self.assertEqual(summary['distinct_requests'],4)
            self.assertEqual(summary['repeated_calls'],1)
            self.assertEqual(summary['repeated_by_mode'],{'overview':1})
            self.assertEqual(summary['by_mode'],dict(
                audit=1,overview=2,query=1,unit=1))
            self.assertGreater(summary['returned_bytes'],0)
            self.assertGreater(summary['stdout_bytes'],0)
            self.assertGreater(summary['returned_items'],0)
            # 统计通过下一次编排状态写入汇总；查询本身不修改 run-state。
            existing=json.loads(state_path.read_text(encoding='utf-8'))
            self.assertEqual(existing['query_stats']['calls'],0)
            run_delivery._write_state(state_path,existing)
            updated=json.loads(state_path.read_text(encoding='utf-8'))
            self.assertEqual(updated['query_stats']['calls'],5)
            self.assertEqual(updated['query_stats']['repeated_calls'],1)

    def test_file_output_counts_generated_json_and_only_path_printed(self):
        with tempfile.TemporaryDirectory() as td:
            run,analysis,review,_=self._fixture(td)
            output=run/'.work'/'query-result.json'
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(review_query.main([
                    '--analysis',str(analysis),'--review',str(review),
                    '--overview','--output',str(output)]),0)
            summary=query_metrics.summarize_queries(run/'.work')
            self.assertEqual(summary['calls'],1)
            self.assertGreater(summary['returned_bytes'],summary['stdout_bytes'])
            self.assertEqual(summary['returned_items'],1)
            self.assertEqual(summary['invalid_events'],0)

    def test_duplicate_calls_view_is_removed(self):
        with tempfile.TemporaryDirectory() as td:
            _,analysis,review,_=self._fixture(td)
            with contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()) as error:
                status=review_query.main([
                    '--analysis',str(analysis),'--review',str(review),
                    '--query','access','--view','calls'])
            self.assertEqual(status,2)
            self.assertIn('ERROR',error.getvalue())

    def test_repeat_identity_accounts_for_cursor_and_view(self):
        args=SimpleNamespace(
            overview=False,audit=False,query='fieldFromSource',unit=None,
            fact=None,evidence=None,view='facts',cursor=None,limit=10,
            source_lines=8,output=None)
        first=query_metrics.request_hash(args,'analysis-1')
        args.cursor='next-page'
        self.assertNotEqual(first,query_metrics.request_hash(args,'analysis-1'))
        args.cursor=None
        args.view='relations'
        self.assertNotEqual(first,query_metrics.request_hash(args,'analysis-1'))
        args.view='facts'
        args.matched_only=True
        self.assertNotEqual(first,query_metrics.request_hash(args,'analysis-1'))

    def test_invalid_telemetry_event_does_not_break_summary(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'query-metrics.jsonl'
            path.write_text('not json\n',encoding='utf-8')
            result=query_metrics.summarize_queries(td)
            self.assertEqual(result['invalid_events'],1)
            self.assertEqual(result['calls'],0)

    def test_unrelated_analysis_does_not_write_to_run(self):
        with tempfile.TemporaryDirectory() as td:
            run,analysis,review,_=self._fixture(td)
            self.assertIsNotNone(query_metrics.metrics_path(
                analysis,review,run_delivery.RUN_SCHEMA_VERSION))
            self.assertIsNone(query_metrics.metrics_path(
                analysis,run/'unrelated-review.json',run_delivery.RUN_SCHEMA_VERSION))
            self.assertIsNone(query_metrics.metrics_path(
                analysis,review,run_delivery.RUN_SCHEMA_VERSION-1))
            state_path=run/'.work'/'run-state.json'
            state=json.loads(state_path.read_text(encoding='utf-8'))
            state['status']='completed'
            run_delivery._write_state(state_path,state)
            self.assertIsNone(query_metrics.metrics_path(
                analysis,review,run_delivery.RUN_SCHEMA_VERSION))


if __name__=='__main__':
    unittest.main()
