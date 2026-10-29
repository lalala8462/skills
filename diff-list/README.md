# diff-list

按明确的 GitHub/GitLab 仓库和修订进行静态差异分析，生成 Markdown 报告及四类测试用例 Excel。脚本提供固定源码、事实候选、覆盖义务和结构校验；需求解释、规则登记、源码补查和可执行步骤仍由使用者/模型完成，不会一次调用自动生成完整报告与用例。

设计前提：每次使用当前版本重新采集、分析并生成报告；本次的分析、证据、测试点、用例和补查记录成组生成，不读取或迁移历史报告及旧版分析数据。

运行环境：Windows PowerShell、Git、Python 3.8+、JDK 8+。Python 脚本使用标准库；Java 辅助程序只解析语法，不构建目标项目或解析 Maven 外部依赖。每项目在 `%LOCALAPPDATA%\diff-list\runs\run-*\repo` 创建新的无检出仓库；本地分析目录保留，需后续手动处理。

## 标准流程

以下命令从 skill 目录执行，示例路径和修订替换为本次实际值。新增产物拒绝覆盖已有同名文件；`append_run_dirs.py` 只在现有报告末尾追加一次目录清单。

**1. 固定源码与分析材料**

```powershell
py -3 .\scripts\analyze.py --repo https://github.com/example/project --rev FULL_SHA --outdir G:\tmp\analysis-run
py -3 .\scripts\init_report.py --outdir G:\tmp\delivery --filename report.md
```

`--rev SHA[:parent]` 可重复；合并提交需明确 parent。范围模式改用 `--base BASE --head HEAD`；多项目改用 `--manifest projects.json`，格式见 [分析契约](references/analysis-contract.md)。RHF 自定义配置注解和作用域需显式追加 `--rules .\rules\rhf.json`。

离散提交在隔离索引内合成补丁并保留结果树；后续读取 baseline/result 树。合成冲突时保留材料并停止最终行为分析，不扩大提交范围或改用最后一个提交。`analysis.json` 的状态始终为 `needs_review`，退出码 0 只表示采集完成。

**2. 登记测试点并编写交付材料**

阅读固定材料并按 [分析检查清单](references/analysis-checklist.md) 补查相关源码。依据已确认需求和本次影响关系，按 [测试点契约](references/test-point-contract.md) 填写 `point-plan.json`，再编译：

```powershell
py -3 .\scripts\test_points.py --input G:\tmp\delivery\point-plan.json --analysis G:\tmp\analysis-run\analysis.json --output G:\tmp\delivery\test-points.json
```

编译器规范化条件，按入口、规则结构、观察对象和断言类型确定身份，合并实例数据并选择唯一测试目的；相同身份的预期冲突报错。它不从自然语言自动识别等价规则。没有显式等价依据的不同条件保守保留。

模型将完整编译目录嵌入 `test-cases.json.test_point_catalog`，填写 `schema_version=2`、四类 `purpose_cases`、步骤和覆盖关联；另按分析契约填写 `review.json`，并按 [报告规则](references/report-rules.md) 完成初始化的报告。即使没有补查处置，review 也应包含本次 analysis_id 及空 dispositions 数组。

**3. 校验、导出与目录登记**

```powershell
py -3 .\scripts\validate_analysis.py --analysis G:\tmp\analysis-run\analysis.json --review G:\tmp\delivery\review.json --cases G:\tmp\delivery\test-cases.json
py -3 .\scripts\export_test_cases.py --input G:\tmp\delivery\test-cases.json --output G:\tmp\delivery\test-cases.xlsx --analysis G:\tmp\analysis-run\analysis.json --review G:\tmp\delivery\review.json
py -3 .\scripts\append_run_dirs.py G:\tmp\delivery\report.md --source G:\tmp\analysis-run\analysis.json
py -3 .\scripts\validate_report.py G:\tmp\delivery\report.md --expect project=FULL_40_CHARACTER_SHA --analysis G:\tmp\analysis-run\analysis.json --review G:\tmp\delivery\review.json --cases G:\tmp\delivery\test-cases.json
```

`--expect` 为每个项目/修订重复提供。校验失败应修正材料或留存有依据的边界处置；源码/修订错误不能用例外绕过。四类都为空时先确认没有遗漏的变更行为，再在报告第5章写“无”并跳过 Excel 导出。

## 输出与归属

| 产物 | 内容 |
| --- | --- |
| `analysis.json` / `context.jsonl` | 固定树、事实与关系、覆盖义务、提交演进、解析边界及源码证据 |
| `point-plan.json` / `test-points.json` | 人工核实的结构输入、编译后的唯一测试点及合并来源 |
| `test-cases.json` / `review.json` | 可执行用例、测试点/源码覆盖关联及补查处置 |
| Markdown / Excel | 用户阅读的业务结论、测试设计与验证边界 |

Excel 按 **需求验收 > 既有核心流程回归 > 关联回归 > 业务规则与边界验证** 设置四个工作表。需求验收覆盖本次功能变更的正常流程；两类回归分别保护受影响的既有主流程和关联功能；异常及边界场景单列。没有正式需求文档时，可按固定源码复核的正常功能登记 `reviewed_change` 来源，并保留事实及证据关联，不自动将需求验收置空。同一点按上述顺序唯一归属，顺序不代表风险或执行优先级；每类允许为空。统一五列为用例编号、用例名称、前置条件、步骤描述、预期结果。导出器补入固定条件、全部登记的数据组和结构断言，不增加内部编号列。

Markdown 使用 [固定模板](assets/diff-list-template.md)，第5章只列文件及数量：

```text
Excel：test-cases.xlsx；需求验收用例数量：2；既有核心流程回归用例数量：1；关联回归用例数量：1；业务规则与边界验证用例数量：3；总用例数量：7
```

正文不复制源码片段、内部证据编号、本机源码路径或密钥。报告末尾由脚本生成的分析目录清单是本机路径的例外，仅用于后续清理。

## 限制与当前人工复核要求

不同处理层的限制不能混用：

| 处理层 | 默认限制 |
| --- | --- |
| 全量 Java/XML 索引，每项目每快照 | 10,000 文件，单文件 1 MiB，累计 128 MiB |
| 关联与源码材料 | 4 跳、400 方法、8 MiB 片段；基线/最终同一方法共用方法计数 |
| 补充语法/配置候选采集 | 变更 Java 最多80个、配置最多100个，单文件300,000字节 |
| 提交历史 | 最多300个提交；范围历史另有8,000,000字节差异限制 |

索引和关联限制由 `--rules` 文件的 limits 配置；补充候选层不随这些值自动扩大。截断、解析失败和未解析关系必须查看，不能由候选为空推断无影响。

当前实现的处理方式及复核边界：

- 字段/常量变化始终生成必要覆盖项；可确定的直接字段读取和注解访问器消费者参与追踪。反射、外部消费者及复杂别名仍需补查，未找到消费者不表示字段无影响。
- 调用绑定收集继承重载并检查实参兼容性；无法确定的类型保留未解析状态。switch 记录 case/default 及分组标签，合并可能来源；穿透等复杂控制流保留边界，不证明精确执行路径。
- 新测试点校验直接核对可解析的数量、分支、状态集合、switch 条件、参数来源及明确错误结果；业务角色别名使用显式 source_bindings，不靠名称猜测。等价声明也不能绕过源码约束。原样复制 coverage_links 或包装 source.condition 元数据不能替代真实触发条件。

无法解析的复合条件使用明确的 java:表达式 布尔谓词，配置启停尚不自动推断实际取值；这些与其余业务语义仍需源码复核。分析器和测试点编译器仅接受当前版本材料，版本不匹配直接拒绝，不做迁移或降级。

框架注解、访问器、对象复制、跨项目合同匹配均不证明运行时绑定。配置解析覆盖 properties、普通标量 YAML 和 XML property/entry；复杂配置和动态 Mapper 条件需补查。模型补查必须保留来源，不改称脚本已解析。

校验通过只说明当前规则检查的引用与结构声明通过，不证明事实发现完整、业务预期正确、自然语言步骤可执行、Excel 已人工检查或业务测试已运行。Mermaid 校验也不是完整语法解析或渲染验证。

## 维护与验证

- 用例只接受 `schema_version=2`、当前测试点目录及四个 `purpose_cases` 数组；导出始终为四个工作表和五列，不再接受其他历史结构。
- Excel 导出必须提供本次 `analysis/review`；报告校验必须提供本次 `analysis/review/cases`，不提供降级为仅格式检查的交付入口。内部格式检查函数用于编写过程和单元测试，不代表完成交付校验。
- 保留版本、修订、证据和覆盖关联检查，用于发现不同材料之间的不一致；版本标识用于拒绝错误输入，不承担旧数据兼容。
- `collect_git.py`、`logic_candidates.py`、`impact_candidates.py` 的采集/解析能力仍被统一流程复用或用于补查，参数见各自 `--help`。独立补查创建的目录用 `append_run_dirs.py --clone` 明确登记；`--source` 只读取本次统一入口生成的 analysis.json。

在 skill 目录运行离线回归：

```powershell
py -3 -B -m unittest discover -s tests -v
```
