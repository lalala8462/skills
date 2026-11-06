# analysis.json 契约

## analysis.json

- schema_version=2
- analyzer_version=2.1.0

analysis_id 由分析器版本、规则和项目固定快照计算。

每个 project 保存 repository、analysis_clone、snapshots、change_seeds、git、facts、relations、hard_errors 和 stats。

facts 是静态事实底账，主要包括方法/字段/类变化、配置、参数来源、Mapper、字段来源、返回值、分支、集合判断、未解析关系、对象复制和构造映射等。

relations 只保存与本次变化直接相连的一层关系，状态包括 source_resolved、convention_inferred、unresolved。跨项目关系记录在 cross_project_relations。

## context.jsonl

每条 evidence 保存 id、project、side、tree、path、blob、start_line、end_line、digest、snippet。

完整 fact/evidence 不因 review 摘要、facet 或导航而删除。最终交付按 test-cases 中实际引用的 review unit 重新展开和校验证据。

## review.json

当前 review_schema_version=4。

- review_units：变化方法或项目级变化的最小复核单元；
- review_facets：按精确结构锚点形成的完整关系索引；
- navigation_facets：review_facets 的高价值阅读排序子集；
- fact_catalog：本轮复核相关 fact 预览；
- relation_catalog：本轮复核相关 relation 预览。

change_summary 只压缩直接事实。

navigation_facets 不能作为范围过滤器。facet 不表示业务需求归属，也不做传递合并。

## review_query.py

Agent 可以直接调用此脚本，不需要 MCP 服务或持久化查询缓存：

- `--overview`：全部 review unit 的分页索引和全局异常计数；
- `--unit / --query`：按变化单元或关键词获取索引；
- `--view facts/relations/evidence/db/calls/conditions`：仅显式展开所选类别，分类不代表全部业务影响；
- `--fact`：完整事实及相关 evidence ID；
- `--evidence --source-lines N`：校验固定 tree/blob 后返回脱敏源码上下文；
- `--audit`：原始分析事实相对于 review 的归属差异、unresolved、hard_errors、证据缺失及分析边界；
- `--audit --view <类别>`：按类别分页读取审计问题明细。

分页字段为 `page.total/returned/complete/next_cursor`，游标绑定固定分析身份、视图和排序内容，错误游标应拒绝，不能静默跳过任何记录。

`--audit` 不证明 Agent 已完整理解业务；支持性事实未绑定 unit 可是正常情况，但必须可见并供按需回查。

## test-cases.json

当前 schema_version=4。

每条用例只引用 review_unit_ids，不接受用例级 fact_ids/evidence_ids。导出和最终校验阶段根据 review unit 自动展开 analysis fact 和 evidence。

## report.md

Agent 直接维护业务章节：

- 需求改动；
- DDL；
- DML；
- 整体流程；
- Mermaid；
- 特殊逻辑与测试关注点/既有功能影响。

脚本维护并在 refresh 时重建：

- 分析范围；
- 配置文件变化；
- Excel 文件名和用例数量；
- 验证说明。

脚本刷新不得改写 Agent 业务章节。

## coverage.json

当前 coverage_schema_version=2。

pending_review 表示尚未被测试用例引用的 review unit，并提供 high_value_facets、candidate_case_ids、related_covered_unit_ids 和完整 facet 引用。

它是遗漏复核工具，不是业务覆盖率证明。

## run-state.json

当前 run_schema_version=2。

除脚本阶段外，单独记录：

- agent_source_review
- agent_business_reasoning
- agent_test_design
- agent_report_edit
- agent_coverage_review
- agent_rework
- agent_between_steps（包含所有上述 Agent 子阶段，不可重复相加）

preflight 等 resume 阶段失败后记录 `waiting_rework`，从失败时刻到下一次 resume 的 Agent 修复区间计入总耗时。

统计口径：`TRACKED_TOTAL = SCRIPT_TOTAL + agent_between_steps`；子阶段仅用于拆解 `agent_between_steps`，不得再加一次。
