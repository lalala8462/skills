# diff-list（Windows 10）

按指定 Git 修订生成面向测试人员的提测说明。需要 **Git for Windows**、**Python 3.8+**（标准库），可通过 PowerShell 5.1/7 调用；不需要数据库连接，不安装 Python 第三方依赖。分析和撰写由执行 Skill 的助手负责；脚本只固定化**取证、候选检索、初始化和静态文档验证**，不能代替业务语义判断。

## 目录

- `SKILL.md`：主规则（执行时必读）
- `assets/diff-list-template.md`：固定标题模板，只有标题与空行
- `references/analysis-checklist.md`：按需读取的影响与风险检查清单
- `scripts/collect_git.py`：只读收集指定提交/范围 diff，提示同路径未选中提交交叉
- `scripts/impact_candidates.py`：指定修订上的字面引用候选检索
- `scripts/init_report.py`：从模板新建 `.md`
- `scripts/validate_report.py`：文档结构/用语静态检查
- `tests/test_scripts.py`：回归测试

## 1. 指定分析范围

在本机单独准备 `manifest.json`（不必提交仓库）：

```json
{
  "projects": [
    {
      "name": "webapi",
      "path": "D:\\work\\webapi",
      "revisions": ["a1b2c3d4", "e5f6a7b8"]
    },
    {
      "name": "adpts",
      "path": "D:\\work\\adpts",
      "range": { "base": "release-1", "head": "release-2" }
    }
  ]
}
```

示例修订仅说明输入格式，**必须替换为真实存在且用户指定的修订**。合并提交用 `"revisions": [{"rev":"完整SHA", "parent":1}]` 显式选择父提交（1 开始）；多个选中提交要位于可比较的祖先链上，不能根据清单顺序判断覆盖关系。显式范围 `base/head` 包含两端之间的所有差异；不要将“离散选中提交”误写为区间。

在 Skill 根目录的 PowerShell 终端执行：

```powershell
py -3 .\scripts\collect_git.py --manifest .\manifest.json --output G:\tmp\diff-list-evidence.json
py -3 .\scripts\impact_candidates.py --repo D:\work\webapi --rev YOUR_COMMIT_SHA --symbol isSuccess --symbol processPayment
py -3 .\scripts\init_report.py --outdir G:\tmp --filename diff-list-payment.md
```

> `collect_git.py` 输出含代码 diff，可能携带敏感配置。证据 JSON 仅在受控本机存放，不得直接提交或复制到最终测试文档；已有输出文件不会被覆盖。默认 20 MB diff 上限可用 `--max-diff-bytes` 调整，以免意外读取过大输出。工作区未提交改动不纳入指定修订的代码事实。`impact_candidates.py` 只是字面命中候选，不能声称精确 Java 类型绑定。

## 2. 根据指定修订撰写并校验

将分析结论填入新建 Markdown，保持模板所有标题。对删除/废弃配置、间接调用方、需求与实现差异及高风险场景按主 Skill 和参考清单逐项核实。示例校验：

```powershell
py -3 .\scripts\validate_report.py G:\tmp\diff-list-payment.md --expect webapi=YOUR_40_CHARACTER_SHA --expect adpts=YOUR_40_CHARACTER_SHA
py -3 -m unittest discover -s tests -v
```

脚本 `PASS` 仅表示结构、文本和基本约束通过；**并不证明 Mermaid 8.8.3 能完整解析、Git 净行为归属准确、测试预期正确、实际环境已联调**。仍应检查真实修订上的调用链和业务分支。若 G: 盘不存在或不可写，可显式指定其他输出目录；默认不静默回退。

## 安全及兼容

- Python 脚本只读 Git 仓库；只写 `--output` 证据文件或报告初始化路径，不触碰业务仓库、SQL 和服务。
- 采用 `subprocess` 参数数组，不经 Shell 拼装用户输入；仅使用标准库，文本显式 UTF-8。
- 本地未安装 Git/Python、不可读取仓库或修订、跨分支无法排序、合并提交缺父节点时明确报错。
