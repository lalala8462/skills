---
name: diff-list
description: "基于指定仓库与 Git 修订生成最终净变化分析、范围报告和四类测试用例 Excel。"
---

# diff-list

## 执行流程

1. `scripts/analyze.py` 生成 `analysis.json`、`context.jsonl`。
2. `scripts/review_workbench.py` 生成 `review.json`、`test-cases.json` 骨架和 `report-data.json` 骨架。
3. Agent 先看 `review_facets` 和 unit 的 `change_summary`，再按必要的 `fact_ids` 回查完整事实。
4. Agent 填写四类测试用例；每条用例只写 `review_unit_ids`。
5. 运行 `scripts/validate_analysis.py --coverage-output coverage.json` 做结构遗漏检查；只补真正有业务价值的遗漏。
6. `scripts/export_test_cases.py` 自动展开 review unit 对应 fact/evidence，校验后导出 Excel。
7. Agent 只填写报告业务字段；`scripts/generate_report.py` 自动生成固定章节。
8. `scripts/append_run_dirs.py` 追加隔离目录，`scripts/validate_report.py --finalize` 定稿。

## 分析范围

最终净变化为唯一范围依据。脚本解析 Java 类/方法/字段、一层直接调用、Mapper/DB、HTTP/RPC/MQ、配置消费者、参数来源、返回值来源、分支事实、提交说明和固定源码证据。

调用关系最多一层、每侧最多 100 个方法。

## review.json

当前 `review_schema_version=3`。

`review_units` 是用例和证据绑定的最小复核单元。

每个 unit 的 `change_summary` 只摘要直接绑定当前变化点的事实；`direct_fact_ids/context_fact_ids` 与对应 group 统计明确区分直接事实和一层上下文。摘要不得替代 `fact_catalog / analysis.json / context.jsonl`。

`review_facets` 只按单一精确锚点形成可重叠集合，不做传递合并。facet 用于减少重复阅读，不承担业务分类。

## test-cases.json

当前 `schema_version=4`。

每条用例固定字段：

- `case_id`
- `title`
- `preconditions`
- `steps`
- `expected`
- `observable_evidence`
- `review_unit_ids`

四类固定为需求验收、既有核心流程回归、关联回归、业务规则与边界验证。

Agent 不手工写 fact/evidence ID。

## 结构覆盖检查

`coverage.json` 只检查 unit/facet 是否被用例结构引用，不证明业务覆盖。

未引用 unit 或部分覆盖 facet 必须复核，但不要求机械补齐；如果已有业务场景足以覆盖多个变化点，应在同一用例引用对应 unit，避免重复造用例。

## report-data.json

Agent 只填写：

- `requirements`
- `ddl`
- `dml`
- `flow`
- `mermaid`
- `special_rules`
- `existing_impact`

项目/SHA、配置变化、Excel 数量和本技能静态验证说明由脚本生成。

## 质量约束

优化目标是减少重复劳动，不是减少事实覆盖：

- 不删除完整 fact/evidence；
- 不根据 facet 自动得出业务结论；
- 不根据 coverage 自动生成测试；
- unresolved、hard_errors、高风险状态/金额/幂等/接口契约等仍需回查完整材料；
- 未实际执行的构建、单元测试、数据库验证或环境联调不得写成已验证。
