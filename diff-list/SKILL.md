---
name: diff-list
description: "基于 Git 最终净变化生成 Markdown 变更与测试报告、四类测试用例 Excel 和 coverage.json 复核覆盖清单。"
---

# diff-list

## 执行步骤

1. 运行 `scripts/run_delivery.py`，指定 Git 仓库与修订。**默认使用通用规则**；不要主动搜索、猜测或补传项目专用 `rules` 文件，只有用户明确提供规则文件时才使用 `--rules`；不得为尝试或补传规则对同一批 SHA 重复分析。
2. 在运行目录执行 `review_query.py --overview` 和 `--audit`，确认变化范围、未解析关系及硬错误。用 `--unit`、`--query`、`--fact`、`--evidence` 回查具体源码；逐页读取 `next_cursor`。
3. 基于最终差异、提交说明、before/after 源码和接口契约复核业务语义、既有功能影响及异常边界；分析器关系是静态证据。
4. 集中编写前运行 `run_delivery.py --run-dir <run> --mark-agent-phase analysis_authoring`，记录 Agent 工作阶段。
5. 填写 `.work/test-cases.json` 与 `.work/report.md` 的 Agent 业务章节。每条测试用例引用真实支撑场景的 `review_unit_ids`，包含至少一个可观察且可判定的验证结果。
6. 执行 `run_delivery.py --resume <run>`。状态为 `waiting_coverage_review` 时逐项核实 `pending_review`，补充已有用例引用或有价值的新测试，随后再次 resume。
7. 最终执行 `review_query.py --audit`，检查证据、范围及验证边界；读取成功交付的 Markdown、Excel 和 coverage.json。

命令参数、六项预算、报告硬约束、Agent/脚本职责与数据字段，以[使用指南](docs/usage-guide.md)和其中链接的分析契约为准。
