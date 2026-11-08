# diff-list

基于指定 Git 修订的最终净变化生成 **Markdown 变更与测试报告**、**四类测试用例 Excel** 和 **coverage.json 复核覆盖清单**。

脚本负责固定 Git 快照、静态取证、复核索引、校验与交付；Agent 根据事实、提交演进和业务上下文判断既有功能影响、风险与测试预期。

## 一分钟理解：像给装修工程做验收

假设一套房屋装修前后有两版图纸、施工记录和验收要求：

- **Git 版本和提交说明**像装修前后图纸与施工日志；脚本先核对最终到底改了什么，避免把施工中改了又撤销的内容误当作最终成果。
- **分析脚本**像资料整理员：逐项登记变化，整理相关的房间、线路和原始图纸证据。依据明确的施工规范，还能提示*可能关联*的部位，但会把“已确认”“推测候选”“暂时查不清”分开标记。
- **Agent**像验收人员：根据图纸、施工记录和现场要求判断影响，设计应该检查什么，再形成报告与测试清单。脚本负责检查清单是否关联到了真实资料，**不能代替验收人员判断检查是否有效**。

**优势**：不用人工反复翻阅大量提交；以最终差异为底账，减少遗漏；每项分析可以追溯到源码；大量细节可以按需查看，便于复核。

**不足**：它不运行被分析项目，也不真正执行测试；框架、反射、外部系统和运行时条件可能无法完全确认。候选关系和未解析事项需要 Agent 或人工继续核实；“检查清单有对应证据”不等于“业务测试已经充分”。

## 工作流程

~~~text
指定仓库与修订 → 隔离 clone 固定快照 → 解析最终净变化
→ 静态事实 / 直接关系 / 源码证据（analysis.json、context.jsonl）
→ review unit 索引 + Agent 按需查证 → 测试用例与报告业务章节
→ --resume：预检 → coverage → Excel → 报告刷新 → 证据校验 → 交付
~~~

1. **首次运行** `run_delivery.py`：生成事实底账、复核索引和报告草稿，随后停在 `waiting_agent`。
2. **Agent 复核**：先用 `review_query.py --overview`、`--audit` 查看全局单元与异常；提交说明另用 `--overview --view commits --limit 10` 按需分页读取，**该子视图不包含全局统计与回执状态**，且单条提交正文可能较长。随后按 unit / fact / evidence 回查源码，编辑 `.work/test-cases.json`、`.work/report.md` 和 `.work/review-receipts.json`。用例的 `unit_assertions` **必填**：为每个 `review_unit_id` 显式绑定该单元的主事实 `direct_fact_ids` 和 `observable_evidence` 数组下标（详见[使用指南](docs/usage-guide.md#5-用例与报告硬约束)）。
3. **恢复交付** `--resume`：依次执行格式及回执身份预检、用例引用复核、Excel 导出、报告机器章节刷新与证据身份校验。若 `coverage.pending_review` 未清零，会进入 `waiting_coverage_review`，补充 `unit_assertions` 的主事实和断言关联或新增用例后再次 resume；**手工调用 `delivery_preflight.py` 必须指定 `--receipts .work/review-receipts.json`**。本次运行契约升级为 v7，旧运行目录不能直接 `--resume`，需重新执行首次分析以重建 review unit。
4. **最终交付**：运行目录根部只发布 `report.md`、`test-cases.xlsx`、`coverage.json`；`.work/` 保留完整事实底账与运行状态，成功后自动清理隔离 clone。

只修改类继承、注解等结构而方法体不变时，相关方法保留为项目级主变化的上下文，不强制为缺少主事实的方法单独生成测试单元；无法归属的变化会明确报错。

脚本提供静态事实、具有明确依据的有限候选推导和未解析边界，并负责结构与身份校验；业务语义、既有功能影响与测试设计由 Agent 判断。`navigation_facets` 只用于阅读排序、`coverage.pending_review` 只表示复核单元尚未形成显式用例—主事实—断言关联，都不构成业务覆盖结论。


## 脚本与 Agent 的职责边界（强制约束）

> **脚本不判断业务含义、业务风险和测试是否充分。允许基于 Java 语言规则、AST、框架约定及明确 API 契约进行确定性分析或有限推导，但必须区分已确认事实、候选关系和未解析事项。推导结果不得伪装成已确认事实，也不得因为推导失败而丢弃原始变化。**

- **脚本负责重复、可核验的工作**：固定 Git 修订、计算最终净差异、解析 Java/XML/配置结构、提取源码证据、建立关系和索引、统计状态、分页查询及校验引用身份。
- **三种结果必须区分**：`source_resolved` 表示能够按当前静态证据明确解析的源码关系（不代表运行时必然执行）；`convention_inferred` 表示基于 JavaBean、Lombok、MyBatis、API 或框架约定得到的候选关系；`unresolved` 表示目前无法可靠解析。每种状态都应保留来源与局限。
- **合理推导可以保留**：Getter/Setter、BeanUtils、回调、接口实现等分析，只要有具体语言或框架规则作为依据，并准确标记确定程度，就属于准备材料的优化；不能把可能的关系写成已证明的实际调用。
- **不依赖业务关键词猜测**：不能因为名称出现 `refund`、`save`、`status` 等词，就判断业务风险、用途或测试价值，更不能据此丢弃直接相关的未解析变化；普通关键词搜索仅用于定位材料。
- **业务判断交给 Agent 或人工**：提交意图、既有功能影响、重要性与测试设计由 Agent 依据真实代码和上下文复核。回执及 `unit_assertions` 只验证证据 ID、关联与处理状态，不证明结论或测试的业务正确性。
- **不以排序裁剪范围**：`navigation_priority` / `priority_kind=reading_order_heuristic` 只决定阅读顺序；`coverage.pending_review` 只统计缺少显式用例—主事实—断言关联的单元，不等于业务覆盖率。
- **保留未知边界**：推导失败或文件内容暂不支持解析时，保留可取得的原始变化及 `unresolved` / 解析边界，不以提高报告简洁度为由静默忽略。

## 快速使用

环境：Git、Python 3.8+、JDK 8+。分析器目标为 **Java 8 源码语法**，运行 JDK 与目标 Java 源码版本需要区分。

~~~powershell
py -3 .\scripts\run_delivery.py --repo G:\Code\project --rev FULL_SHA
~~~

首次执行输出 `RUN_DIR` 和查询命令。Agent 复核并填写 `.work/test-cases.json`、`.work/report.md` 和 `.work/review-receipts.json`，之后执行：

~~~powershell
py -3 .\scripts\run_delivery.py --resume "<RUN_DIR>"
~~~

若进入 `waiting_coverage_review`，核实未引用 review unit 后补充用例或引用并再次 resume。成功交付时，运行目录根部得到 `report.md`、`test-cases.xlsx`、`coverage.json`。

**参考文档**：[使用指南（完整命令、参数、硬约束、脚本职责）](docs/usage-guide.md) · [分析契约（字段权威）](references/analysis-contract.md) · [业务分析检查清单](references/analysis-checklist.md)
