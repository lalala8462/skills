---
name: diff-list
description: "基于指定仓库与 Git 修订生成最终净变化分析、范围报告和四类测试用例 Excel。"
---

# diff-list

## 基本原则

最终净变化是唯一范围底账。

脚本负责完整静态事实、直接关系、证据、索引、格式与交付校验；Agent 负责业务语义、业务影响与测试设计。不要因为脚本可以聚类或生成文本，就让它替 Agent 猜业务需求。

任何摘要、facet、navigation 或查询结果都不能删除或替代 analysis.json / context.jsonl 中的完整事实。

## 正常入口

正常流程只使用 scripts/run_delivery.py。

首次运行生成：

- .work/analysis/analysis.json
- .work/analysis/context.jsonl
- .work/review.json
- .work/test-cases.json
- .work/report.md
- .work/run-state.json

Agent 直接编辑 test-cases.json 与 report.md。

resume 先执行 preflight 和 coverage。pending_review 存在时必须逐项判断：已有场景确实覆盖则补充 review_unit_id；否则新增必要用例。pending_review 清零后自动完成 Excel、报告脚本章节刷新和 finalize。

最终根目录只交付：

- test-cases.xlsx
- report.md
- coverage.json

## review.json

当前 review_schema_version=4。

review_units 是变化点级复核入口；review_facets 保存完整精确结构关系；navigation_facets 只是高价值阅读排序。

**navigation_facets 不能作为过滤范围。** 普通 Util/Common/Base/Helper 等共享调用即使不进入导航层，仍保留在完整 review_facets 和 analysis 中。

facet 不代表同一业务需求，代码关系也不等于业务关系。

## 复核方式

先确认所有 review unit 都被考虑，再按风险决定阅读顺序。navigation_facets 可以帮助优先定位高价值关系，但不得据此跳过其他 unit。

当已经发现具体字段、方法、配置、Mapper 或 unit 需要深查时，优先调用 review_query.py：

    py -3 .\scripts\review_query.py --analysis <analysis.json> --review <review.json> --query <关键词>

或：

    py -3 .\scripts\review_query.py --analysis <analysis.json> --review <review.json> --unit <RU-id>

查询只展开显式命中和确定性引用，不做业务相似度聚类。未出现在查询结果里的材料不表示无关。

## test-cases.json

当前 schema_version=4。

固定字段：

- case_id
- title
- preconditions
- steps
- expected
- observable_evidence
- review_unit_ids

四类固定为需求验收、既有核心流程回归、关联回归、业务规则与边界验证。

不要从代码分支机械生成业务测试。测试场景必须由 Agent 基于完整业务行为判断；脚本只负责结构校验、证据绑定和遗漏提示。

## report.md

Agent 直接填写：

- 需求改动
- DDL
- DML
- 整体流程
- Mermaid
- 特殊逻辑与测试关注点，包括既有功能影响

脚本维护：

- 项目与完整 SHA
- 配置文件变化
- Excel 文件名与四类用例数量
- 静态验证说明

resume 时脚本只刷新机器维护章节，Agent 业务章节必须原样保留。

## coverage.json

当前 coverage_schema_version=2。

pending_review 是遗漏复核清单，不是覆盖率门槛，也不证明业务语义或分支覆盖。candidate_case_ids 只是帮助判断已有场景是否可能复用，最终仍由 Agent 决定。

## Agent 子阶段计时

run_schema_version=2。

初次进入 Agent 阶段自动开始 source_review。工作重点变化时切换：

    py -3 .\scripts\run_delivery.py --run-dir <run> --mark-agent-phase business_reasoning
    py -3 .\scripts\run_delivery.py --run-dir <run> --mark-agent-phase test_design
    py -3 .\scripts\run_delivery.py --run-dir <run> --mark-agent-phase report_edit

coverage 暂停自动记录 coverage_review。

run-state.json 分别记录 source_review、business_reasoning、test_design、report_edit、coverage_review 和 agent_between_steps 总耗时，用真实数据决定下一轮优化方向。

## 质量约束

- 最终差异和完整 fact/evidence 不裁剪；
- navigation 只排序，不过滤；
- query 只查证，不归纳业务；
- 不自动生成正式业务变化组；
- 不从代码结构自动声明测试充分；
- unresolved、hard_errors、状态、金额、幂等、接口契约、配置、DB/渠道副作用和异常恢复仍需按风险回查；
- 未实际执行的构建、单元测试、数据库验证或环境联调不得写成已验证。
