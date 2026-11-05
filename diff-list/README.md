# diff-list

默认目标是在 10 分钟内围绕关键数据流完成并交付 Markdown 范围报告和独立测试用例 Excel；每次调用都必须交付完整的所选范围。Excel 沿用既有四类工作表模板。用户要求正式全量阶段时，再完成全部覆盖校验。

10 分钟是范围交付的目标，不是自动停止点。若实际任务超过 10 分钟，可以在超时后给一次清楚标注缺口的阶段草稿，但必须继续直至最终范围报告和用例完成；真实障碍除外。正式全量阶段另要求完整分析、覆盖校验及 Excel，不迁移或接受旧版分析数据。

运行环境：Windows PowerShell、Git、Python 3.8+、JDK 8+。Python 脚本使用标准库；Java 辅助程序只解析语法，不构建目标项目或解析 Maven 外部依赖。仓库输入支持 HTTP(S) URL 或本地 Git 工作区路径，二者都会在 `%LOCALAPPDATA%\diff-list\runs\run-*\repo` 创建新的无检出仓库；本地路径仅提供已提交 Git 对象，未提交工作区改动不进入分析；本地分析目录保留，需后续手动处理。

## 默认流程：10 分钟快审

每次 `$diff-list` 都必须先初始化内部工作材料，再完成并交付范围报告和独立 Excel。默认目标从仓库和修订可用时起 10 分钟，覆盖源码采集、脚本运行、人工复核、报告和 Excel。按 [10 分钟范围审查模板](references/fast10-review-template.md) 使用 `review_workbench.py --fast10` 将入选方法和调用证据归并为不超过 8 个主流程，默认按退款、支付/流水号、通知回调等业务职责聚合。分支与调用关系是支持证据，不各自形成复核项或测试场景；只有会改变关键状态、单号、金额、渠道请求、通知或响应时才拆分场景。普通 DTO 访问器、Builder 写入、无关键数据流的辅助调用及预算/远距离缺口不形成独立复核项。底层事实、义务与证据 ID 仍保留在完整分析材料中；无法归入主流程的关系列为范围外统计，不判定为无影响，也不阻止所选范围完成。

```powershell
# 默认范围流程先生成固定分析与快审材料，不预先创建 Markdown 报告。
py -3 .\scripts\analyze.py --repo https://github.com/example/project --rev FULL_SHA --outdir G:\tmp\analysis-run
py -3 .\scripts\review_workbench.py --analysis G:\tmp\analysis-run\analysis.json --output G:\tmp\delivery\fast10-review.json --fast10
```

`--fast10` 只输出入选义务、最多 4 组绑定问题；每组最多 3 个代表成员完整内容，其余保留义务/证据 ID 并计入范围外；全量候选留在 analysis 材料中，但不会进入默认人工阅读视图；仅按选定范围中的证据 ID 读取对应源码片段，不整份载入 analysis/context。队列只提供候选和证据定位，仍须核实完整的所选业务流程、配置前提、影响及用例。

默认范围报告登记语义不同的关键测试场景。每条都要写清配置/数据前提、可直接执行的步骤和至少一项可观察的确定预期；场景必须覆盖所有入选主流程的关键输入和结果出口。用例只写入独立 Excel；报告第 5 章只列 Excel 文件名、四类数量和总数。报告正文按固定标题保存在 `report-data.json` 的 `sections` 映射中，范围流程顺序如下：

```powershell
# 先生成独立工作簿；参数须指向同一轮分析、review 和 cases。
py -3 .\scripts\export_test_cases.py --input G:\tmp\delivery\test-cases.json --output G:\tmp\delivery\test-cases.xlsx --analysis G:\tmp\analysis-run\analysis.json --review G:\tmp\delivery\fast10-review.json --scope-only
# 如果找到了既有工作簿模板，在上面的导出命令追加 --template G:\path\test-cases-template.xlsx；脚本会保留四表、列宽和单元格样式，并替换旧用例。
# 按固定模板与结构化正文生成内部候选。report-data.json 的 sections 以模板标题为键，不含标题行及第5章。
py -3 .\scripts\generate_report.py --data G:\tmp\delivery\report-data.json --analysis G:\tmp\analysis-run\analysis.json --review G:\tmp\delivery\fast10-review.json --cases G:\tmp\delivery\test-cases.json --excel G:\tmp\delivery\test-cases.xlsx --output G:\tmp\delivery\report.md --scope-only
py -3 .\scripts\append_run_dirs.py G:\tmp\delivery\report.md --source G:\tmp\analysis-run\analysis.json
# 章节、Excel 数量、范围引用和草稿标记全部校验通过后才清除未完成标识。
py -3 .\scripts\validate_report.py G:\tmp\delivery\report.md --analysis G:\tmp\analysis-run\analysis.json --review G:\tmp\delivery\fast10-review.json --cases G:\tmp\delivery\test-cases.json --scope-only --finalize
```

若校验失败，只修正结构化报告/用例来源，再对仍带草稿标识的候选使用 `generate_report.py --replace-draft` 重生成；不从头手写报告。范围外延后义务不使报告变成草稿，但要列出主要类别/数量，并注明“范围内完成；未做全量覆盖校验”。若工作实际需要超过 10 分钟，可在超过 10 分钟后给一次阶段草稿，然后继续至最终范围交付。除真实阻塞外，不在中途停止；也不把范围报告描述为全量覆盖或正式全量校验通过。

## 正式全量流程（完整材料与 Excel）

以下命令从 skill 目录执行，示例路径和修订替换为本次实际值。新增产物拒绝覆盖已有同名文件；`append_run_dirs.py` 只在现有报告末尾追加一次目录清单。

**1. 先创建报告，再固定源码与分析材料**

```powershell
py -3 .\scripts\init_report.py --outdir G:\tmp\delivery --filename report.md
py -3 .\scripts\analyze.py --repo https://github.com/example/project --rev FULL_SHA --outdir G:\tmp\analysis-run
```

正式全量流程仍先创建带未完成标识的内部报告；在所有覆盖、用例和正式校验完成前不作为最终交付。默认范围流程也可复用该初始化文件，但只有真实阻塞才以未完成报告结束。逐批把已核实事实和影响写入文件；范围内无法确定的内容明确标注证据限制，不写“无”。

`--rev SHA[:parent]` 可重复；合并提交需明确 parent。范围模式改用 `--base BASE --head HEAD`；多项目改用 `--manifest projects.json`，格式见 [分析契约](references/analysis-contract.md)。RHF 自定义配置注解和作用域需显式追加 `--rules .\rules\rhf.json`。

离散提交在隔离索引内合成补丁并保留结果树；后续读取 baseline/result 树。合成冲突时保留材料并停止最终行为分析，不扩大提交范围或改用最后一个提交。`analysis.json` 的状态始终为 `needs_review`，退出码 0 只表示采集完成。

新增 `src/test` 方法不作为业务分析起点；新增测试类的字段和类签名不生成业务义务，测试目录不运行补充语法候选扫描。测试目录以外的测试代码需人工识别，生产变更仍须独立追踪。

**2. 登记测试点并编写交付材料**

阅读固定材料并按 [分析检查清单](references/analysis-checklist.md) 补查相关源码。依据已确认需求和本次影响关系，按 [测试点契约](references/test-point-contract.md) 填写 `point-plan.json`，再编译：

```powershell
py -3 .\scripts\review_workbench.py --analysis G:\tmp\analysis-run\analysis.json --output G:\tmp\delivery\review-groups.json
```

分组视图列出必要义务、最多两层上下游关系及未扩展同名字段弱线索；预算边界和远距离预算/解析缺口不生成复核义务。调用关系最多向上、向下扩展 2 层，每个快照最多选取 100 个方法。Builder 字段填充由字段来源事实覆盖，不把每个生成式 setter 拆成绑定复核。`relation_review_batches` 只合并可能影响数据库、渠道、QCM、通知、退款状态或最终响应，且方法、接收者、名称、参数和候选目标一致的绑定问题。只有受影响源码索引失败、变更方法超过限额或关键变更证据无法提取时才阻塞报告。

```powershell
py -3 .\scripts\test_points.py --input G:\tmp\delivery\point-plan.json --analysis G:\tmp\analysis-run\analysis.json --output G:\tmp\delivery\test-points.json
```

编译器规范化条件，按入口、规则结构、观察对象和断言类型确定身份，合并实例数据并选择唯一测试目的；相同身份的预期冲突报错。它不从自然语言自动识别等价规则。没有显式等价依据的不同条件保守保留。

填写 `point-plan.json` 时只维护一份滚动清单。新增候选先查规范身份：同一测试点立即补充其来源、义务、证据和数据组；经源码证实的语义等价在首次确认时登记等价依据。不得先分别形成多批候选、用例或归属，再在编译后整批回头归并。完成首轮清单和归属后编译，并做一次全量覆盖核对；之后只修正新增证据或校验错误所指向的条目，必要时重新编译校验，但不重建全部候选或手工重并已有用例。用例也采用单一滚动清单：新测试点写入时即检查并关联现有兼容用例，完整保留每条测试点及覆盖义务的来源。

可从已编译目录生成未完成的用例骨架：

```powershell
py -3 .\scripts\review_workbench.py --analysis G:\tmp\analysis-run\analysis.json --catalog G:\tmp\delivery\test-points.json --output G:\tmp\delivery\test-cases-draft.json
```

骨架只复制测试点与证据关联，标题、前提、步骤、预期和观察说明留空；必须人工填写并核对实际业务语义后才能校验和导出，不生成豁免或替代源码复核。

模型将完整编译目录嵌入 `test-cases.json.test_point_catalog`，填写 `schema_version=2`、四类 `purpose_cases`、步骤和覆盖关联；另按分析契约填写 `review.json`，并按 [报告规则](references/report-rules.md) 完成初始化的报告。即使没有补查处置，review 也应包含本次 analysis_id 及空 dispositions 数组。每次分析只维护一份工作中的 point-plan、review 和 cases；小改动原位更新，只在检查点生成必要的编译产物，避免每轮保存整份编号快照。静态证据足以回答的问题不额外运行本地模拟或受控探针；仅当探针可能改变关键结论时使用并注明其环境边界。

**3. 校验、导出与目录登记**

```powershell
py -3 .\scripts\validate_analysis.py --analysis G:\tmp\analysis-run\analysis.json --review G:\tmp\delivery\review.json --cases G:\tmp\delivery\test-cases.json
py -3 .\scripts\export_test_cases.py --input G:\tmp\delivery\test-cases.json --output G:\tmp\delivery\test-cases.xlsx --analysis G:\tmp\analysis-run\analysis.json --review G:\tmp\delivery\review.json
py -3 .\scripts\append_run_dirs.py G:\tmp\delivery\report.md --source G:\tmp\analysis-run\analysis.json
# 完成正文和用例后，删除报告中的未完成标识及状态提示，再运行正式交付校验。
py -3 .\scripts\validate_report.py G:\tmp\delivery\report.md --expect project=FULL_40_CHARACTER_SHA --analysis G:\tmp\analysis-run\analysis.json --review G:\tmp\delivery\review.json --cases G:\tmp\delivery\test-cases.json
```

`--expect` 为每个项目/修订重复提供。正式报告校验失败时立即恢复 `<!-- diff-list-status: draft -->` 和“报告状态：未完成”，修正材料后重新校验；不能将未完成报告称为正式报告。确遇无法自行解决的障碍时交付未完成报告，写明障碍、已核实部分、未覆盖内容、未执行验证及后续动作，Excel 未通过校验则明确未交付。源码/修订错误不能用例外绕过。四类都为空时先确认没有遗漏的变更行为，再在报告第5章写“无”并跳过 Excel 导出。

## 输出与归属

| 产物 | 内容 |
| --- | --- |
| `analysis.json` / `context.jsonl` | 固定树、事实与关系、覆盖义务、提交演进、解析边界及源码证据 |
| `point-plan.json` / `test-points.json` | 人工核实的结构输入、编译后的唯一测试点及合并来源 |
| `test-cases.json` / `review.json` | 可执行用例、测试点/源码覆盖关联及补查处置 |
| Markdown / Excel | 用户阅读的业务结论、测试设计与验证边界 |

Excel 按 **需求验收 > 既有核心流程回归 > 关联回归 > 业务规则与边界验证** 设置四个工作表。需求验收覆盖本次功能变更的正常流程；两类回归分别保护受影响的既有主流程和关联功能；异常及边界场景单列。没有正式需求文档时，可按固定源码复核的正常功能登记 `reviewed_change` 来源，并保留事实及证据关联，不自动将需求验收置空。同一点按上述顺序唯一归属，顺序不代表风险或执行优先级；每类允许为空。统一五列为用例编号、用例名称、前置条件、步骤描述、预期结果。导出器补入固定条件、全部登记的数据组和结构断言，不增加内部编号列。

Markdown 使用 [固定模板](assets/diff-list-template.md)，保留全部章节。范围报告和正式全量报告的第 5 章只列独立 Excel 文件名、四类数量和总数，不在 Markdown 中重复完整用例：

```text
Excel：test-cases.xlsx；需求验收用例数量：2；既有核心流程回归用例数量：1；关联回归用例数量：1；业务规则与边界验证用例数量：3；总用例数量：7
```

正文不复制源码片段、内部证据编号、本机源码路径或密钥。报告末尾由脚本生成的分析目录清单是本机路径的例外，仅用于后续清理。

## 限制与当前人工复核要求

不同处理层的限制不能混用：

| 处理层 | 默认限制 |
| --- | --- |
| 全量 Java/XML 索引，每项目每快照 | 10,000 文件，单文件 1 MiB，累计 128 MiB |
| 关联与源码材料 | 调用关系最多 2 层、每侧最多 100 个方法、源码上下文最多 8 MiB |
| 补充语法/配置候选采集 | 变更 Java 最多80个、配置最多100个，单文件300,000字节 |
| 提交历史 | 最多300个提交；范围历史另有8,000,000字节差异限制 |

索引和关联限制由 `--rules` 文件的 limits 配置；补充候选层不随这些值自动扩大。受影响变更 Java 源码索引失败、变更方法超过每侧上限或关键变更证据无法提取时，分析以 `hard_errors` 阻断完整交付。限额之外的远距离预算/解析缺口不生成逐项复核或缺口清单；报告说明采用的固定上限，不能把未扩展范围描述为已核实。

当前实现的处理方式及复核边界：

- 字段/常量变化始终生成必要覆盖项；可确定的直接字段读取和注解访问器消费者参与追踪。反射、外部消费者及复杂别名仍需补查，未找到消费者不表示字段无影响。
- 调用绑定收集继承重载并检查实参兼容性；无法确定的类型保留未解析状态。switch 记录 case/default 及分组标签，合并可能来源；穿透等复杂控制流保留边界，不证明精确执行路径。
- 新测试点校验直接核对可解析的数量、分支、状态集合、switch 条件、参数来源及明确错误结果；业务角色别名使用显式 source_bindings，不靠名称猜测。等价声明也不能绕过源码约束。原样复制 coverage_links 或包装 source.condition 元数据不能替代真实触发条件。

无法解析的复合条件使用明确的 java:表达式 布尔谓词，配置启停尚不自动推断实际取值；这些与其余业务语义仍需源码复核。分析器和测试点编译器仅接受当前版本材料，版本不匹配直接拒绝，不做迁移或降级。

框架注解、访问器、对象复制、跨项目合同匹配均不证明运行时绑定。配置解析覆盖 properties、普通标量 YAML 和 XML property/entry；复杂配置和动态 Mapper 条件需补查。模型补查必须保留来源，不改称脚本已解析。调用图固定最多扩展 2 层、每侧最多 100 个方法，不再通过同返回类型扩展调用图或生成预算边界义务；未解析调用只在自身是关键副作用/状态/响应操作，或返回值流入关键写入、调用、返回时进入复核；普通访问器与 Builder 字段写入由字段来源事实覆盖，不用参数文本触发范围匹配。受影响 Java 文件的语法/索引错误仍作为阻塞错误保留。补充语法扫描在本次进程内按源码去重并批量运行 Java。覆盖校验内部复用已经检查的对象，不复用跨进程或历史 PASS。

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
