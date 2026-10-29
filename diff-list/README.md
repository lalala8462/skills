# diff-list

按明确的 GitHub/GitLab 仓库和修订进行静态差异分析，生成 Markdown 报告及四类测试用例 Excel。脚本提供固定源码、事实候选、覆盖义务和结构校验；需求解释、规则登记、源码补查和可执行步骤仍由使用者/模型完成，不会一次调用自动生成完整报告与用例。

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

Excel 按 **需求验收 > 既有核心流程回归 > 关联回归 > 业务规则与边界验证** 设置四个工作表。同一点按该顺序唯一归属，顺序不代表风险或执行优先级；每类允许为空。统一五列为用例编号、用例名称、前置条件、步骤描述、预期结果。导出器补入固定条件、全部登记的数据组和结构断言，不增加内部编号列。

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
| 内嵌旧候选采集 | 变更 Java 最多80个、配置最多100个，单文件300,000字节 |
| 提交历史 | 最多300个提交；范围历史另有8,000,000字节差异限制 |

索引和关联限制由 `--rules` 文件的 limits 配置；旧候选层不随这些值自动扩大。截断、解析失败和未解析关系必须查看，不能由候选为空推断无影响。

当前实现的处理方式及复核边界：

- 字段/常量变化始终生成必要覆盖项；可确定的直接字段读取和注解访问器消费者参与追踪。反射、外部消费者及复杂别名仍需补查，未找到消费者不表示字段无影响。
- 调用绑定收集继承重载并检查实参兼容性；无法确定的类型保留未解析状态。switch 记录 case/default 及分组标签，合并可能来源；穿透等复杂控制流保留边界，不证明精确执行路径。
- 新测试点校验直接核对可解析的数量、分支、状态集合、switch 条件、参数来源及明确错误结果；业务角色别名使用显式 source_bindings，不靠名称猜测。等价声明也不能绕过源码约束。原样复制 coverage_links 或包装 source.condition 元数据不能替代真实触发条件。

无法解析的复合条件使用明确的 java:表达式 布尔谓词，配置启停尚不自动推断实际取值；这些与其余业务语义仍需源码复核。分析器和测试点编译器版本为1.1.0，旧版本材料需重新分析、编译，不能混用。

框架注解、访问器、对象复制、跨项目合同匹配均不证明运行时绑定。配置解析覆盖 properties、普通标量 YAML 和 XML property/entry；复杂配置和动态 Mapper 条件需补查。模型补查必须保留来源，不改称脚本已解析。

校验通过只说明当前规则检查的引用与结构声明通过，不证明事实发现完整、业务预期正确、自然语言步骤可执行、Excel 已人工检查或业务测试已运行。Mermaid 校验也不是完整语法解析或渲染验证。

## 兼容与维护

- 旧 `focus_cases/regular_cases` 仍可导出原两个工作表，不执行新测试点去重与四类归属校验。关联1.1.0分析材料时，也须用 coverage_links.trigger_conditions 和 observable_evidence.constraint 满足源码条件/断言；未关联分析材料时保留原格式兼容。
- 更早的 `{"cases":[...]}` 仅保留独立导出兼容，不能直接通过当前分析覆盖校验。
- `collect_git.py`、`logic_candidates.py`、`impact_candidates.py` 可独立运行，具体参数见各自 `--help`。它们会另建分析仓库；全部目录应通过 `append_run_dirs.py --source/--clone` 记入报告。
- 不带 analysis/review 的导出只检查用例结构，不验证固定源码证据。标准交付使用上述完整参数。

在 skill 目录运行离线回归：

```powershell
py -3 -B -m unittest discover -s tests -v
```
