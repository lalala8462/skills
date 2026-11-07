---
name: diff-list
description: "基于指定仓库与 Git 修订生成最终净变化分析、范围报告和四类测试用例 Excel。"
---

# diff-list

## 目标与分工

以指定修订形成的**最终净变化**为范围依据，输出变更报告、测试用例与可复核证据。

- 脚本：固定 Git 快照，收集完整静态事实、直接关系与证据，生成复核索引，验证用例、报告和交付。
- Agent：核验变更前后行为、提交说明与业务预期，判断既有功能影响，编写场景和可观察预期。
- `analysis.json` / `context.jsonl` 是事实底账；`review_units` 是复核入口，`navigation_facets` 是阅读优先级，`coverage.pending_review` 是待核实引用清单。

## 执行流程

1. 使用 `scripts/run_delivery.py` 指定仓库与修订。默认采用通用分析规则；用户提供自定义规则文件时传入 `--rules`。
2. 阅读运行目录中的 `analysis.json`、`review.json` 和首次执行打印的命令。先执行 `review_query.py --overview`、`--audit`，确定变化点、风险与分析边界。
3. 使用 `--unit`、`--query` 查找复核入口，按需展开 `--view facts/relations/db/conditions/evidence`。通过 `--fact` 与 `--evidence --source-lines` 核实固定 Git 快照及 before/after。分页响应的 `next_cursor` 用于继续查阅剩余事实。
4. 直接填写 `.work/test-cases.json` 和 `.work/report.md` 的 Agent 业务章节。测试引用准确的 `review_unit_ids`，每项预期包含可观察、可判定的结果。填写既有功能影响；核实无相关内容时使用“无”。
5. 综合分析开始时执行 `run_delivery.py --run-dir <run> --mark-agent-phase analysis_authoring`，记录 Agent 工作阶段。
6. 执行 `run_delivery.py --resume <run>`。如出现 `waiting_coverage_review`，逐项核实 pending unit，复用实际覆盖该变化的测试或补充场景，再次 resume。
7. 交付前再次运行 `review_query.py --audit`，确认主要变化、未解析依赖、证据与硬错误的处理结果。完成后读取运行目录根部的 `test-cases.xlsx`、`report.md`、`coverage.json`。

## 业务产物与检验

测试固定为四类：`acceptance`（需求验收）、`core_regression`（既有核心流程）、`related_regression`（关联回归）、`boundary`（业务规则和边界）。

`report.md` 的 Agent 章节为：需求改动、DDL、DML、整体流程、Mermaid、特殊逻辑与测试关注点（含既有功能影响）。分析范围、配置变化、用例数量和静态验证说明由脚本刷新。

Agent 重点核对字段与接口契约、状态与数值、配置、数据库及外部副作用、幂等和异常恢复，区分静态证据与真实构建、测试及环境联调结果。

## 参考资料

- [使用指南](docs/usage-guide.md)：入口参数、运行目录、逐步操作、查询命令、恢复与统计。
- [数据契约](references/analysis-contract.md)：各 JSON schema 和证据约束。
- [分析检查清单](references/analysis-checklist.md)：业务影响与测试设计参考。
