---
name: diff-list
description: "基于指定仓库与 Git 修订生成最终净变化分析、范围报告和四类测试用例 Excel。"
---

# diff-list

## 执行流程

1. `scripts/analyze.py` 生成 `analysis.json`、`context.jsonl`。
2. `scripts/review_workbench.py` 生成 `review.json`、`test-cases.json` 骨架和 `report-data.json` 骨架。
3. Agent 先按 `review_groups` 阅读，再按必要的 `review_units` 核实业务含义。
4. Agent 填写四类测试用例；每条用例只写 `review_unit_ids`。
5. `scripts/export_test_cases.py` 自动展开 review unit 对应 fact/evidence，校验后导出 Excel。
6. Agent 只填写报告业务字段；`scripts/generate_report.py` 自动生成范围、配置、特殊逻辑结构、Excel 数量和验证说明。
7. `scripts/append_run_dirs.py` 追加隔离目录，`scripts/validate_report.py --finalize` 定稿。

## 分析范围

最终净变化为唯一范围依据。脚本解析 Java 类/方法/字段、一层直接调用、Mapper/DB、HTTP/RPC/MQ、配置消费者、参数来源、返回值来源、分支事实、提交说明和固定源码证据。

调用关系最多一层、每侧最多 100 个方法。

## review.json

当前 `review_schema_version=2`。

`review_units` 是用例和证据绑定的最小复核单元；`review_groups` 只是机械聚合，用共享类、直接调用、配置键、Mapper、字段等确定性锚点减少重复阅读，不承担业务分类。

Agent 不需要手工筛选或写入 fact/evidence ID。

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

## report-data.json

Agent 只填写：

- `requirements`
- `ddl`
- `dml`
- `flow`
- `mermaid`
- `special_rules`
- `existing_impact`

项目/SHA、配置变化、Excel 数量和本技能静态验证说明由脚本生成。正文不得自行增加 Markdown 标题。

## 执行约束

较大的 JSON 直接写文件，不把完整内容拼进命令行。未实际执行的构建、单元测试、数据库验证或环境联调不得写成已验证。
