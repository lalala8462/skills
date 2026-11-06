---
name: diff-list
description: "基于指定仓库与 Git 修订生成最终净变化分析、范围报告和四类测试用例 Excel。"
---

# diff-list

## 正常执行入口

正常流程只使用 scripts/run_delivery.py。

首次运行指定仓库和修订，脚本自动建立独立 runs/<timestamp>/ 目录并完成静态分析和复核材料生成。随后 Agent 编辑 .work/test-cases.json 与 .work/report-data.json，再使用同一脚本 --resume。

resume 会先执行统一格式预检和 coverage。若 coverage.json 仍存在 pending_review，必须先处理这些变化单元：

- 已有测试场景已经覆盖：把对应 review_unit_id 加到已有用例；
- 没有合适场景：补充测试用例。

再次 --resume，pending_review 清零后脚本自动完成 Excel、报告和 finalize。

最终运行目录根部只交付：

- test-cases.xlsx
- report.md
- coverage.json

中间文件统一保存在 .work/，不得再生成 final 后缀的第二套文件。

## 分析范围

最终净变化为唯一范围依据。脚本解析 Java 类/方法/字段、一层直接调用、Mapper/DB、HTTP/RPC/MQ、配置消费者、参数来源、返回值来源、分支事实、提交说明和固定源码证据。

调用关系最多一层，不尝试静态证明完整业务链。

## review.json

当前 review_schema_version=4。

review_units 是用例和证据绑定的最小复核单元。

完整结构关系保留在 review_facets；Agent 首轮只阅读 navigation_facets。navigation_facets 优先保留配置键、Mapper、字段、变化类、变化方法之间的直接关系和非通用业务调用。普通 Util/Common/Base/Helper 等共享调用只保留在完整 review_facets 中。

阅读顺序固定为：

1. navigation_facets
2. review_unit.change_summary
3. 必要时回查 review_facets、fact_catalog、analysis.json、context.jsonl

不得因为导航层减少而删除完整事实。

## test-cases.json

当前 schema_version=4。

每条用例固定字段：

- case_id
- title
- preconditions
- steps
- expected
- observable_evidence
- review_unit_ids

四类固定为需求验收、既有核心流程回归、关联回归、业务规则与边界验证。

Agent 不手工写 fact/evidence ID。一个已有场景可以绑定多个 review_unit_ids，避免为了结构覆盖重复造用例。

## coverage.json

当前 coverage_schema_version=2。

coverage 的 pending_review 是待复核清单，不是覆盖率门槛。每项直接给出变化摘要、高价值 facet 和完整 facet 引用。

对 pending_review 的处理只能是：

- 绑定到确实覆盖该变化点的已有用例；
- 新增必要测试用例。

不得为了数字清零添加没有业务价值的重复用例。

## 前置格式校验

delivery_preflight.py 必须在 Excel/报告生成前执行并一次性暴露纯格式错误，包括：

- 用例 JSON 结构；
- report-data 固定字段；
- Mermaid 围栏；
- Mermaid 起始语法；
- analysis/review/cases 身份一致性。

Mermaid 只允许以 flowchart TD 或 sequenceDiagram 开始，graph TD 不允许进入报告生成阶段。

## 耗时记录

.work/run-state.json 记录 analyze、review_workbench、preflight、coverage、excel、report、finalize 的实际开始、结束、耗时和状态。

agent_between_steps 累计 Agent 首次业务复核以及 coverage 待复核阶段的实际等待时间。

优化判断以这些真实数据为准，不再人工回溯估算阶段耗时。

## 质量约束

- 最终差异和完整 fact/evidence 不裁剪；
- navigation_facets 只调整阅读优先级，不改变分析范围；
- facet 不代表业务需求归属；
- coverage 不证明分支覆盖或业务正确性；
- unresolved、hard_errors、状态、金额、幂等、接口契约、配置、数据库/渠道副作用和异常恢复仍需按风险回查；
- 未实际执行的构建、单元测试、数据库验证或环境联调不得写成已验证；
- analyze.py、review_workbench.py、validate_analysis.py 等独立脚本仅作为内部实现或调试入口。
