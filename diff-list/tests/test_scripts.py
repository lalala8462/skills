"""Run: py -3 -m unittest discover -s tests -v"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / "scripts"))


def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/(name+'.py'))
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DiffListTest(unittest.TestCase):
    def test_template_heading_only(self):
        template=(ROOT/'assets'/'diff-list-template.md').read_text(encoding='utf-8')
        self.assertTrue(all(not line.strip() or line.startswith('# ' ) or line.startswith('## ')
                            or line.startswith('### ') for line in template.splitlines()))
        self.assertEqual(len([x for x in template.splitlines() if x.startswith('#')]),13)

    def test_create_and_validate(self):
        init=module('init_report'); val=module('validate_report')
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(init.main(['--outdir',td,'--filename','case.md']),0)
            self.assertEqual(init.main(['--outdir',td,'--filename','case.md']),2)
            template=(ROOT/'assets'/'diff-list-template.md').read_text(encoding='utf-8')
            sha='a'*40
            sections={'## 1. 分析范围':'webapi '+sha,
                      '### 2.1 需求改动':'修改支付入口。',
                      '### 2.2 表字段 DDL 修改':'无',
                      '### 2.3 表配置 DML 准备清单':'无',
                      '### 2.4 配置文件调整':'无',
                      '### 3.1 整体流程说明':'请求后检查支付状态。',
                      '### 3.2 Mermaid 流程图':'```mermaid\nflowchart TD\n A[请求] --> B[状态]\n```',
                      '## 4. 特殊逻辑与测试关注点':'无',
                      '## 5. 重点测试场景':'**测试点：** 状态检查\n**测试前提：** 渠道成功\n**预期结果：** 更新成功',
                      '## 6. 验证情况':'仅完成静态分析。'}
            report='\n'.join(line+'\n'+sections.get(line,'') if line.startswith('#') else line
                             for line in template.splitlines())
            errors,_=val.validate(report,template,['webapi='+sha])
            self.assertEqual(errors,[])
            errors,_=val.validate(report.replace('**预期结果：**','**其他：**'),template,[])
            self.assertTrue(any('预期结果' in e for e in errors))
            errors,_=val.validate(report+'\n待确认',template,[])
            self.assertTrue(any('禁止' in e for e in errors))

    def test_collect_selected_and_intervening(self):
        collector=module('collect_git')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            def g(*args):
                return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
            g('init','-q');g('config','user.name','Test');g('config','user.email','test@example.com')
            f=root/'app.txt';f.write_text('value=0\n')
            g('add','.');g('commit','-qm','base')
            f.write_text('value=1\n');g('add','.');g('commit','-qm','selected-1');c1=g('rev-parse','HEAD')
            f.write_text('value=intervening\n');g('add','.');g('commit','-qm','unselected')
            f.write_text('value=2\n');g('add','.');g('commit','-qm','selected-2');c3=g('rev-parse','HEAD')
            r=collector.collect({'name':'demo','path':str(root),'revisions':[c3,c1]})
            self.assertEqual([x['sha'] for x in r['selection']],[c1,c3])
            self.assertEqual(len(r['unselected_overlaps']),1)
            self.assertIn('app.txt',r['unselected_overlaps'][0]['files'])
            self.assertEqual(r['worktree_status'],[])

    def test_github_url_parser(self):
        access=module('repo_access')
        self.assertEqual(access.parse_github_url('https://github.com/lalala8462/webapi'),
                         ('lalala8462','webapi','https://github.com/lalala8462/webapi.git'))
        self.assertEqual(access.parse_github_url('https://github.com/lalala8462/webapi.git'),
                         ('lalala8462','webapi','https://github.com/lalala8462/webapi.git'))
        for bad in ('https://evil.com/owner/repo', 'https://user:pass@github.com/owner/repo',
                    'https://github.com/owner/../repo', 'https://github.com/owner/repo/commit/a'):
            with self.assertRaises(ValueError):
                access.parse_github_url(bad)

    def test_collect_cli_direct_revisions(self):
        collector=module('collect_git')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'project';root.mkdir()
            def g(*args):
                return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
            g('init','-q');g('config','user.name','Test');g('config','user.email','test@example.com')
            (root/'a.txt').write_text('hello\n',encoding='utf-8')
            g('add','.');g('commit','-qm','base')
            (root/'a.txt').write_text('world\n',encoding='utf-8')
            g('add','.');g('commit','-qm','change')
            commit=g('rev-parse','HEAD')
            evidence=Path(td)/'evidence.json'
            with mock.patch.object(collector,'prepare_repo',return_value=(root,'project')) as prepared:
                self.assertEqual(collector.main(['--repo','https://github.com/owner/project',
                    '--rev',commit,'--output',str(evidence)]),0)
                prepared.assert_called_once_with('https://github.com/owner/project',None)
            parsed=json.loads(evidence.read_text(encoding='utf-8'))
            self.assertEqual(parsed['schema_version'],2)
            self.assertEqual(parsed['projects'][0]['selection'][0]['sha'],commit)
            self.assertEqual(collector.main(['--repo','https://github.com/owner/project',
                    '--rev',commit,'--base','start','--head','end',
                    '--output',str(Path(td)/'bad.json')]),2)


    def test_selected_net_and_cancel(self):
        collector=module('collect_git')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            def g(*args):
                return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
            g('init','-q');g('config','user.name','Test');g('config','user.email','test@example.com')
            file=root/'record.txt'
            file.write_text('status=old\n',encoding='utf-8')
            g('add','.');g('commit','-qm','initial')
            file.write_text('status=temporary\n',encoding='utf-8')
            g('add','.');g('commit','-qm','intermediate')
            a=g('rev-parse','HEAD')
            file.write_text('status=old\n',encoding='utf-8')
            g('add','.');g('commit','-qm','undo')
            b=g('rev-parse','HEAD')
            result=collector.collect({'name':'sample','path':str(root),
                                       'revisions':[b,a]})
            self.assertEqual(result['net_effect']['status'],'no_net_change')
            self.assertEqual(result['net_effect']['changed_files'],[])
            self.assertEqual(len(result['net_effect']['applied_commits']),2)

    def test_selected_net_intervening_conflict(self):
        collector=module('collect_git')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            def g(*args):
                return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
            g('init','-q');g('config','user.name','Test');g('config','user.email','test@example.com')
            file=root/'record.txt'
            file.write_text('status=old\n',encoding='utf-8')
            g('add','.');g('commit','-qm','initial')
            file.write_text('status=selected-a\n',encoding='utf-8')
            g('add','.');g('commit','-qm','selected-a')
            a=g('rev-parse','HEAD')
            file.write_text('status=unselected\n',encoding='utf-8')
            g('add','.');g('commit','-qm','intervening')
            file.write_text('status=selected-b\n',encoding='utf-8')
            g('add','.');g('commit','-qm','selected-b')
            b=g('rev-parse','HEAD')
            result=collector.collect({'name':'sample','path':str(root),
                                       'revisions':[a,b]})
            self.assertEqual(result['net_effect']['status'],'review_required')
            self.assertFalse(result['net_effect']['net_diff'])
            self.assertTrue(result['unselected_overlaps'])

    def test_validate_misgrouped_tests(self):
        val=module('validate_report')
        template=(ROOT/'assets'/'diff-list-template.md').read_text(encoding='utf-8')
        report=template.replace('## 5. 重点测试场景',
            '## 5. 重点测试场景\n**测试点：** A\n**测试前提：** A1\n'
            '**测试点：** B\n**预期结果：** B2\n**测试前提：** B1\n'
            '**预期结果：** A2')
        errors,_=val.validate(report,template,[])
        self.assertTrue(any('唯一有效' in e for e in errors))

class LogicAstTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from shutil import which
        if not which('javac') or not which('java'):
            raise unittest.SkipTest('Requires JDK 8+ for Java AST scanner')

    def test_ast_detects_changed_special_logic(self):
        logic = module('logic_candidates')
        old = '''class Pay {
          void run(String status) { if (status == null) return; }
          void run(int count) { if (count > 3) return; }
        }'''
        new = '''class Pay {
          @Transactional void run(String status) {
            if (status == null) retry();
            if (status.equals("SECRET_STATUS_1")) setStatus("WAITING");
            try { charge(); } catch (Exception ex) { rollback(); }
            synchronized(this) { lock(); unlock(); }
          }
          void run(int count) { if (count > 3) return; }
          void retry() {} void setStatus(String status) {} void charge() {}
          void rollback() {} void lock() {} void unlock() {}
        }
        @interface Transactional {}'''
        findings, errors = logic.extract_candidates([{'path':'src/Pay.java','before':old,'after':new}])
        self.assertEqual(errors, [])
        cats = {x['category'] for x in findings}
        self.assertTrue({'BRANCH','STATE','RETRY','TRANSACTION','EXCEPTION','CONCURRENCY'} <= cats)
        self.assertTrue(all(x['priority'] in ('high','medium') and
                            not x['actual_behavior_verified'] and not x['binding_verified']
                            for x in findings))
        self.assertFalse(any('run(int)' in x['method'] for x in findings))
        self.assertNotIn('SECRET_STATUS_1', json.dumps(findings))

    def test_ast_source_format_only_is_ignored(self):
        logic = module('logic_candidates')
        before = 'class A{void test(){if(x>0){setStatus(1);}} int x;void setStatus(int s){}}'
        after = 'class A { void test() { if (x > 0) { setStatus(1); } } int x; void setStatus(int s) {} }'
        findings, errors = logic.extract_candidates([{'path':'A.java','before':before,'after':after}])
        self.assertEqual(errors, [])
        self.assertEqual(findings, [])

    def test_ast_syntax_error_skips_without_fake_candidates(self):
        logic = module('logic_candidates')
        findings, errors = logic.extract_candidates([
            {'path':'Bad.java','before':'class C {}','after':'class C { void broken( {'}])
        self.assertFalse(findings)
        self.assertEqual(len(errors),1)
        self.assertEqual(errors[0]['path'],'Bad.java')

    def test_java_source_pairs_range_and_selected(self):
        collector = module('collect_git')
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            def g(*args):
                return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
            g('init','-q');g('config','user.name','Test');g('config','user.email','test@example.com')
            java=root/'Main.java'
            java.write_text('class Main { void f() {} }\n', encoding='utf-8')
            g('add','.');g('commit','-qm','base'); a=g('rev-parse','HEAD')
            java.write_text('class Main { void f() { retry(); } void retry(){} }\n', encoding='utf-8')
            g('add','.');g('commit','-qm','selected'); b=g('rev-parse','HEAD')
            for choice in ({'range': {'base':a,'head':b}}, {'revisions':[b]}):
                r=collector.collect({'name':'demo','path':str(root), **choice}, capture_java=True)
                self.assertEqual(r['net_effect']['status'],'composed')
                pairs=r['net_effect']['java_sources']
                self.assertEqual(len(pairs),1)
                self.assertIn('void retry()',pairs[0]['after'])
                self.assertNotIn('void retry()',pairs[0]['before'])

    def test_rename_does_not_invent_special_changes(self):
        collector = module('collect_git')
        logic = module('logic_candidates')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            def g(*args):
                return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
            g('init','-q');g('config','user.name','Test');g('config','user.email','test@example.com')
            (root/'Old.java').write_text('class A { void f(){ if (ok) retry(); } boolean ok; void retry(){} }\n',encoding='utf-8')
            g('add','.');g('commit','-qm','baseline');a=g('rev-parse','HEAD')
            (root/'Old.java').rename(root/'New.java')
            g('add','-A');g('commit','-qm','rename');b=g('rev-parse','HEAD')
            for spec in ({'range':{'base':a,'head':b}}, {'revisions':[b]}):
                evidence=collector.collect({'name':'demo','path':str(root),**spec},capture_java=True)
                pairs=evidence['net_effect']['java_sources']
                self.assertEqual(len(pairs),1)
                self.assertEqual(pairs[0]['before'],pairs[0]['after'])
                findings,skips=logic.extract_candidates(pairs)
                self.assertEqual(findings,[])
                self.assertEqual(skips,[])


if __name__=='__main__':
    unittest.main()
