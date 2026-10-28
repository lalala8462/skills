# diff-list（Windows 10）

直接指定 GitHub URL 和 Git 提交号/范围，Skill 输出一个提测 Markdown；无需 manifest 或手动克隆项目。要求 Git for Windows + Python 3.8+（标准库）；启用 Java AST 候选检查还需 **JDK 8 或更新版本**，并让 `javac` 和 `java` 可执行。私有 GitHub 仓库使用本机 Git 凭据。

## 调用示例

```text
/diff-list https://github.com/lalala8462/webapi
修订：a1b2c3d4、e5f6a7b8
```

多项目分别提供 URL/修订，各自执行脚本后将结论合成同一报告。

## 固定脚本（PowerShell 5.1/7）

```powershell
py -3 .\scripts\collect_git.py --repo https://github.com/lalala8462/webapi --rev a1b2c3d4 --rev e5f6a7b8 --output G:\tmp\webapi-evidence.json
py -3 .\scripts\logic_candidates.py --repo https://github.com/lalala8462/webapi --rev a1b2c3d4 --rev e5f6a7b8 --output G:\tmp\webapi-logic.json
py -3 .\scripts\logic_candidates.py --repo https://github.com/lalala8462/adpts --base release-1 --head release-2 --output G:\tmp\adpts-logic.json
py -3 .\scripts\impact_candidates.py --repo https://github.com/lalala8462/webapi --rev YOUR_SHA --symbol processPayment
py -3 .\scripts\init_report.py --outdir G:\tmp --filename test-report.md
py -3 .\scripts\validate_report.py G:\tmp\test-report.md --expect webapi=YOUR_FULL_40_CHARACTER_SHA
py -3 -m unittest discover -s tests -v
```

这些命令由 Skill 执行，用户只需提交 GitHub 地址与修订号。`--rev` 可重复，范围用 `--base/--head`；合并提交需要明确父节点，可写 `--rev SHA:2`。私有仓库凭据不可用时报告错误，不改用 HEAD 或其他源。

## 脚本分工

| 脚本 | 作用 |
| --- | --- |
| `repo_access.py` | 自动 GitHub 克隆/更新隔离缓存（`%LOCALAPPDATA%\diff-list\git-cache`） |
| `collect_git.py` / `net_effect.py` | 按指定提交提取 diff，在临时索引合成净差异，提示未选中提交交叉 |
| `logic_candidates.py` / `java/JavaLogicAst.java` | 使用 JDK Java 编译器 AST 对比变更前后，输出结构化风险候选 |
| `impact_candidates.py` | 指定修订的符号字面引用检索，不进行类型精确绑定 |
| `init_report.py` / `validate_report.py` | 依据固定模板新建文档并校验结构、风险场景标签等 |

## AST 候选规则和限制

针对最终净变化的 `.java` 文件，AST 扫描条件、状态赋值、异常、重试、锁、事务和配置读取。比较同一类/方法/语法节点的结构摘要，输出 `added/removed/modified`、风险类别、优先级、方法与源码行号（**内部证据**），不会自动把候选写进测试文档。只改格式的代码不应生成候选；代码里未改动的旧条件不应被写成新增。

风险优先级是**启发式**（例如状态、锁、事务或异常变化优先），不是实际业务风险的事实结论。AST 不执行 Java 类型检查、重载绑定或框架动态路由；对语法错误、过大文件、非 UTF-8、选中提交合成冲突，输出 `skipped`/`review_required`，不得声称无风险。Java 文件默认单文件最大 300 KB，离散提交最多自动读取 80 个变更 Java 文件（范围模式也受限），需人工分析被跳过的文件。

净差异 `composed/no_net_change/review_required` 只表示补丁合成结果，不证明最终业务行为；未选中提交可能影响间接调用。报告只能写核实过的业务逻辑及配置，源文件位置和证据 JSON 不应进入提测说明。证据可能含敏感内容，须保存在受控本机，不提交到仓库。已有输出文件不覆盖，默认目录为 `G:\tmp`，无法写入时报告错误。
