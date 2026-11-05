---
name: diff-list
description: "基于指定仓库与 Git 修订生成最终净变化分析、范围报告和四类测试用例 Excel。"
---

# diff-list

用于把代码差异分析中的机械工作固化到脚本：固定 Git 修订、解析 Java/XML/配置、定位一层直接影响、整理源码事实和证据，再由 AI 核实业务含义并生成测试用例。

## 执行流程

1. 使用 `scripts/analyze.py` 生成 `analysis.json` 和 `context.jsonl`。
2. 使用 `scripts/review_workbench.py` 生成 `fast10-review.json`。
3. AI 基于快审材料生成 `schema_version=3` 的 `test-cases.json`。
4. 使用 `scripts/export_test_cases.py` 导出 Excel。
5. 使用 `scripts/generate_report.py` 生成 Markdown 候选。
6. 使用 `scripts/validate_report.py --finalize` 完成最终校验和定稿。

## 分析范围

以指定修订形成的最终净变化为底账。

脚本提取：

- Java 类、方法、字段变化；
- 变化方法向上、向下各一层直接关系；
- DB/Mapper、HTTP/RPC/MQ、渠道调用；
- 配置项及直接消费者；
- 参数来源变化；
- 返回值和字段来源；
- branch、switch、集合判断等直接源码事实；
- 动态 SQL、自定义注解、无法解析调用等风险事实；
- 提交演进和配置文件变化；
- 固定 baseline/result 快照及源码证据。

调用关系默认最多 1 层、每侧最多 100 个方法。业务关键词仅用于快审材料的可读归组。

## test-cases.json

固定格式：

```json
{
  "schema_version": 3,
  "analysis_id": "本次 analysis_id",
  "purpose_cases": {
    "acceptance": [],
    "core_regression": [],
    "related_regression": [],
    "boundary": []
  }
}
```

每条用例必须包含：

- `case_id`
- `title`
- `preconditions`
- `steps`
- `expected`
- `observable_evidence`
- `fact_ids`
- `evidence_ids`

四类唯一归属顺序：

1. 需求验收
2. 既有核心流程回归
3. 关联回归
4. 业务规则与边界验证

测试步骤必须可直接执行。预期结果至少包含一个可观察、可判定的接口、页面、日志、数据库、文件或下游行为结果。

源码事实型用例引用 `fact_ids` 时，`evidence_ids` 必须来自对应事实。

## 报告

Markdown 使用 `assets/diff-list-template.md`。

- 第 1 章：项目、修订和分析范围；
- 第 2 章：最终生效的需求、DDL/DML、配置变化；
- 第 3 章：必要的整体业务流程；
- 第 4 章：特殊逻辑、既有功能影响和测试关注点；
- 第 5 章：Excel 文件名及四类用例数量；
- 第 6 章：实际执行过的静态分析、构建测试和环境联调。

报告不展示本机源码路径、内部 fact/evidence ID、真实密钥或大段源码。

## 校验

交付校验包括：

- analysis/review/cases 属于同一次分析；
- 用例引用的 fact 存在；
- evidence 属于对应 fact；
- evidence 对应固定 Git 快照中的真实源码；
- 用例只引用当前快审范围中的事实和证据；
- Excel 四类数量与报告一致。

静态校验通过不代表真实业务测试、数据库验证、运行时绑定或渠道联调已完成。
