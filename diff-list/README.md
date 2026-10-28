# diff-list（Windows 10）

直接输入 GitHub 仓库 URL 与修订号，输出提测 Markdown。无需本机项目路径、manifest 或预先克隆。需要 Git for Windows、Python 3.8+，PowerShell 5.1/7 均可；脚本仅依赖标准库。

## Skill 直接调用

```text
/diff-list https://github.com/lalala8462/webapi
提交：a1b2c3d4、e5f6a7b8
```

多项目分别指定仓库/修订，最后合并同一份文档；也支持范围 `release-1..release-2`。提交号为示例。私有仓库使用 Git for Windows 已配置的 GitHub 凭据，缓存位于 `%LOCALAPPDATA%\diff-list\git-cache`。

## 内部固定脚本

```powershell
py -3 .\scripts\collect_git.py --repo https://github.com/lalala8462/webapi --rev a1b2c3d4 --rev e5f6a7b8 --output G:\tmp\webapi-evidence.json
py -3 .\scripts\collect_git.py --repo https://github.com/lalala8462/adpts --base release-1 --head release-2 --output G:\tmp\adpts-evidence.json
py -3 .\scripts\impact_candidates.py --repo https://github.com/lalala8462/webapi --rev YOUR_SHA --symbol processPayment
py -3 .\scripts\init_report.py --outdir G:\tmp --filename test-report.md
py -3 .\scripts\validate_report.py G:\tmp\test-report.md --expect webapi=YOUR_FULL_40_CHARACTER_SHA
py -3 -m unittest discover -s tests -v
```

`collect_git.py` 的 `net_effect` 字段区分：

- `composed`：在**独立临时 Git 索引**中，仅应用选中提交的补丁，输出机械净差异 `net_diff`、`changed_files`。不包含未选中提交自动带入的其他变化。
- `no_net_change`：选中补丁组合后与基线没有净差异；不应描述中途已撤销的改动。
- `review_required`：选中补丁依赖其他提交、补丁冲突或无法机械合成；不输出假净差异，必须分析差异归属及交叉依赖。

这里的合成结果**并不证明最终业务行为**，更不等于真实 Git 分支结尾的全部代码。即使没有文件级交叉，未选中的其他文件仍可能影响调用链。范围模式直接比较指定两个端点，包含范围内全部提交。

## 文件和边界

- `SKILL.md`：调用和分析主指令（保持简短）。
- `references/report-rules.md`：DDL、DML、配置和提测写作规范。
- `references/analysis-checklist.md`：双向调用与风险检查。
- `scripts/repo_access.py`：自动 GitHub 缓存。
- `scripts/collect_git.py` + `scripts/net_effect.py`：Git 取证、祖先排序、独立索引净差异。
- `scripts/impact_candidates.py`：指定 SHA 的符号字面候选。
- `scripts/init_report.py` + `scripts/validate_report.py`：固定模板与静态校验。
- `assets/diff-list-template.md`：只含标题和空行，保持不变。
- `tests/test_scripts.py`：离线测试。

所有脚本不得触碰用户已有业务仓库；临时 Git 索引和缓存属于工具内部数据。证据 JSON 可能包含敏感代码、密钥，严禁提交仓库或复制到提测文档。输出文件不覆盖既有同名文件。默认 `G:\tmp` 不能访问时明确报告，不静默改路径。

**限制**：文字差异合成不等同 Java 类型绑定或业务正确性；文档静态检查也不代表 Mermaid 实际渲染、数据库环境或联调完成。
