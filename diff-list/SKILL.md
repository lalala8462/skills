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

## Agent 复核顺序与脚本接口

1. 先用 `review_query.py --analysis <analysis.json> --review <review.json> --overview` 获得全局变化索引，再用 `--audit` 查看结构盲区和 hard_errors；不能只看 navigation_facets。
2. 针对需要核实的问题使用 `--unit <RU-id>`、`--query <字段/方法/配置键/Mapper>` 获取轻量索引；显式通过 `--view facts/relations/evidence/db/calls/conditions` 查询相关证据。分类视图不代表完整业务范围。
3. 根据返回的 fact/evidence ID 使用 `--fact <id>` 和 `--evidence <id> --source-lines 12` 核验固定 Git 快照中的脱敏源码，重要结论需要检查 before/after。
4. 响应 `page.complete=false` 时用 `--cursor <next_cursor>` 继续读取，不得把截取的第一页视为完整；默认 10 条、`--limit` 最大 100。不要主动使用 `--view all` 把大量重复证据一次性送入 Agent 上下文。
5. 完成业务结论、用例和报告后再次 `--audit`，检查未归属主要事实、未索引事实/关系、未解析依赖、证据缺失和分析硬错误。`coverage.pending_review=0` 不能代替这一步。

这三层只是查询深度，不引入新中间产物、不归纳业务，也不替 Agent 判断遗漏风险是否可接受。

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

run_schema_version=3。

初次进入 Agent 阶段自动开始 source_review。工作重点变化时切换：

    py -3 .\scripts\run_delivery.py --run-dir <run> --mark-agent-phase business_reasoning
    py -3 .\scripts\run_delivery.py --run-dir <run> --mark-agent-phase test_design
    py -3 .\scripts\run_delivery.py --run-dir <run> --mark-agent-phase report_edit

coverage 暂停自动记录 coverage_review；resume 的 preflight / coverage / Excel / report / finalize 失败后自动进入 `waiting_rework`，修复期间计入 `agent_rework`，下次 `--resume` 先结算此区间。可用 `--mark-agent-phase report_edit` 等细分修复工作。

`agent_between_steps` 是 source_review、business_reasoning、test_design、report_edit、coverage_review、rework 子阶段的**合计值**，不能与这些子阶段再次相加。读取计时只使用 `TRACKED_TOTAL = SCRIPT_TOTAL + AGENT_TOTAL`。失败脚本的尝试次数和用时保留在对应 stage.attempts。

## 质量约束

- 最终差异和完整 fact/evidence 不裁剪；
- navigation 只排序，不过滤；
- query 只查证，不归纳业务；
- 不自动生成正式业务变化组；
- 不从代码结构自动声明测试充分；
- unresolved、hard_errors、状态、金额、幂等、接口契约、配置、DB/渠道副作用和异常恢复仍需按风险回查；
- 未实际执行的构建、单元测试、数据库验证或环境联调不得写成已验证。
