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


def fixture_analysis():
    return dict(analysis_id='fixture-analysis',projects=[dict(name='sample',facts=[
        dict(id='fact-1',kind='method_change',data={'symbol':'sample.Flow#run()'},evidence_ids=['evidence-1'])
    ])])


def fixture_review():
    return dict(analysis_id='fixture-analysis',review_schema_version=4,projects=[dict(
        name='sample',
        fact_catalog=[dict(id='fact-1',kind='method_change',evidence_ids=['evidence-1'])],
        review_units=[dict(id='RU-1',scope='method',symbol='sample.Flow#run()',
                           change_summary=[dict(kind='method_change',fact_id='fact-1',details={})],
                           direct_fact_ids=['fact-1'],context_fact_ids=[],
                           fact_ids=['fact-1'],relation_ids=[],related_symbols=[],facet_ids=[])])])


def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/(name+'.py'))
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DiffListTest(unittest.TestCase):
    def test_source_index_jdk_tools_discovery(self):
        source_index=module('source_index')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            bin_dir=root/'bin';bin_dir.mkdir()
            suffix='.exe' if os.name=='nt' else ''
            javac=bin_dir/('javac'+suffix)
            java=bin_dir/('java'+suffix)
            javac.touch();java.touch()
            with mock.patch.object(source_index.shutil,'which',return_value=str(javac)):
                self.assertEqual(source_index.jdk_tools(),(str(javac.resolve()),str(java),None))
                lib=root/'lib';lib.mkdir()
                tools=lib/'tools.jar';tools.touch()
                self.assertEqual(source_index.jdk_tools(),(str(javac.resolve()),str(java),str(tools)))
            with mock.patch.object(source_index.shutil,'which',return_value=None):
                with self.assertRaisesRegex(ValueError,'javac not found'):
                    source_index.jdk_tools()

    def test_generated_input_skeletons_use_current_contracts(self):
        contract=module('case_contract')
        data=contract.empty_cases('analysis-1')
        self.assertEqual(data['schema_version'],4)
        self.assertEqual(data['analysis_id'],'analysis-1')
        self.assertEqual(set(data['purpose_cases']),set(contract.PURPOSES))
        self.assertTrue(all(value==[] for value in data['purpose_cases'].values()))

    def test_review_workbench_writes_review_and_input_skeletons(self):
        review=module('review_workbench')
        version=module('analysis_engine').VERSION
        analysis=dict(schema_version=2,analyzer_version=version,analysis_id='analysis-1',
                      projects=[dict(name='sample',change_seeds=[],facts=[],relations=[],
                                    hard_errors=[],stats={})],
                      cross_project_relations=[])
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            source=root/'analysis.json'
            source.write_text(json.dumps(analysis),encoding='utf-8')
            review_path=root/'review.json'
            cases_path=root/'cases.json'
            self.assertEqual(review.main(['--analysis',str(source),'--output',str(review_path),
                '--cases-template',str(cases_path)]),0)
            self.assertTrue(review_path.is_file())
            self.assertEqual(json.loads(review_path.read_text(encoding='utf-8'))['review_schema_version'],4)
            self.assertEqual(json.loads(cases_path.read_text(encoding='utf-8'))['analysis_id'],'analysis-1')

    def test_template_heading_only(self):
        template=(ROOT/'assets'/'diff-list-template.md').read_text(encoding='utf-8')
        self.assertTrue(all(not line.strip() or line.startswith('# ') or line.startswith('## ')
                            or line.startswith('### ') or line.startswith('**既有功能影响：**')
                            for line in template.splitlines()))
        self.assertEqual(len([x for x in template.splitlines() if x.startswith('#')]),13)

    def test_generate_and_validate_report_structure(self):
        val=module('validate_report');generator=module('generate_report')
        preflight=module('delivery_preflight')
        template=(ROOT/'assets'/'diff-list-template.md').read_text(encoding='utf-8')
        sha='a'*40
        analysis={'projects':[{'name':'webapi','facts':[
                                  {'kind':'configuration_file_change','data':{
                                      'path':'application.properties','key':'pay.enabled',
                                      'change':'modified','before':'false','after':'true'}}
                              ],'git':{'selection':[{'type':'commit','sha':sha}]}}]}
        draft=generator.initial_report(template,analysis)
        self.assertIn(generator.AGENT_PLACEHOLDER,draft)
        self.assertIn('- webapi='+sha,draft)
        self.assertIn('pay.enabled modified',draft)
        self.assertIn('before=false | after=true',draft)
        replacements={
            '### 2.1 需求改动':'修改支付入口。',
            '### 2.2 表字段 DDL 修改':'无',
            '### 2.3 表配置 DML 准备清单':'无',
            '### 3.1 整体流程说明':'请求后检查支付状态。',
            '### 3.2 Mermaid 流程图':'~~~mermaid\nflowchart TD\n A[请求] --> B[状态]\n~~~',
            '## 4. 特殊逻辑与测试关注点':'**既有功能影响：** 无'
        }
        for heading,value in replacements.items():
            if heading=='## 4. 特殊逻辑与测试关注点':
                draft=draft.replace('**既有功能影响：** '+generator.AGENT_PLACEHOLDER,value,1)
            else:
                draft=draft.replace(heading+'\n\n'+generator.AGENT_PLACEHOLDER,
                                    heading+'\n\n'+value,1)
        self.assertEqual(preflight.report_errors(draft,template),[])
        case_data={'purpose_cases':{'acceptance':[{}],'core_regression':[],'related_regression':[],'boundary':[]}}
        refreshed=generator.refresh_report(draft,template,analysis,case_data,'webapi-cases.xlsx')
        self.assertIn('Excel：[webapi-cases.xlsx](webapi-cases.xlsx)',refreshed)
        for value in replacements.values():
            self.assertIn(value,refreshed)
        self.assertIn('未执行构建、单元测试、数据库验证或环境联调',refreshed)

        finalized=refreshed.replace(generator.DRAFT_MARKER+'\n'+generator.DRAFT_STATUS+'\n','',1)
        errors,_=val.validate(finalized,template,['webapi='+sha])
        self.assertEqual(errors,[])

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

    def test_failed_clone_cleans_its_own_temporary_directory(self):
        access=module('repo_access')
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ,{'LOCALAPPDATA':td}), \
                 mock.patch.object(access,'run_git',side_effect=ValueError('clone failed')):
                with self.assertRaisesRegex(ValueError,'clone failed'):
                    access.prepare_repo('https://github.com/example/inventory')
            root=Path(td)/'diff-list'/'runs'
            self.assertTrue(root.exists())
            self.assertEqual(list(root.iterdir()),[])

    def test_local_repository_path_is_cloned_without_checkout(self):
        access=module('repo_access')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source=root/'workspace';source.mkdir()
            def g(*args):
                return subprocess.check_output(['git','-C',str(source),*args],text=True).strip()
            g('init','-q');g('config','user.name','Test');g('config','user.email','test@example.com')
            (source/'app.txt').write_text('committed\n',encoding='utf-8')
            g('add','.');g('commit','-qm','base')
            g('remote','add','origin','https://gitlab.example/group/webapi3.0.git')
            local_appdata=root/'local-appdata'
            self.assertEqual(access.repository_name(str(source)),'webapi3.0')
            with mock.patch.dict(os.environ,{'LOCALAPPDATA':str(local_appdata)}):
                clone,name=access.prepare_repo(str(source))
            self.assertEqual(name,'webapi3.0')
            self.assertEqual(subprocess.check_output(['git','-C',str(clone),'rev-parse','HEAD'],text=True).strip(),g('rev-parse','HEAD'))
            self.assertFalse((clone/'app.txt').exists())

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
        self.assertEqual(access.parse_repo_url('https://gitlab.example.org/team/inventory/service'),
                         ('gitlab.example.org', 'team/inventory', 'service',
                          'https://gitlab.example.org/team/inventory/service.git'))
        for bad in ('http://user:pass@gitlab.example/group/project',
                    'https://gitlab.example/group/../project',
                    'https://gitlab.example/group/project?token=secret',
                    'file:///group/project'):
            with self.assertRaises(ValueError):
                access.parse_repo_url(bad)

    def test_collect_direct_revisions_without_obsolete_cli(self):
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
            data=collector.collect(dict(name='project',path=str(root),revisions=[commit]))
            self.assertEqual(data['selection'][0]['sha'],commit)
            self.assertFalse(hasattr(collector,'main'))
            with self.assertRaisesRegex(ValueError,'exactly one'):
                collector.collect(dict(name='project',path=str(root),revisions=[commit],
                                       range=dict(base=commit,head=commit)))

    def test_report_requires_existing_function_impact(self):
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
                                 '新字段对旧调用方可能有影响。').replace(
                                 '【Agent填写：基于完整复核事实形成业务结论；无相关内容时填写“无”】',
                                 '旧调用方可能忽略该字段，需验证已有调用方行为。')
        errors,_=val.validate(corrected,template,[])
        self.assertEqual(errors, [])


    def test_bare_existing_function_impact_line_is_rejected(self):
        validator=module('validate_report')
        template=(ROOT/'assets'/'diff-list-template.md').read_text(encoding='utf-8')
        preflight=module('delivery_preflight')
        from generate_report import initial_report, AGENT_PLACEHOLDER
        sha='a'*40
        analysis={'projects':[{'name':'sample','facts':[],
                              'git':{'selection':[{'type':'commit','sha':sha}]}}]}
        draft=initial_report(template,analysis)
        for title in ('### 2.1 需求改动','### 2.2 表字段 DDL 修改',
                      '### 2.3 表配置 DML 准备清单','### 3.1 整体流程说明',
                      '### 3.2 Mermaid 流程图'):
            draft=draft.replace(title+'\\n\\n'+AGENT_PLACEHOLDER,title+'\\n\\n无')
        draft=draft.replace(AGENT_PLACEHOLDER,'')
        self.assertTrue(any('既有功能影响' in error
                            for error in preflight.report_errors(draft,template)))
        self.assertTrue(any('既有功能影响' in error
                            for error in validator.validate(draft.replace(
                                '<!-- diff-list-status: draft -->','').replace(
                                '**报告状态：未完成。** 业务章节待 Agent 复核，校验通过前不作为正式交付。',''),
                                template,[])[0]))

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

    def test_unicode_paths_and_config_capture(self):
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
            java.write_text('class Pay { void run(){ retry(); } void retry(){} }\n',encoding='utf-8')
            cfg.write_text('retry=true\n',encoding='utf-8')
            git('add','.');git('commit','-qm','selected')
            head=git('rev-parse','HEAD')
            for spec in ({'revisions':[head]}, {'range':{'base':base,'head':head}}):
                result=collector.collect({'name':'demo','path':str(root),**spec},capture_config=True)
                net=result['net_effect']
                self.assertEqual(net['status'],'composed')
                self.assertIn('订单服务.java',net['changed_files'])
                self.assertIn('渠道配置.properties',net['changed_files'])
                self.assertEqual([p['path'] for p in net['config_sources']],['渠道配置.properties'])
                self.assertFalse(net['config_skipped'])

    def test_report_rejects_machine_local_paths_without_legacy_footer(self):
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
        report=report.replace("**既有功能影响：** 【Agent填写：基于完整复核事实形成业务结论；无相关内容时填写“无”】",'**既有功能影响：** 无')
        errors,_=val.validate(report,template,['webapi='+sha])
        self.assertEqual(errors,[])
        errors,_=val.validate(report+'\nC:\\work\\Main.java',template,['webapi='+sha])
        self.assertTrue(any('Windows 本机绝对路径' in err for err in errors))

    def test_completed_run_cleanup_deletes_only_isolated_clones(self):
        access=module('repo_access')
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ,{'LOCALAPPDATA':td}):
                base=Path(td)/'diff-list'/'runs'
                clones=[base/'run-a'/'repo',base/'run-b'/'repo']
                for clone in clones:
                    (clone/'.git').mkdir(parents=True)
                report=Path(td)/'analysis.json'
                report.write_text(json.dumps({'projects':[{'analysis_clone':str(p)} for p in clones]}),encoding='utf-8')
                # 运行完成时的用户环境变量可以已经改变；记录的路径仍应可清理。
                with mock.patch.dict(os.environ,{'LOCALAPPDATA':str(Path(td)/'other-session')}):
                    self.assertEqual(access.cleanup_analysis_clones(report),2)
                self.assertTrue(all(not clone.parent.exists() for clone in clones))

    def test_clone_cleanup_rejects_external_or_mixed_directories(self):
        access=module('repo_access')
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ,{'LOCALAPPDATA':td}):
                protected=Path(td)/'source'/'repo'
                (protected/'.git').mkdir(parents=True)
                owned=Path(td)/'diff-list'/'runs'/'run-a'/'repo'
                (owned/'.git').mkdir(parents=True)
                source=Path(td)/'analysis.json'
                source.write_text(json.dumps({'projects':[
                    {'analysis_clone':str(owned)},{'analysis_clone':str(protected)}]}),encoding='utf-8')
                with self.assertRaisesRegex(ValueError,'unsafe clone cleanup'):
                    access.cleanup_analysis_clones(source)
                self.assertTrue(protected.exists())
                self.assertTrue(owned.exists())

    def test_clone_cleanup_rejects_symlinked_repository(self):
        access=module('repo_access')
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)/'diff-list'/'runs'
            real=Path(td)/'actual-repo';(real/'.git').mkdir(parents=True)
            linked=base/'run-link'/'repo';linked.parent.mkdir(parents=True)
            try:
                linked.symlink_to(real,target_is_directory=True)
            except (OSError,NotImplementedError):
                self.skipTest('creating directory symlinks requires OS permission')
            source=Path(td)/'analysis.json'
            source.write_text(json.dumps({'projects':[{'analysis_clone':str(linked)}]}),
                              encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'unsafe clone cleanup'):
                access.cleanup_analysis_clones(source)
            self.assertTrue(real.exists())

    def test_range_long_paths_capture_config(self):
        collector=module('collect_git')
        with tempfile.TemporaryDirectory() as td:
            parent=Path(td).resolve()
            root=parent/('repo-'+'x'*max(1,140-len(str(parent))-6))
            root.mkdir()
            def git(*args,stdin=None):
                return subprocess.check_output(
                    ['git','-c','core.longpaths=true','-C',str(root),*args],
                    input=stdin.encode('utf-8') if stdin is not None else None
                ).decode('utf-8').strip()
            git('init','-q')
            git('config','user.name','Test')
            git('config','user.email','test@example.com')
            git('config','core.longpaths','false')
            java_path='src/test/java/'+'nested/'*12+'LongPathRegression.java'
            config_path='src/main/resources/'+'nested/'*12+'application.properties'
            before={java_path:'class LongPathRegression { int value = 1; }\n',
                    config_path:'enabled=false\n'}
            after={java_path:'class LongPathRegression { int value = 2; }\n',
                   config_path:'enabled=true\n'}
            def commit_sources(sources,parent_sha=None):
                for name,content in sources.items():
                    blob=git('hash-object','-w','--stdin',stdin=content)
                    git('update-index','--add','--cacheinfo','100644,'+blob+','+name)
                tree=git('write-tree')
                args=('-p',parent_sha) if parent_sha else ()
                return git('commit-tree',tree,*args,stdin='fixture\n')
            base=commit_sources(before)
            head=commit_sources(after,base)
            result=collector.collect(
                {'name':'demo','path':str(root),'isolated_no_checkout':True,
                 'range':{'base':base,'head':head}},capture_config=True)['net_effect']
            self.assertIn(java_path,result['changed_files'])
            self.assertEqual(result['config_skipped'],[])
            self.assertEqual(result['config_sources'],[
                {'path':config_path,'before':before[config_path],'after':after[config_path]}])



class AdditionalRegressionTests(unittest.TestCase):
    def test_config_properties_yaml_xml_changes_and_secret_mask(self):
        config = module('config_changes')
        pairs = [
          {'path':'profile/test.properties','before':'refund.retry.enabled=false\nsecret.token=abc123\n','after':'refund.retry.enabled=true\nsecret.token=def456\n'},
          {'path':'app.yml','before':'payment:\n  retry: false\n','after':'payment:\n  retry: true\n'},
          {'path':'app.xml','before':'<beans><property name="retry" value="0"/></beans>',
           'after':'<beans><property name="retry" value="1"/></beans>'},
        ]
        findings, skipped = config.extract_config_changes(pairs)
        self.assertEqual(skipped, [])
        self.assertEqual(len(findings),4)
        self.assertTrue(any(x['key']=='refund.retry.enabled' and x['after']=='true' for x in findings))
        self.assertTrue(any(x['key']=='payment.retry' for x in findings))
        secret = next(x for x in findings if x['key']=='secret.token')
        self.assertEqual(secret['before'],'<redacted>')
        self.assertNotIn('abc123',str(findings))

    def test_config_unknown_yaml_is_skipped_not_silent(self):
        config = module('config_changes')
        findings, skipped = config.extract_config_changes([{'path':'app.yml',
          'before':'urls:\n - http://localhost\n', 'after':'urls:\n - http://example.com\n'}])
        self.assertEqual(findings,[])
        self.assertEqual(len(skipped),1)
        self.assertFalse(skipped[0]['review_required'])
        self.assertEqual(skipped[0]['kind'],'non_runtime_yaml')

    def test_invalid_new_xml_is_not_masked_by_unmapped_old_xml(self):
        config=module('config_changes')
        changes,issues=config.extract_config_changes([{
            'path':'src/main/resources/config.xml',
            'before':'<beans><bean id="x"/></beans>',
            'after':'<beans><property name="broken"></beans>'}])
        self.assertEqual(changes,[])
        self.assertEqual(len(issues),1)
        self.assertEqual(issues[0]['kind'],'parse_failure')
        self.assertIn('invalid XML',issues[0]['reason'])
        self.assertIn('after:',issues[0]['reason'])
        self.assertTrue(issues[0]['review_required'])

    def test_git_ancestor_exit_codes(self):
        collector=module('collect_git')
        for code,expected in ((0,True),(1,False)):
            with mock.patch.object(collector.subprocess,'run',
                                   return_value=mock.Mock(returncode=code,stderr=b'')):
                self.assertIs(collector.is_ancestor('sample','old','new'),expected)
        with mock.patch.object(collector.subprocess,'run',
                               return_value=mock.Mock(returncode=128,stderr=b'broken object')):
            with self.assertRaisesRegex(ValueError,'broken object'):
                collector.is_ancestor('sample','old','new')

    def test_runtime_yaml_and_non_config_xml_review_classification(self):
        config = module('config_changes')
        findings, skipped = config.extract_config_changes([
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

    def test_properties_escaped_keys_unicode_and_multiline(self):
        config=module('config_changes')
        slash=chr(92)
        cases=[
            (r'escaped\=key=value',{'escaped=key':'value'}),
            (r'folder\\=value',{'folder\\':'value'}),
            (r'name\ with\ spaces : ok',{'name with spaces':'ok'}),
            (r'code=\u4E2D\u6587',{'code':'中文'}),
            (r'emoji=\uD83D\uDE00',{'emoji':'😀'}),
            ('join=first'+slash+'\n second',{'join':'firstsecond'}),
            ('join=first'+slash*2+'\nother=value',{'join':'first'+slash,'other':'value'}),
            ('join=first'+slash*3+'\n second',{'join':'first'+slash+'second'}),
            ('\ufeffkey=value',{'key':'value'})
        ]
        for content,expected in cases:
            with self.subTest(content=content):
                self.assertEqual(config.properties(content),expected)
        with self.assertRaisesRegex(ValueError,'surrogate pair'):
            config.properties(r'invalid=\uD83D')

    def test_windows_style_runtime_yaml_path_is_not_downgraded(self):
        config=module('config_changes')
        changed,issues=config.extract_config_changes([{
            'path':r'src\main\resources\application.yml',
            'before':'options:\n  - one\n',
            'after':'options:\n  - two\n'}])
        self.assertEqual(changed,[])
        self.assertEqual(issues[0]['kind'],'parse_failure')
        self.assertTrue(issues[0]['review_required'])

    def test_config_range_and_one_commit_have_equal_contents(self):
        collector=module('collect_git')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'repo';root.mkdir()
            def git(*args):
                return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
            git('init','-q');git('config','user.name','Test');git('config','user.email','test@example.com')
            cfg=root/'application.properties'
            cfg.write_bytes(b'\xef\xbb\xbfvalue=old\n')
            git('add','.');git('commit','-qm','base')
            base=git('rev-parse','HEAD')
            cfg.write_bytes(b'\xef\xbb\xbfvalue=new\n')
            git('add','.');git('commit','-qm','change')
            head=git('rev-parse','HEAD')
            ranged=collector.collect(dict(name='repo',path=str(root),range=dict(base=base,head=head)),capture_config=True)
            isolated=collector.collect(dict(name='repo',path=str(root),revisions=[head]),capture_config=True)
            self.assertEqual(ranged['net_effect']['config_sources'],
                             isolated['net_effect']['config_sources'])
            self.assertEqual(ranged['net_effect']['config_sources'][0]['before'],'value=old\n')

    def test_git_zero_delimited_names_and_invalid_utf8_rejection(self):
        collector=module('collect_git')
        access=module('repo_access')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'repo';root.mkdir()
            def git(*args):
                return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
            git('init','-q');git('config','user.name','Test');git('config','user.email','test@example.com')
            filename='中文名称.java' if os.name=='nt' else '中文'+chr(10)+'名称.java'
            (root/filename).write_text('class Source {}',encoding='utf-8')
            git('add','.');git('commit','-qm','initial')
            base=git('rev-parse','HEAD')
            (root/filename).write_text('class Source {int value;}',encoding='utf-8')
            git('add','.');git('commit','-qm','update')
            head=git('rev-parse','HEAD')
            self.assertEqual(collector.paths(root,base,head),[filename])
            self.assertEqual(access.decode_git_path(filename.encode('utf-8')),filename)
            self.assertEqual(collector.git_null_fields(b'normal'+bytes([10])+b'name.java'+bytes([0])),['normal'+chr(10)+'name.java'])
            self.assertNotIn('\ufffd',collector.paths(root,base,head)[0])
            invalid=bytes([255,46,106,97,118,97])
            with mock.patch.object(access.os,'fsdecode',
                                   side_effect=UnicodeDecodeError('utf-8',invalid,0,1,'invalid')):
                with self.assertRaisesRegex(ValueError,'hex=ff2e6a617661'):
                    access.decode_git_path(invalid)
            if os.name=='nt':
                # 在真实 Windows 文件系统编解码器下，不允许泄漏 UnicodeDecodeError。
                try:
                    decoded=access.decode_git_path(invalid)
                    self.assertEqual(os.fsencode(decoded),invalid)
                    decoded.encode('utf-8')
                except ValueError as exc:
                    self.assertIn('hex=ff2e6a617661',str(exc))

    def test_final_evidence_verifier_rejects_unsafe_git_path(self):
        verifier=module('validate_analysis')
        sha='a'*40
        invalid=(b'100644 blob '+b'b'*40+bytes([9,255])+b'.java'+bytes([0]))
        with mock.patch.object(verifier,'git_bytes',
                               side_effect=[(sha+'\n').encode('ascii'),invalid]), \
             mock.patch('repo_access.os.fsdecode',
                        side_effect=UnicodeDecodeError('utf-8',bytes([255]),0,1,'invalid')):
            with self.assertRaisesRegex(ValueError,'hex=ff2e6a617661'):
                verifier.verify_evidence(
                    dict(analysis_clone='unused',snapshots={'result':sha}),[])

    def test_xml_config_mapping_add_remove_is_not_ignored(self):
        config = module('config_changes')
        removed, skipped = config.extract_config_changes([{
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

        added, skipped = config.extract_config_changes([{
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
                findings,skipped=module('config_changes').extract_config_changes(pairs)
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
        content=content.replace("**既有功能影响：** 【Agent填写：基于完整复核事实形成业务结论；无相关内容时填写“无”】",'**既有功能影响：** 无')
        errors,_=validator.validate(content,template,['webapi='+one,'adpts='+two])
        self.assertEqual(errors,[])
        errors,_=validator.validate(content,template,['webapi='+two])
        self.assertTrue(any('对应关系' in x for x in errors))

    def export_input(self,count=1):
        from case_contract import PURPOSES
        groups={purpose:[] for purpose in PURPOSES}
        for index in range(count):
            groups['acceptance'].append(dict(
                case_id='A-{:03d}'.format(index+1),
                title='退款场景{}'.format(index+1),
                preconditions='准备有效退款数据',
                steps=['发起请求','检查响应'],
                expected=['接口返回退款结果'],
                observable_evidence=[dict(surface='接口',target='返回体',assertion='refundStatus=SUCCESS')],
                review_unit_ids=['RU-1']))
        return dict(schema_version=4,analysis_id='fixture-analysis',purpose_cases=groups)

    def test_export_test_cases_xlsx_structure(self):
        import zipfile
        from xml.etree import ElementTree as ET
        data=self.export_input(2)
        data['purpose_cases']['acceptance'][1]['steps']=['=1+2']
        exporter=module('export_test_cases');rows=exporter.normalize(data, fixture_analysis(), fixture_review())
        self.assertEqual(rows['purposes']['acceptance'][0][3],'1. 发起请求\n2. 检查响应')
        with tempfile.TemporaryDirectory() as td:
            target=Path(td)/'cases.xlsx'
            self.assertEqual(exporter.export(rows,target)['total'],2)
            with zipfile.ZipFile(target) as archive:
                self.assertIsNone(archive.testzip())
                root=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
                ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                workbook_rows=root.findall('.//s:row',ns)
                self.assertEqual(len(workbook_rows),3)
                self.assertEqual(len(workbook_rows[0].findall('s:c',ns)),5)
                self.assertIn('=1+2',archive.read('xl/worksheets/sheet1.xml').decode('utf-8'))
                self.assertFalse(root.findall('.//s:f',ns))
            with self.assertRaises(FileExistsError):exporter.export(rows,target)

    def test_export_rejects_incomplete_current_cases(self):
        exporter=module('export_test_cases')
        for field,value in [('case_id',''),('steps',[]),('preconditions',''),('expected',[])]:
            data=self.export_input();data['purpose_cases']['acceptance'][0][field]=value
            with self.assertRaises(ValueError):exporter.normalize(data,fixture_analysis(),fixture_review())

    def test_invalid_case_shapes_and_extra_fields_are_rejected(self):
        exporter=module('export_test_cases')
        for data in ({'cases':[]},{'purpose_cases':[]}):
            with self.assertRaises(ValueError):exporter.normalize(data,fixture_analysis(),fixture_review())
        data=self.export_input();data['purpose_cases']['acceptance'][0]['unexpected_field']=[]
        with self.assertRaisesRegex(ValueError,'unsupported fields'):
            exporter.normalize(data,fixture_analysis(),fixture_review())

    def test_delivery_commands_require_current_analysis_material(self):
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/'cases.xlsx'
            for script,args in [('export_test_cases.py',['--input','cases.json','--output',str(out)]),
                                ('validate_report.py',['report.md'])]:
                result=subprocess.run([sys.executable,'-B',str(ROOT/'scripts'/script),*args],capture_output=True)
                self.assertEqual(result.returncode,2)
                self.assertIn(b'--analysis',result.stderr)
                self.assertFalse(out.exists())

    def test_report_excel_summary_requires_current_counts(self):
        validator=module('validate_report');template=(ROOT/'assets'/'diff-list-template.md').read_text(encoding='utf-8')
        summary='Excel：cases.xlsx；需求验收用例数量：1；既有核心流程回归用例数量：0；关联回归用例数量：0；业务规则与边界验证用例数量：0；总用例数量：1'
        report=template.replace('## 5. 测试用例导出','## 5. 测试用例导出\n'+summary)
        errors,_=validator.validate(report,template,[])
        self.assertFalse(any('测试用例导出章节' in err for err in errors))
        errors,_=validator.validate(report.replace('总用例数量：1','总用例数量：两条'),template,[])
        self.assertTrue(any('总用例数量' in err for err in errors))

    def test_four_sheets_keep_empty_groups_and_valid_xml(self):
        import zipfile
        from xml.etree import ElementTree as ET
        from case_contract import PURPOSE_NAMES
        exporter=module('export_test_cases');rows=exporter.normalize(self.export_input(),fixture_analysis(),fixture_review())
        with tempfile.TemporaryDirectory() as td:
            target=Path(td)/'cases.xlsx';counts=exporter.export(rows,target)
            self.assertEqual((counts['acceptance'],counts['core_regression'],counts['total']),(1,0,1))
            with zipfile.ZipFile(target) as archive:
                for name in archive.namelist():
                    if name.endswith(('.xml','.rels')):ET.fromstring(archive.read(name))
                ns={'x':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                workbook=ET.fromstring(archive.read('xl/workbook.xml'))
                self.assertEqual([item.attrib['name'] for item in workbook.findall('.//x:sheet',ns)],list(PURPOSE_NAMES))
                for n in range(1,5):
                    sheet=ET.fromstring(archive.read('xl/worksheets/sheet{}.xml'.format(n)))
                    self.assertEqual(len(sheet.findall('.//x:row',ns)),2 if n==1 else 1)

    def test_duplicate_case_ids_are_rejected(self):
        data=self.export_input(2);rows=data['purpose_cases']['acceptance']
        rows[1]['case_id']=rows[0]['case_id']
        with self.assertRaisesRegex(ValueError,'duplicate or multiline case_id'):
            module('export_test_cases').normalize(data,fixture_analysis(),fixture_review())

    def test_observable_evidence_required_for_current_cases(self):
        exporter=module('export_test_cases')
        for incomplete in ([],[{}],[{'surface':'接口','target':'返回体'}],[{'surface':'数据库','assertion':'status=SUCCESS'}]):
            data=self.export_input();data['purpose_cases']['acceptance'][0]['observable_evidence']=incomplete
            with self.assertRaises(ValueError):exporter.normalize(data,fixture_analysis(),fixture_review())

    def test_vague_assertions_rejected(self):
        exporter=module('export_test_cases')
        for phrase in ('符合预期','处理成功','系统正常','接口成功','退款成功','状态正确','库存正常','权限校验成功','文件上传成功'):
            data=self.export_input();data['purpose_cases']['acceptance'][0]['observable_evidence'][0]['assertion']=phrase
            with self.assertRaisesRegex(ValueError,'not an observable'):exporter.normalize(data,fixture_analysis(),fixture_review())

    def test_nonpayment_observable_assertions_are_specific(self):
        exporter=module('export_test_cases')
        data=self.export_input()
        case=data['purpose_cases']['acceptance'][0]
        case['observable_evidence'][0]=dict(
            surface='接口',target='权限校验接口',assertion='无权限用户返回HTTP 403，记录数保持0')
        result=exporter.normalize(data,fixture_analysis(),fixture_review())
        self.assertIn('HTTP 403',result['purposes']['acceptance'][0][4])

    def test_evidence_channels_visible_in_xlsx_expected_column(self):
        import zipfile
        exporter=module('export_test_cases');channels=('页面','接口','日志','数据库','文件','下游行为','缓存')
        data=self.export_input();case=data['purpose_cases']['acceptance'][0]
        case['observable_evidence'] += [dict(surface=surface,target='订单状态字段',assertion='status=SUCCESS') for surface in channels]
        records=exporter.normalize(data,fixture_analysis(),fixture_review())
        for surface in channels:self.assertIn(surface+'｜订单状态字段：status=SUCCESS',records['purposes']['acceptance'][0][4])
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/'test.xlsx';exporter.export(records,out)
            with zipfile.ZipFile(out) as archive:
                xml=archive.read('xl/worksheets/sheet1.xml').decode('utf-8')
                self.assertIn('可观察证据：',xml);self.assertIn('status=SUCCESS',xml)


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
            result=collector.collect({'name':'webapi3.0','path':str(root),
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
