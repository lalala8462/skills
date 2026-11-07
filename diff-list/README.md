# diff-list

基于指定 Git 修订的最终净变化生成 **Markdown 变更与测试报告**、**四类测试用例 Excel** 和 **coverage.json 复核覆盖清单**。

脚本负责固定 Git 快照、静态取证、复核索引、校验与交付；Agent 根据事实、提交演进和业务上下文判断既有功能影响、风险与测试预期。

## 工作流程

~~~text
指定仓库与修订 → 隔离 clone 固定快照 → 解析最终净变化
→ 静态事实 / 直接关系 / 源码证据（analysis.json、context.jsonl）
→ review unit 索引 + Agent 按需查证 → 测试用例与报告业务章节
→ --resume：预检 → coverage → Excel → 报告刷新 → 证据校验 → 交付
~~~

1. **首次运行** `run_delivery.py`：生成事实底账、复核索引和报告草稿，随后停在 `waiting_agent`。
2. **Agent 复核**：用 `review_query.py --overview`、`--audit` 掌握全局，再按 unit / fact / evidence 回查固定快照中的 before/after 源码；据此直接编辑 `.work/test-cases.json`（四类用例，引用 `review_unit_ids`）和 `.work/report.md` 的业务章节。
3. **恢复交付** `--resume`：依次执行格式预检、用例引用复核、Excel 导出、报告机器章节刷新与证据身份校验。若 `coverage.pending_review` 未清零，会进入 `waiting_coverage_review`，补充引用或用例后再次 resume。
4. **最终交付**：运行目录根部只发布 `report.md`、`test-cases.xlsx`、`coverage.json`；`.work/` 保留完整事实底账与运行状态，成功后自动清理隔离 clone。

脚本只产出确定性的静态事实与结构校验，业务语义、既有功能影响与测试设计由 Agent 判断。`navigation_facets` 只用于阅读排序、`coverage.pending_review` 只表示复核单元尚未被用例引用，都不构成业务覆盖结论。

## 快速使用

环境：Git、Python 3.8+、JDK 8+。分析器目标为 **Java 8 源码语法**，运行 JDK 与目标 Java 源码版本需要区分。

~~~powershell
py -3 .\scripts\run_delivery.py --repo G:\Code\project --rev FULL_SHA
~~~

首次执行输出 `RUN_DIR` 和查询命令。Agent 复核并填写 `.work/test-cases.json`、`.work/report.md`，之后执行：

~~~powershell
py -3 .\scripts\run_delivery.py --resume "<RUN_DIR>"
~~~

若进入 `waiting_coverage_review`，核实未引用 review unit 后补充用例或引用并再次 resume。成功交付时，运行目录根部得到 `report.md`、`test-cases.xlsx`、`coverage.json`。

**参考文档**：[使用指南（完整命令、参数、硬约束、脚本职责）](docs/usage-guide.md) · [分析契约（字段权威）](references/analysis-contract.md) · [业务分析检查清单](references/analysis-checklist.md)
