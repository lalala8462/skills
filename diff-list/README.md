# diff-list（Windows 10）

按指定 Git 修订生成面向测试人员的提测说明。需要 **Git for Windows**、**Python 3.8+**（标准库），可通过 PowerShell 5.1/7 调用；不需要数据库连接，不安装 Python 第三方依赖。分析和撰写由执行 Skill 的助手负责；脚本只固定化**取证、候选检索、初始化和静态文档验证**，不能代替业务语义判断。

## 目录

- `SKILL.md`：主规则（执行时必读）
- `assets/diff-list-template.md`：固定标题模板，只有标题与空行
- `references/analysis-checklist.md`：按需读取的影响与风险检查清单
- `scripts/repo_access.py`：依据 GitHub 仓库 URL 自动创建/更新隔离 Git 缓存
- `scripts/collect_git.py`：只读收集指定提交/范围 diff，提示同路径未选中提交交叉
- `scripts/impact_candidates.py`：指定修订上的字面引用候选检索
- `scripts/init_report.py`：从模板新建 `.md`
- `scripts/validate_report.py`：文档结构/用语静态检查
- `tests/test_scripts.py`：回归测试

## 1. 直接指定 GitHub 仓库和修订号

**无需创建 JSON 配置文件，也无需提前 clone 项目。** 调用 Skill 时直接说明 GitHub 仓库及修订即可：

```text
/diff-list https://github.com/lalala8462/webapi
修订号：a1b2c3d4、e5f6a7b8
```

多项目可一次提出，Skill 会分别取证，最终合并成一份提测 Markdown：

```text
/diff-list
webapi: https://github.com/lalala8462/webapi，修订 a1b2c3d4、e5f6a7b8
adpts: https://github.com/lalala8462/adpts，范围 release-1..release-2
```

修订号仅为格式示例，必须换成真实提交；若仅提供项目名，必须能从当前会话/工作区唯一确认 GitHub 地址，否则需提供 URL。合并提交可使用 `--rev SHA:2` 选择第 2 个父节点（默认非合并提交用唯一父节点）；无法确认正确基线时先询问。对于离散提交，不能默认纳入两次提交之间其他人的修改。

在 Skill 根目录 PowerShell 终端，脚本执行形式如下（由 Skill 自行组织，用户不必手动输入命令）：

```powershell
py -3 .\scripts\collect_git.py --repo https://github.com/lalala8462/webapi --rev a1b2c3d4 --rev e5f6a7b8 --output G:\tmp\webapi-evidence.json
py -3 .\scripts\collect_git.py --repo https://github.com/lalala8462/adpts --base release-1 --head release-2 --output G:\tmp\adpts-evidence.json
py -3 .\scripts\impact_candidates.py --repo https://github.com/lalala8462/webapi --rev YOUR_COMMIT_SHA --symbol isSuccess --symbol processPayment
py -3 .\scripts\init_report.py --outdir G:\tmp --filename diff-list-payment.md
```

脚本自动在 `%LOCALAPPDATA%\diff-list\git-cache\<owner>\<repo>` 准备及更新 Git 缓存。缓存**只供取证，不是用户原有工作区**。私有仓库需要 Git for Windows 已配置有效的 GitHub 认证（如 Git Credential Manager）；如无权限或网络不可达，应报告失败，不改用不明来源代码。

> 证据 JSON 含代码 diff，可能携带敏感配置。仅在受控本机存放，不得直接提交或复制到最终测试文档；已有证据文件不会被覆盖。默认 20 MB diff 上限可用 `--max-diff-bytes` 调整。`impact_candidates.py` 是字面引用检索，不代表已验证 Java 方法绑定。

## 2. 根据指定修订撰写并校验

将分析结论填入新建 Markdown，保持模板所有标题。对删除/废弃配置、间接调用方、需求与实现差异及高风险场景按主 Skill 和参考清单逐项核实。示例校验：

```powershell
py -3 .\scripts\validate_report.py G:\tmp\diff-list-payment.md --expect webapi=YOUR_40_CHARACTER_SHA --expect adpts=YOUR_40_CHARACTER_SHA
py -3 -m unittest discover -s tests -v
```

脚本 `PASS` 仅表示结构、文本和基本约束通过；**并不证明 Mermaid 8.8.3 能完整解析、Git 净行为归属准确、测试预期正确、实际环境已联调**。仍应检查真实修订上的调用链和业务分支。若 G: 盘不存在或不可写，可显式指定其他输出目录；默认不静默回退。

## 安全及兼容

- Python 脚本只读取远程 GitHub 仓库并在隔离缓存中 clone/fetch；只写缓存、`--output` 证据文件或报告初始化路径，不触碰用户已有业务工作区、SQL 和服务。
- 采用 `subprocess` 参数数组，不经 Shell 拼装用户输入；仅使用标准库，文本显式 UTF-8。
- 未安装 Git/Python、GitHub 无访问权限、不可读取仓库或修订、跨分支无法排序、合并提交缺父节点时明确报错。
