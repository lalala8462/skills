"""Run: py -3 -m unittest discover -s tests -v"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


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


if __name__=='__main__':
    unittest.main()
