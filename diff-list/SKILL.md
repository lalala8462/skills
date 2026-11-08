---
name: diff-list
description: "基于 Git 最终净变化生成 Markdown 变更与测试报告、四类测试用例 Excel 和 coverage.json 复核覆盖清单。"
---

# diff-list

## 执行步骤

**硬约束**：以 [README 的职责边界](README.md#脚本与-agent-的职责边界强制约束) 为准：允许基于 Java/AST/框架约定/API 契约做有依据的有限推导，区分已确认、候选、未解析；不得用业务关键词推断风险或测试覆盖，也不得因推导失败丢弃原始变化。业务判断由 Agent 完成。

1. 运行 `scripts/run_delivery.py`，指定 Git 仓库与修订。**默认使用通用规则**；不要主动搜索、猜测或补传项目专用 `rules` 文件，只有用户明确提供规则文件时才使用 `--rules`；不得为尝试或补传规则对同一批 SHA 重复分析。
2. 在运行目录执行 `review_query.py --overview` 和 `--audit`，确认变化范围、未解析关系及硬错误。用 `--unit`、`--query`、`--fact`、`--evidence` 回查具体源码；逐页读取 `next_cursor`。
3. 需要核对提交演进时，调用 `review_query.py --overview --view commits --limit 10`，逐页跟随 `page.next_cursor` 阅读 Git 提交说明；如历史不完整则明确标记范围。结合最终差异、before/after 源码和接口契约复核业务语义、既有功能影响及异常边界。脚本只分页提供提交资料，不自动核对提交意图与实现。
4. 集中编写前运行 `run_delivery.py --run-dir <run> --mark-agent-phase analysis_authoring`，记录 Agent 工作阶段。
5. 填写 `.work/test-cases.json`、`.work/report.md` 和 `.work/review-receipts.json`：六个业务章节、既有功能影响、直接关联的 unresolved 关系必须记录处理状态和真实 unit/fact/evidence 来源。回执只证明引用有效，不证明业务结论正确。每条测试用例必须通过 `unit_assertions` 为每个 `review_unit_id` 显式绑定本 unit 的直接主事实 ID 与具体 `observable_evidence` 下标；这些引用只证明可追溯，不证明业务覆盖。
6. 执行 `run_delivery.py --resume <run>`。状态为 `waiting_coverage_review` 时逐项核实 `pending_review`，补充已有用例引用或有价值的新测试，随后再次 resume。
7. 最终执行 `review_query.py --audit`，检查证据、范围及验证边界；读取成功交付的 Markdown、Excel 和 coverage.json。

命令参数、六项预算、报告硬约束、Agent/脚本职责与数据字段，以[使用指南](docs/usage-guide.md)和其中链接的分析契约为准。
