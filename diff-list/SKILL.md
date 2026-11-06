---
name: diff-list
description: "基于指定仓库与 Git 修订生成最终净变化分析、范围报告和四类测试用例 Excel。"
---

# diff-list

将代码差异分析中的确定性工作固化到脚本：固定 Git 修订、解析 Java/XML/配置、提取最终净变化和一层直接影响，再由 AI 核实业务含义并生成测试用例。

## 执行流程

1. 使用 `scripts/analyze.py` 生成 `analysis.json` 和 `context.jsonl`。
2. 使用 `scripts/review_workbench.py` 按变化方法生成聚合快审材料，并同时生成 `test-cases.json` 与 `report-data.json` 骨架。
3. AI 以 `review_units` 为主要复核入口，必要时再按 fact/evidence 回查 `analysis.json` 和 `context.jsonl`，直接填充四类用例数组。
4. 使用 `scripts/export_test_cases.py` 导出 Excel。
5. AI 只填写固定语义字段的 `report-data.json`，由 `scripts/generate_report.py` 负责 Markdown 标题和顺序。
6. 使用 `scripts/append_run_dirs.py --source analysis.json` 追加本轮隔离目录。
7. 使用 `scripts/validate_report.py --finalize` 完成最终校验和定稿。

## 分析范围

以指定修订形成的最终净变化为底账。脚本提取 Java 类/方法/字段变化、一层直接调用、Mapper/DB、HTTP/RPC/MQ、配置消费者、参数和返回值来源、分支事实、Git 提交说明以及固定源码证据。

调用关系最多 1 层、每侧最多 100 个方法。快审不再通过共享源码 evidence 扩散事实，而是按每个变化方法建立 `review_units`：

- `symbol`：变化方法；
- `related_symbols`：该变化点一层关系直接涉及的方法；
- `fact_groups` / `fact_ids`：该单元直接相关的事实；
- `relation_ids`：直接关系；
- `evidence_ids`：需要复核时使用的源码证据。

`review.json` 固定使用 `review_schema_version=1`。完整事实仍保留在 `analysis.json`。需要核对提交意图时读取 `analysis.json.projects[].git.commit_review`。

## test-cases.json

运行：

```powershell
py -3 .\scripts\review_workbench.py --analysis G:\tmp\analysis-run\analysis.json --output G:\tmp\delivery\review.json --cases-template G:\tmp\delivery\test-cases.json --report-template G:\tmp\delivery\report-data.json
```

脚本同时预生成 `test-cases.json` 和 `report-data.json` 的固定结构。AI 只填充用例数组和报告正文，不重新创建顶层 JSON 结构。

每条用例固定包含：

- `case_id`
- `title`
- `preconditions`
- `steps`：字符串数组
- `expected`：字符串数组
- `observable_evidence`
- `fact_ids`
- `evidence_ids`

四类唯一归属顺序：需求验收、既有核心流程回归、关联回归、业务规则与边界验证。预期结果至少包含一个可观察、可判定的接口、页面、日志、数据库、文件或下游行为结果。

## report-data.json

AI 不写 Markdown 标题，只填写以下固定语义字段：

```json
{
  "sections": {
    "scope": "项目和修订范围",
    "requirements": "最终需求改动",
    "ddl": "无",
    "dml": "无",
    "config": "无",
    "flow": "业务流程说明或无",
    "mermaid": "Mermaid 代码块或无",
    "special": "特殊逻辑与既有功能影响或无",
    "verification": "实际执行过的验证"
  }
}
```

字段正文不得再加入 Markdown 标题。标题、章节顺序以及第 5 章用例数量均由生成器负责。

## 执行约束

较大的 `test-cases.json`、`report-data.json` 必须直接写入文件，不把完整 JSON 拼进 PowerShell、Python 或 JavaScript 命令参数；避免 Windows 命令行长度和执行环境编码能力成为交付步骤。

## 校验

交付校验包括 analysis/review/cases 身份、fact/evidence 归属、固定 Git 快照源码、聚合快审范围以及 Excel/报告用例数量。
