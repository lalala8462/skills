"""Run: py -3 -m unittest discover -s tests -v"""
import importlib.util
import json
import os
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
    def test_jdk_tools_discovery(self):
        logic = module('logic_candidates')
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            suffix = '.exe' if os.name == 'nt' else ''
            javac = bin_dir / ('javac' + suffix)
            java = bin_dir / ('java' + suffix)
            javac.touch()
            java.touch()
            with mock.patch.object(logic.shutil, 'which', return_value=str(javac)):
                self.assertEqual(logic.jdk_tools(), (str(javac.resolve()), str(java), None))
                lib = root / 'lib'
                lib.mkdir()
                jar = lib / 'tools.jar'
                jar.touch()
                self.assertEqual(logic.jdk_tools(), (str(javac.resolve()), str(java), str(jar)))
            with mock.patch.object(logic.shutil, 'which', return_value=None):
                with self.assertRaisesRegex(ValueError, 'javac not found'):
                    logic.jdk_tools()

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
                      '## 5. 测试用例导出':'Excel：webapi-cases.xlsx；重点用例数量：1；常规用例数量：0；总用例数量：1',
                      '## 6. 验证情况':'仅完成静态分析。'}
            report='\n'.join(line+'\n'+sections.get(line,'') if line.startswith('#') else line
                             for line in template.splitlines())
            errors,_=val.validate(report,template,['webapi='+sha])
            self.assertEqual(errors,[])
            errors,_=val.validate(report.replace('总用例数量：1','总用例数量：零'),template,[])
            self.assertTrue(any('用例数量' in e for e in errors))
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
    def test_fresh_no_checkout_repository(self):
        access = module('repo_access')
        url = 'https://github.com/owner/project'
        with mock.patch.object(access.Path, 'mkdir'), \
             mock.patch.object(access.tempfile, 'mkdtemp', side_effect=['run-one', 'run-two']), \
             mock.patch.object(access, 'run_git') as git:
            first, name = access.prepare_repo(url)
            second, _ = access.prepare_repo(url)
            self.assertNotEqual(first, second)
            self.assertEqual(name, 'project')
            self.assertEqual(git.call_count, 2)
            for call in git.call_args_list:
                self.assertIn('--no-checkout', call.args)
                self.assertIn('core.longpaths=true', call.args)
            with self.assertRaisesRegex(ValueError, 'no longer supported'):
                access.prepare_repo(url, 'user-workspace')

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

    def test_gitlab_url_parser(self):
        access = module('repo_access')
        self.assertEqual(access.parse_repo_url('http://gitlab.fingard.cn/rh-rhf/webapi-group/webapi3.0'),
                         ('gitlab.fingard.cn', 'rh-rhf/webapi-group', 'webapi3.0',
                          'http://gitlab.fingard.cn/rh-rhf/webapi-group/webapi3.0.git'))
        for bad in ('http://user:pass@gitlab.example/group/project',
                    'https://gitlab.example/group/../project',
                    'https://gitlab.example/group/project?token=secret',
                    'file:///group/project'):
            with self.assertRaises(ValueError):
                access.parse_repo_url(bad)

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


    def test_report_requires_legacy_function_impact(self):
        val=module('validate_report')
        template=(ROOT/'assets'/'diff-list-template.md').read_text(encoding='utf-8')
        sha='a'*40
        report=template
        blocks={
            '## 1. 分析范围':'webapi '+sha,
            '### 2.1 需求改动':'新增响应字段 orderStatus。',
            '### 2.2 表字段 DDL 修改':'无',
            '### 2.3 表配置 DML 准备清单':'无',
            '### 2.4 配置文件调整':'无',
            '### 3.1 整体流程说明':'返回业务处理结果。',
            '### 3.2 Mermaid 流程图':'```mermaid\nflowchart TD\n A[请求] --> B[响应]\n```',
            '## 4. 特殊逻辑与测试关注点':'新增字段对旧调用方的影响。',
            '## 5. 测试用例导出':'无',
            '## 6. 验证情况':'仅静态分析。'
        }
        report='\n'.join(line+'\n'+blocks.get(line,'') if line.startswith('#') else line
                         for line in template.splitlines())
        errors,_=val.validate(report,template,[])
        self.assertTrue(any('既有功能影响' in e for e in errors))
        corrected=report.replace('新增字段对旧调用方的影响。',
                                 '既有功能影响：旧调用方可能忽略该字段，需验证向后兼容。')
        errors,_=val.validate(corrected,template,[])
        self.assertEqual(errors, [])


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

    def test_markdown_rejects_duplicated_excel_cases(self):
        val=module('validate_report')
        template=(ROOT/'assets'/'diff-list-template.md').read_text(encoding='utf-8')
        report=template.replace('## 5. 测试用例导出',
            '## 5. 测试用例导出\nExcel：cases.xlsx；用例数量：2\n'
            '**测试点：** A\n**测试前提：** A1\n'
            '**预期结果：** A2')
        errors,_=val.validate(report,template,[])
        self.assertTrue(any('不得重复' in e for e in errors))

    def test_unicode_java_and_config_paths(self):
        collector=module('collect_git')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            def git(*args):
                return subprocess.check_output(['git','-C',str(root),*args],
                                               encoding='utf-8').strip()
            git('init','-q')
            git('config','user.name','Test')
            git('config','user.email','test@example.com')
            java=root/'订单服务.java'
            cfg=root/'渠道配置.properties'
            java.write_text('class Pay { void run() {} }\n',encoding='utf-8')
            cfg.write_text('retry=false\n',encoding='utf-8')
            git('add','.');git('commit','-qm','base')
            base=git('rev-parse','HEAD')
            java.write_text('class Pay { void run(){ retry(); } void retry(){} }\n',
                            encoding='utf-8')
            cfg.write_text('retry=true\n',encoding='utf-8')
            git('add','.');git('commit','-qm','selected')
            head=git('rev-parse','HEAD')
            for spec in ({'revisions':[head]}, {'range':{'base':base,'head':head}}):
                result=collector.collect({'name':'demo','path':str(root),**spec},
                                         capture_java=True,capture_config=True)
                net=result['net_effect']
                self.assertEqual(net['status'],'composed')
                self.assertIn('订单服务.java',net['changed_files'])
                self.assertIn('渠道配置.properties',net['changed_files'])
                self.assertEqual([p['path'] for p in net['java_sources']],['订单服务.java'])
                self.assertEqual([p['path'] for p in net['config_sources']],['渠道配置.properties'])
                self.assertFalse(net['java_skipped'])
                self.assertFalse(net['config_skipped'])

    def test_footer_allows_only_isolated_clone_directories(self):
        val=module('validate_report')
        template=(ROOT/'assets'/'diff-list-template.md').read_text(encoding='utf-8')
        sha='a'*40
        blocks={
            '## 1. 分析范围':'webapi '+sha,
            '### 2.1 需求改动':'无',
            '### 2.2 表字段 DDL 修改':'无',
            '### 2.3 表配置 DML 准备清单':'无',
            '### 2.4 配置文件调整':'无',
            '### 3.1 整体流程说明':'无',
            '### 3.2 Mermaid 流程图':'无',
            '## 4. 特殊逻辑与测试关注点':'无',
            '## 5. 测试用例导出':'无',
            '## 6. 验证情况':'已执行静态分析。'
        }
        report='\n'.join(line+'\n'+blocks.get(line,'') if line.startswith('#') else line
                         for line in template.splitlines())
        footer=('\n<!-- diff-list-generated-clones:start -->\n'
                '**本次创建的分析仓库目录（请后续手动删除）：**\n'
                '- \x60C:\\Users\\test\\AppData\\Local\\diff-list\\runs\\run-abc\x60\n'
                '<!-- diff-list-generated-clones:end -->\n')
        errors,_=val.validate(report+footer,template,['webapi='+sha])
        self.assertEqual(errors,[])
        errors,_=val.validate(report+footer.replace('diff-list\\runs','work\\source'),
                               template,['webapi='+sha])
        self.assertTrue(any('专用目录' in err for err in errors))
        errors,_=val.validate(report+'\nC:\\work\\Main.java',template,['webapi='+sha])
        self.assertTrue(any('Windows 本机绝对路径' in err for err in errors))

    def test_append_run_dirs_records_exact_locations(self):
        writer=module('append_run_dirs')
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict('os.environ',{'LOCALAPPDATA':td}):
                clone=Path(td)/'diff-list'/'runs'/'run-123'/'repo'
                (clone/'.git').mkdir(parents=True)
                report=Path(td)/'report.md'
                report.write_text('# Report\n## 6. 验证情况\n静态分析。\n',encoding='utf-8')
                evidence=Path(td)/'evidence.json'
                evidence.write_text(json.dumps({'analysis_clone':str(clone)}),
                                    encoding='utf-8')
                dirs=writer.append(report,[str(evidence)],[str(clone)])
                self.assertEqual(dirs,[str(clone.parent)])
                self.assertIn(str(clone.parent),report.read_text(encoding='utf-8'))
                self.assertIn('请后续手动删除',report.read_text(encoding='utf-8'))
                with self.assertRaises(ValueError):
                    writer.append(report,[str(evidence)],[])

    def test_range_long_paths_capture_java_and_config(self):
        collector = module('collect_git')
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td).resolve()
            root = parent / ('repo-' + 'x' * max(1, 140 - len(str(parent)) - 6))
            root.mkdir()

            def git(*args, stdin=None):
                return subprocess.check_output(
                    ['git', '-c', 'core.longpaths=true', '-C', str(root), *args],
                    input=stdin.encode('utf-8') if stdin is not None else None
                ).decode('utf-8').strip()

            git('init', '-q')
            git('config', 'user.name', 'Test')
            git('config', 'user.email', 'test@example.com')
            git('config', 'core.longpaths', 'false')
            java_path = 'src/test/java/' + 'nested/' * 12 + 'LongPathRegression.java'
            config_path = 'src/main/resources/' + 'nested/' * 12 + 'application.properties'
            before = {java_path: 'class LongPathRegression { int value = 1; }\n',
                      config_path: 'enabled=false\n'}
            after = {java_path: 'class LongPathRegression { int value = 2; }\n',
                     config_path: 'enabled=true\n'}

            def commit_sources(sources, parent_sha=None):
                # 仅写入 Git 对象和索引，避免测试依赖 Windows 工作树的长路径写入支持。
                for name, content in sources.items():
                    blob = git('hash-object', '-w', '--stdin', stdin=content)
                    git('update-index', '--add', '--cacheinfo', '100644,' + blob + ',' + name)
                tree = git('write-tree')
                args = ('-p', parent_sha) if parent_sha else ()
                return git('commit-tree', tree, *args, stdin='fixture\n')

            base = commit_sources(before)
            head = commit_sources(after, base)
            self.assertGreater(len(str(root / (head + ':' + java_path))), 260)
            result = collector.collect(
                {'name': 'demo', 'path': str(root), 'isolated_no_checkout': True,
                 'range': {'base': base, 'head': head}},
                capture_java=True, capture_config=True)['net_effect']
            self.assertEqual(result['java_skipped'], [])
            self.assertEqual(result['config_skipped'], [])
            self.assertEqual(result['java_sources'], [
                {'path': java_path, 'before': before[java_path], 'after': after[java_path]}])
            self.assertEqual(result['config_sources'], [
                {'path': config_path, 'before': before[config_path], 'after': after[config_path]}])

    def test_range_blob_read_failure_is_skipped_not_empty_source(self):
        collector=module('collect_git')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            def git(*args):
                return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
            git('init','-q');git('config','user.name','Test');git('config','user.email','test@example.com')
            java=root/'Main.java'
            java.write_text('class Main { int value=1; }\n',encoding='utf-8')
            git('add','.');git('commit','-qm','base');base=git('rev-parse','HEAD')
            java.write_text('class Main { int value=2; }\n',encoding='utf-8')
            git('add','.');git('commit','-qm','change');head=git('rev-parse','HEAD')
            real_read=collector.read_blob
            def flaky(root_arg, rev, filename):
                if rev == base:
                    return None, 'simulated blob read failure'
                return real_read(root_arg, rev, filename)
            with mock.patch.object(collector,'read_blob',side_effect=flaky):
                result=collector.collect({'name':'demo','path':str(root),
                                          'range':{'base':base,'head':head}},
                                         capture_java=True)
            self.assertEqual(result['net_effect']['java_sources'],[])
            self.assertEqual(len(result['net_effect']['java_skipped']),1)
            self.assertIn('cannot read baseline blob',result['net_effect']['java_skipped'][0]['reason'])



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

class AdditionalRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import shutil
        if not (shutil.which('java') and shutil.which('javac')):
            raise unittest.SkipTest('JDK 8+ required')

    def test_call_and_money_expression(self):
        logic = module('logic_candidates')
        old = '''class Pay { void settle(){ debit(); amount = amount.add(one); }
          Object amount, one; void debit(){} void credit(){} }'''
        new = '''class Pay { void settle(){ credit(); amount = amount.multiply(hundred); }
          Object amount, hundred; void debit(){} void credit(){} }'''
        candidates, skipped = logic.extract_candidates([{'path':'Pay.java','before':old,'after':new}])
        self.assertEqual(skipped, [])
        self.assertTrue(any(c['category']=='CALL' and c['description']=='call credit' for c in candidates))
        self.assertTrue(any(c['category']=='CALL' and c['description']=='call debit' for c in candidates))
        self.assertTrue(any(c['category']=='EXPRESSION' and c['description']=='assignment amount' for c in candidates))

    def test_nested_call_discard_is_syntax_hint_only(self):
        logic = module('logic_candidates')
        before = 'class A { String walk(Object x) { return ""; } }'
        after = '''class A { String walk(Object x) {
            if (x instanceof java.util.Map) { walk(x); }
            return "";
        } }'''
        candidates, skipped = logic.extract_candidates([{'path':'A.java','before':before,'after':after}])
        self.assertEqual(skipped, [])
        hint = next(c for c in candidates if c['category'] == 'DISCARDED_CALL')
        self.assertEqual(hint['description'], 'standalone call walk')
        self.assertFalse(hint['binding_verified'])
        self.assertFalse(hint['actual_behavior_verified'])
        groups = logic.group_candidates(candidates)
        self.assertEqual(len(groups), 1)
        self.assertIn('walk', groups[0]['changed_call_names'])
        self.assertIn('DISCARDED_CALL', groups[0]['categories'])

    def test_consumed_return_does_not_emit_discarded_call(self):
        logic = module('logic_candidates')
        old = 'class A { String f() { return ""; } String g(){ return ""; } }'
        new = 'class A { String f() { return ""; } String g(){ return f(); } }'
        candidates, skipped = logic.extract_candidates([{'path':'A.java','before':old,'after':new}])
        self.assertEqual(skipped, [])
        self.assertFalse(any(c['category'] == 'DISCARDED_CALL' for c in candidates))

    def test_anonymous_classes_have_distinct_stable_scopes(self):
        logic = module('logic_candidates')
        old = '''class A {
          boolean flag;
          void ping() {}
          void run() {
            Runnable one = new Runnable() { public void run() { if (flag) ping(); } };
            Runnable two = new Runnable() { public void run() { if (flag) ping(); } };
          }
        }'''
        new = '''class A {
          boolean flag;
          void ping() {}
          void run() {
            Runnable one = new Runnable() { public void run() { if (flag) ping(); } };
            Runnable two = new Runnable() { public void run() { if (!flag) ping(); } };
          }
        }'''
        candidates, skipped = logic.extract_candidates([{'path':'A.java','before':old,'after':new}])
        self.assertEqual(skipped,[])
        changed=[item for item in candidates if item['category']=='BRANCH']
        self.assertTrue(changed)
        self.assertTrue(all('<anonymous:2>#run()' in item['method'] for item in changed))
        self.assertFalse(any('<anonymous:1>#run()' in item['method'] for item in changed))

    def test_class_and_method_rename_not_false_high(self):
        logic = module('logic_candidates')
        old = '''class Pay { void execute(){ if(status==1) setStatus(1); }
            int status; void setStatus(int value){} }'''
        new = '''class PayService { void perform(){ if(status==1) setStatus(1); }
            int status; void setStatus(int value){} }'''
        candidates, skipped = logic.extract_candidates([{'path':'Pay.java','before':old,'after':new}])
        self.assertEqual(skipped, [])
        self.assertEqual(candidates, [])

    def test_config_properties_yaml_xml_changes_and_secret_mask(self):
        config = module('config_candidates')
        pairs = [
          {'path':'profile/test.properties','before':'refund.retry.enabled=false\nsecret.token=abc123\n','after':'refund.retry.enabled=true\nsecret.token=def456\n'},
          {'path':'app.yml','before':'payment:\n  retry: false\n','after':'payment:\n  retry: true\n'},
          {'path':'app.xml','before':'<beans><property name="retry" value="0"/></beans>',
           'after':'<beans><property name="retry" value="1"/></beans>'},
        ]
        findings, skipped = config.extract_config_candidates(pairs)
        self.assertEqual(skipped, [])
        self.assertEqual(len(findings),4)
        self.assertTrue(any(x['key']=='refund.retry.enabled' and x['after']=='true' for x in findings))
        self.assertTrue(any(x['key']=='payment.retry' for x in findings))
        secret = next(x for x in findings if x['key']=='secret.token')
        self.assertEqual(secret['before'],'<redacted>')
        self.assertNotIn('abc123',str(findings))

    def test_config_unknown_yaml_is_skipped_not_silent(self):
        config = module('config_candidates')
        findings, skipped = config.extract_config_candidates([{'path':'app.yml',
          'before':'urls:\n - http://localhost\n', 'after':'urls:\n - http://example.com\n'}])
        self.assertEqual(findings,[])
        self.assertEqual(len(skipped),1)
        self.assertFalse(skipped[0]['review_required'])
        self.assertEqual(skipped[0]['kind'],'non_runtime_yaml')

    def test_runtime_yaml_and_non_config_xml_review_classification(self):
        config = module('config_candidates')
        findings, skipped = config.extract_config_candidates([
            {'path':'src/main/resources/application.yml',
             'before':'urls:\n - http://localhost\n',
             'after':'urls:\n - http://example.com\n'},
            {'path':'src/main/resources/mapper/OrderMapper.xml',
             'before':'<mapper><select id="q">select 1</select></mapper>',
             'after':'<mapper><select id="q">select 2</select></mapper>'}
        ])
        self.assertEqual(findings,[])
        by_path={item['path']:item for item in skipped}
        self.assertTrue(by_path['src/main/resources/application.yml']['review_required'])
        self.assertEqual(by_path['src/main/resources/application.yml']['kind'],'parse_failure')
        self.assertFalse(by_path['src/main/resources/mapper/OrderMapper.xml']['review_required'])
        self.assertEqual(by_path['src/main/resources/mapper/OrderMapper.xml']['kind'],'not_config_mapping')

    def test_xml_config_mapping_add_remove_is_not_ignored(self):
        config = module('config_candidates')
        removed, skipped = config.extract_config_candidates([{
            'path':'src/main/resources/applicationContext.xml',
            'before':'<beans><property name="retry" value="1"/></beans>',
            'after':'<beans></beans>'
        }])
        self.assertEqual(skipped,[])
        self.assertEqual(len(removed),1)
        self.assertEqual(removed[0]['key'],'retry')
        self.assertEqual(removed[0]['change'],'removed')
        self.assertEqual(removed[0]['before'],'1')
        self.assertIsNone(removed[0]['after'])

        added, skipped = config.extract_config_candidates([{
            'path':'src/main/resources/applicationContext.xml',
            'before':'<beans></beans>',
            'after':'<beans><property name="retry" value="1"/></beans>'
        }])
        self.assertEqual(skipped,[])
        self.assertEqual(len(added),1)
        self.assertEqual(added[0]['key'],'retry')
        self.assertEqual(added[0]['change'],'added')
        self.assertIsNone(added[0]['before'])
        self.assertEqual(added[0]['after'],'1')

    def test_config_capture_range_and_selected(self):
        collector = module('collect_git')
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            def g(*args):
                return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
            g('init','-q');g('config','user.name','Test');g('config','user.email','test@example.com')
            f = root/'application.properties'
            f.write_text('refund.retry.enabled=false\n',encoding='utf-8')
            g('add','.');g('commit','-qm','base');base=g('rev-parse','HEAD')
            f.write_text('refund.retry.enabled=true\n',encoding='utf-8')
            g('add','.');g('commit','-qm','update');head=g('rev-parse','HEAD')
            for selection in ({'range':{'base':base,'head':head}}, {'revisions':[head]}):
                result=collector.collect({'name':'demo','path':str(root),**selection},capture_config=True)
                pairs=result['net_effect']['config_sources']
                self.assertEqual(len(pairs),1)
                findings,skipped=module('config_candidates').extract_config_candidates(pairs)
                self.assertEqual(skipped,[])
                self.assertEqual(findings[0]['change'],'modified')

    def test_document_checks_mapping_and_code_fence_heading(self):
        validator = module('validate_report')
        template=(ROOT/'assets'/'diff-list-template.md').read_text(encoding='utf-8')
        one, two = 'a'*40, 'b'*40
        content=template.replace('## 1. 分析范围', '## 1. 分析范围\nwebapi '+one+'\nadpts '+two)
        content=content.replace('### 2.1 需求改动', '### 2.1 需求改动\n需求修改')
        content=content.replace('### 2.2 表字段 DDL 修改', '### 2.2 表字段 DDL 修改\n无')
        content=content.replace('### 2.3 表配置 DML 准备清单', '### 2.3 表配置 DML 准备清单\n无')
        content=content.replace('### 2.4 配置文件调整',
            '### 2.4 配置文件调整\n```properties\n# this is a valid properties comment\nretry.enabled=true\n```')
        content=content.replace('### 3.1 整体流程说明','### 3.1 整体流程说明\n无')
        content=content.replace('### 3.2 Mermaid 流程图','### 3.2 Mermaid 流程图\n无')
        content=content.replace('## 4. 特殊逻辑与测试关注点','## 4. 特殊逻辑与测试关注点\n无')
        content=content.replace('## 5. 测试用例导出','## 5. 测试用例导出\n无')
        content=content.replace('## 6. 验证情况','## 6. 验证情况\n静态测试')
        errors,_=validator.validate(content,template,['webapi='+one,'adpts='+two])
        self.assertEqual(errors,[])
        errors,_=validator.validate(content,template,['webapi='+two])
        self.assertTrue(any('对应关系' in x for x in errors))

    def test_export_test_cases_xlsx_structure(self):
        import zipfile
        from xml.etree import ElementTree as ET
        exporter=module('export_test_cases')
        rows=exporter.normalize({'cases':[
            {'title':'旧版报文兼容','preconditions':'旧版报文可用',
             'steps':['发起请求','检查响应'],'expected':['请求成功','字段正确'],
             'observable_evidence':[{'surface':'接口','target':'测试响应体状态字段','assertion':'status=SUCCESS，订单号与请求一致'}]},
            {'case_id':'X02','title':'文本防护','preconditions':'测试准备',
             'steps':'=1+2','expected':'保持为文本',
             'observable_evidence':[{'surface':'文件','target':'导出单元格 D3','assertion':'文本内容保持 =1+2，不包含 Excel 公式节点'}]}]})
        self.assertEqual(rows[0][0], 'DL0001')
        self.assertEqual(rows[0][3], '1. 发起请求\n2. 检查响应')
        with tempfile.TemporaryDirectory() as td:
            target=Path(td)/'cases.xlsx'
            self.assertEqual(exporter.export(rows,target), 2)
            with zipfile.ZipFile(target) as archive:
                self.assertIsNone(archive.testzip())
                root=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
                ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                workbook_rows=root.findall('.//s:row',ns)
                self.assertEqual(len(workbook_rows),3)
                self.assertEqual(len(workbook_rows[0].findall('s:c',ns)),5)
                self.assertIn('用例编号',archive.read('xl/worksheets/sheet1.xml').decode('utf-8'))
                self.assertFalse(root.findall('.//s:f',ns))
            with self.assertRaises(FileExistsError):
                exporter.export(rows,target)

    def test_export_test_cases_rejects_incomplete(self):
        exporter=module('export_test_cases')
        case={'title':'测试用例','preconditions':'准备',
              'steps':['执行操作'],'expected':['符合预期'],
              'observable_evidence':[{'surface':'接口','target':'测试响应体状态字段','assertion':'status=SUCCESS，订单号与请求一致'}]}
        for payload in ({'cases':[]}, {'cases':[{**case,'steps':[]}]},
                        {'cases':[{**case,'preconditions':''}]},
                        {'cases':[{**case,'expected':[]}]},
                        {'cases':[{**case,'case_id':'1'},{**case,'case_id':'1'}]}):
            with self.assertRaises(ValueError):
                exporter.normalize(payload)

    def test_report_excel_summary_requires_count(self):
        validator=module('validate_report')
        template=(ROOT/'assets'/'diff-list-template.md').read_text(encoding='utf-8')
        report=template.replace('## 5. 测试用例导出',
            '## 5. 测试用例导出\nExcel：cases.xlsx；重点用例数量：1；常规用例数量：1；总用例数量：2')
        errors,_=validator.validate(report,template,[])
        self.assertFalse(any('测试用例导出章节' in err for err in errors))
        invalid=report.replace('总用例数量：2','总用例数量：两条')
        errors,_=validator.validate(invalid,template,[])
        self.assertTrue(any('总用例数量' in err for err in errors))


    def test_two_sheet_dedup(self):
        import zipfile
        from xml.etree import ElementTree as ET
        exporter=module('export_test_cases')
        def case(title, key):
            return {'title':title,'preconditions':'测试数据已准备',
                    'steps':['发起请求','检查结果'],'expected':['符合预期'],
                    'coverage_key':key,
                    'observable_evidence':[{'surface':'接口','target':'测试响应体状态字段','assertion':'status=SUCCESS，订单号与请求一致'}]}
        data=exporter.normalize({'focus_cases':[case('重试幂等','refund.retry')],
                                 'regular_cases':[case('常规重试','refund.retry'),
                                                  case('正常退款','refund.basic')]})
        self.assertEqual(data['deduplicated'],1)
        self.assertEqual(len(data['regular']),1)
        self.assertEqual(data['focus'][0][0],'DL-F0001')
        self.assertEqual(data['regular'][0][0],'DL-N0001')
        with tempfile.TemporaryDirectory() as td:
            target=Path(td)/'cases.xlsx'
            counts=exporter.export(data,target)
            self.assertEqual((counts['focus'],counts['regular'],counts['total']), (1,1,2))
            with zipfile.ZipFile(target) as archive:
                self.assertIsNone(archive.testzip())
                for name in archive.namelist():
                    if name.endswith(('.xml','.rels')):
                        ET.fromstring(archive.read(name))
                ns={'x':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                workbook=ET.fromstring(archive.read('xl/workbook.xml'))
                names=[item.attrib['name'] for item in workbook.findall('.//x:sheet',ns)]
                self.assertEqual(names,['重点测试用例','常规功能用例'])
                for n in (1,2):
                    sheet=ET.fromstring(archive.read('xl/worksheets/sheet{}.xml'.format(n)))
                    self.assertEqual(len(sheet.findall('.//x:row',ns)),2)
                    self.assertFalse(sheet.findall('.//x:f',ns))
            with self.assertRaises(FileExistsError):
                exporter.export(data,target)

    def test_dual_sheet_partial_overlap_and_empty_focus(self):
        import zipfile
        exporter=module('export_test_cases')
        case={'title':'核心','preconditions':'前提','steps':['执行'],
              'expected':['状态正确'],'coverage_key':'basic',
              'observable_evidence':[{'surface':'接口','target':'测试响应体状态字段','assertion':'status=SUCCESS，订单号与请求一致'}]}
        with self.assertRaisesRegex(ValueError,'partially overlaps'):
            exporter.normalize({'focus_cases':[case],
                'regular_cases':[{**case,'title':'多覆盖','coverage_keys':['basic','other']}]})
        with self.assertRaisesRegex(ValueError,'duplicate key'):
            exporter.normalize({'focus_cases':[case,{**case,'title':'另一重点'}],
                                'regular_cases':[]})
        payload=exporter.normalize({'focus_cases':[],'regular_cases':[case]})
        with tempfile.TemporaryDirectory() as td:
            target=Path(td)/'normal.xlsx'
            counts=exporter.export(payload,target)
            self.assertEqual((counts['focus'],counts['regular']), (0,1))
            with zipfile.ZipFile(target) as archive:
                self.assertIn('常规功能用例',archive.read('xl/workbook.xml').decode('utf-8'))
                self.assertIn('xl/worksheets/sheet2.xml',archive.namelist())

    def test_dual_sheet_fallback_exact_content(self):
        exporter=module('export_test_cases')
        case={'title':'正常支付','preconditions':'同样前提','steps':['相同步骤'],
              'expected':['相同结果'],'coverage_key':'focus.key',
              'observable_evidence':[{'surface':'接口','target':'测试响应体状态字段','assertion':'status=SUCCESS，订单号与请求一致'}]}
        duplicate={**case,'coverage_key':'regular.key'}
        data=exporter.normalize({'focus_cases':[case],'regular_cases':[duplicate]})
        self.assertEqual(data['deduplicated'],1)
        self.assertFalse(data['regular'])

    def test_dual_sheet_report_counts(self):
        validator=module('validate_report')
        template=(ROOT/'assets'/'diff-list-template.md').read_text(encoding='utf-8')
        report=template.replace('## 5. 测试用例导出',
            '## 5. 测试用例导出\nExcel：cases.xlsx；重点用例数量：1；常规用例数量：2；总用例数量：3')
        errors,_=validator.validate(report,template,[])
        self.assertFalse(any('用例数量' in err for err in errors))
        errors,_=validator.validate(report.replace('总用例数量：3','总用例数量：5'),template,[])
        self.assertTrue(any('不一致' in err for err in errors))


    def test_observable_evidence_required_for_all_cases(self):
        exporter=module('export_test_cases')
        case={'title':'正常请求','preconditions':'订单存在','steps':['发起请求'],
              'expected':['正常完成'],'coverage_key':'order|valid|success'}
        for value in ({'focus_cases':[case], 'regular_cases':[]},
                      {'focus_cases':[], 'regular_cases':[case]},
                      {'cases':[case]}):
            with self.assertRaisesRegex(ValueError,'observable_evidence'):
                exporter.normalize(value)
        for incomplete in ([], [{}], [{'surface':'接口','target':'返回体'}],
                           [{'surface':'数据库','assertion':'status=SUCCESS'}]):
            bad=dict(case,observable_evidence=incomplete)
            with self.assertRaises(ValueError):
                exporter.normalize({'focus_cases':[bad],'regular_cases':[]})

    def test_vague_assertions_rejected(self):
        exporter=module('export_test_cases')
        for phrase in ('符合预期','处理成功','系统正常','接口成功','退款成功','状态正确'):
            case={'title':'异常退款','preconditions':'准备退款单','steps':['提交退款'],
                  'expected':['状态正确'],'coverage_key':'refund|duplicate|once',
                  'observable_evidence':[{'surface':'数据库','target':'退款流水',
                                          'assertion':phrase}]}
            with self.assertRaisesRegex(ValueError,'not an observable'):
                exporter.normalize({'focus_cases':[case],'regular_cases':[]})

    def test_evidence_channels_visible_in_xlsx_expected_column(self):
        import zipfile
        exporter=module('export_test_cases')
        channels=('页面','接口','日志','数据库','文件','下游行为','缓存')
        item={'title':'检查证据来源','preconditions':'准备测试数据','steps':['执行请求'],
              'expected':['处理完毕'],'coverage_key':'order|valid|success',
              'observable_evidence':[{'surface':surface,'target':'订单状态字段',
                                      'assertion':'status=SUCCESS'} for surface in channels]}
        records=exporter.normalize({'focus_cases':[item],'regular_cases':[]})
        for surface in channels:
            self.assertIn(surface+'｜订单状态字段：status=SUCCESS',records['focus'][0][4])
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/'test.xlsx'
            exporter.export(records,out)
            with zipfile.ZipFile(out) as z:
                xml=z.read('xl/worksheets/sheet1.xml').decode('utf-8')
                self.assertIn('可观察证据：',xml)
                self.assertIn('status=SUCCESS',xml)


class CommitHistoryRegressionTest(unittest.TestCase):
    def test_commit_messages_reverse_intent_and_zero_net(self):
        collector=module('collect_git')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            def git(*args):
                return subprocess.check_output(['git','-C',str(root),*args],
                                               text=True, encoding='utf-8').strip()
            git('init','-q')
            git('config','user.name','Tests')
            git('config','user.email','example@example.com')
            file=root/'支付路由.java'
            file.write_text('class Pay { String route=\"old\"; }\n',encoding='utf-8')
            git('add','.')
            git('commit','-qm','base')
            base=git('rev-parse','HEAD')
            file.write_text('class Pay { String route=\"new\"; }\n',encoding='utf-8')
            git('add','.')
            git('commit','-m','Use new payment route','-m',
                'Design assumption: new payment route should be enabled')
            one=git('rev-parse','HEAD')
            file.write_text('class Pay { String route=\"old\"; }\n',encoding='utf-8')
            git('add','.')
            git('commit','-qm','Restore original payment route')
            two=git('rev-parse','HEAD')
            for spec in ({'revisions':[two,one]},
                         {'range':{'base':base,'head':two}}):
                evidence=collector.collect({'name':'pay','path':str(root),**spec})
                self.assertEqual(evidence['net_effect']['status'],'no_net_change')
                review=evidence['commit_review']
                self.assertEqual([c['sha'] for c in review['commits']],[one,two])
                self.assertEqual(review['commits'][0]['subject'],'Use new payment route')
                self.assertIn('Design assumption',review['commits'][0]['body'])
                self.assertTrue(review['review_required'])
                self.assertEqual(review['textual_reverse_candidate_count'],1)
                repeated=review['repeated_files']
                self.assertEqual(len(repeated),1)
                self.assertEqual(repeated[0]['touch_count'],2)
                self.assertEqual(repeated[0]['reverse_candidates'][0]['later_commit'],two)
                self.assertEqual(repeated[0]['scope'],'file_only_not_method_binding')

    def test_commit_review_tracks_file_identity_across_rename(self):
        collector=module('collect_git')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            def git(*args):
                return subprocess.check_output(['git','-C',str(root),*args],
                                               text=True, encoding='utf-8').strip()
            git('init','-q')
            git('config','user.name','Tests')
            git('config','user.email','example@example.com')
            old=root/'OldName.java'
            old.write_text('class A { int value=0; }\n',encoding='utf-8')
            git('add','.')
            git('commit','-qm','base')
            base=git('rev-parse','HEAD')
            old.write_text('class A { int value=1; }\n',encoding='utf-8')
            git('add','.')
            git('commit','-qm','enable value')
            one=git('rev-parse','HEAD')
            old.rename(root/'NewName.java')
            git('add','-A')
            git('commit','-qm','rename file')
            two=git('rev-parse','HEAD')
            new=root/'NewName.java'
            new.write_text('class A { int value=0; }\n',encoding='utf-8')
            git('add','.')
            git('commit','-qm','restore value')
            three=git('rev-parse','HEAD')
            result=collector.collect({'name':'sample','path':str(root),
                                      'range':{'base':base,'head':three}})
            repeated=result['commit_review']['repeated_files']
            self.assertEqual(len(repeated),1)
            self.assertEqual(repeated[0]['path'],'NewName.java')
            self.assertEqual(repeated[0]['identity_paths'],['NewName.java','OldName.java'])
            self.assertEqual(repeated[0]['commits'],[one,two,three])
            self.assertEqual(repeated[0]['touch_count'],3)
            self.assertEqual(result['commit_review']['textual_reverse_candidate_count'],1)

    def test_same_file_repeated_is_not_proof_of_opposite_logic(self):
        collector=module('collect_git')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            def git(*args):
                return subprocess.check_output(['git','-C',str(root),*args],
                                               text=True, encoding='utf-8').strip()
            git('init','-q')
            git('config','user.name','Tests')
            git('config','user.email','example@example.com')
            f=root/'service.java'
            f.write_text('class S {\n int a=1;\n int b=1;\n}\n',encoding='utf-8')
            git('add','.')
            git('commit','-qm','base')
            f.write_text('class S {\n int a=2;\n int b=1;\n}\n',encoding='utf-8')
            git('add','.')
            git('commit','-qm','Update first field')
            one=git('rev-parse','HEAD')
            f.write_text('class S {\n int a=2;\n int b=2;\n}\n',encoding='utf-8')
            git('add','.')
            git('commit','-qm','Update second field')
            two=git('rev-parse','HEAD')
            result=collector.collect({'name':'service','path':str(root),
                                      'revisions':[one,two]})
            review=result['commit_review']
            self.assertEqual(len(review['repeated_files']),1)
            self.assertEqual(review['textual_reverse_candidate_count'],0)
            self.assertEqual(len(review['commits']),2)
            self.assertFalse(review['review_required'])

    def test_selected_history_omits_unselected_commit_messages(self):
        collector=module('collect_git')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            def git(*args):
                return subprocess.check_output(['git','-C',str(root),*args],
                                               text=True, encoding='utf-8').strip()
            git('init','-q')
            git('config','user.name','Tests')
            git('config','user.email','example@example.com')
            for f in ('a.txt','b.txt','c.txt'):
                (root/f).write_text('old\n',encoding='utf-8')
            git('add','.')
            git('commit','-qm','base')
            (root/'a.txt').write_text('new\n',encoding='utf-8')
            git('add','.')
            git('commit','-qm','Selected A')
            one=git('rev-parse','HEAD')
            (root/'b.txt').write_text('middle\n',encoding='utf-8')
            git('add','.')
            git('commit','-qm','NOT SELECTED')
            (root/'c.txt').write_text('new\n',encoding='utf-8')
            git('add','.')
            git('commit','-qm','Selected C')
            three=git('rev-parse','HEAD')
            report=collector.collect({'name':'sample','path':str(root),
                                      'revisions':[one,three]})
            reviewed=report['commit_review']['commits']
            self.assertEqual([c['sha'] for c in reviewed],[one,three])
            self.assertNotIn('NOT SELECTED',[c['subject'] for c in reviewed])

    def test_cross_branch_range_warns_instead_of_claiming_linear_history(self):
        collector=module('collect_git')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            def git(*args):
                return subprocess.check_output(['git','-C',str(root),*args],
                                               text=True, encoding='utf-8').strip()
            git('init','-q')
            git('config','user.name','Tests')
            git('config','user.email','example@example.com')
            f=root/'route.txt'
            f.write_text('old\n',encoding='utf-8')
            git('add','.')
            git('commit','-qm','base')
            base=git('rev-parse','HEAD')
            f.write_text('branch one\n',encoding='utf-8')
            git('add','.')
            git('commit','-qm','Branch one')
            one=git('rev-parse','HEAD')
            git('checkout','-q',base)
            f.write_text('branch two\n',encoding='utf-8')
            git('add','.')
            git('commit','-qm','Branch two')
            two=git('rev-parse','HEAD')
            review=collector.collect({'name':'sample','path':str(root),
                                      'range':{'base':one,'head':two}})['commit_review']
            self.assertEqual(review['scope'],'endpoint_comparison_only')
            self.assertTrue(review['review_required'])
            self.assertEqual(review['commits'],[])


if __name__=='__main__':
    unittest.main()
