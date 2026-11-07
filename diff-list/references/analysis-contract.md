# analysis.json 契约

## analysis.json

- schema_version=2
- analyzer_version=2.3.0

analysis_id 由分析器版本、生效规则（含 limits 参数）和项目固定快照计算；显式调整 limits 会改变分析身份。

每个 project 保存 repository、analysis_clone、snapshots、change_seeds、git、facts、relations、hard_errors 和 stats。`analysis_clone` 仅用于当前运行的固定快照取证，成功交付后脚本删除隔离 clone，路径作为来源记录保留；交付后不能继续通过该路径读取源码。

facts 是静态事实底账，主要包括方法/字段/类变化、配置、参数来源、Mapper、字段来源、返回值、分支、集合判断、未解析关系、对象复制和构造映射等。`mapper_statement_change` 是 Mapper XML 中 SQL 语句新增、修改、删除的主事实；即使只有 XML 发生变化，也生成对应的项目级复核单元。

relations 记录直接变化相关的解析关系及需要人工复核的未解析关系，状态包括 source_resolved、convention_inferred、unresolved。默认遍历 depth=1，可显式增至 3；回调对象来源传播独立固定为 4 轮。规则上限和源码索引预算通过明确报错阻断不完整交付；变化 Java/XML 源码解析失败同样形成 hard_errors。跨项目关系记录在 cross_project_relations。

## context.jsonl

每条 evidence 保存 id、project、side、tree、path、blob、start_line、end_line、digest、snippet。

完整 fact/evidence 不因 review 摘要、facet 或导航而删除。最终交付按 test-cases 中实际引用的 review unit 重新展开和校验证据。

**Properties 语义**：配置值按 `java.util.Properties` 标准解析。单个反斜杠是转义前缀，未知转义也会去掉反斜杠；Windows 路径若需保留反斜杠，配置文件中采用双反斜杠，展示值与实际解析值保持一致。

**Git 路径编码边界**：changed-files、离散提交组合、Java/XML 源码索引及最终证据验证均使用零分隔 Git 路径和同一解码规则。优先 UTF-8；仅当文件系统解码可安全编码为 UTF-8、且能还原原始字节时接受。无法安全还原时，立即报包含原始路径十六进制值的错误并终止分析/校验。Git 的诊断 stderr 使用转义显示，路径身份以零分隔的原始字节和经验证的解码结果为准。

## review.json

当前 review_schema_version=4。

- review_units：变化方法或项目级变化的最小复核单元；
- review_facets：按精确结构锚点形成的完整关系索引；
- navigation_facets：review_facets 的高价值阅读排序子集；
- fact_catalog：本轮复核相关 fact 预览；完整关系通过原始 analysis.json 或 `--view relations` 查看。

change_summary 只压缩直接事实。

navigation_facets 用于阅读排序；完整复核范围来自 review_units、review_facets 与底账事实。facet 表示精确结构关联，业务需求归属由 Agent 判断。

## review_query.py

Agent 可以直接调用此脚本，不需要 MCP 服务或持久化查询缓存：

- `--overview`：全部 review unit 的分页索引和全局异常计数；
- `--unit / --query`：按变化单元或关键词获取索引，unit 直接给出 `direct_fact_ids/direct_evidence_ids`；
- `--query --view facts --matched-only`：只返回直接匹配事实，不扩大到 unit 所有事实；默认查询行为不变；
- `--view facts/relations/evidence/db/conditions`：仅显式展开所选类别，分类不代表全部业务影响；
- `--fact`：完整事实及相关 evidence ID；
- `--evidence --source-lines N`：校验固定 tree/blob 后返回脱敏源码上下文；
- `--audit`：原始分析事实相对于 review 的归属差异、unresolved、hard_errors、证据缺失及分析边界；
- `--audit --view <类别>`：按类别分页读取审计问题明细。

分页字段为 `page.total/returned/complete/next_cursor`，游标绑定固定分析身份、视图和排序内容，错误游标应拒绝，不能静默跳过任何记录。

Audit 将未归属的真实直接变化与背景事实分开；`configuration` 的 `retained_or_consumers_changed` 属于背景提示，直接触及净变化的 unresolved 优先显示，但绝不删除原始关系。

`--audit` 提供结构完整性证据；支持性背景事实可以独立保留，并支持按需回查。

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

机器草稿状态标记可以缺失；报告刷新负责重建，正式化仍拒绝重复或不配对的标记。脚本刷新时原样保留 Agent 业务章节。

## coverage.json

当前 coverage_schema_version=3。

pending_review 表示尚未被测试用例引用的 review unit，并提供 high_value_facets、candidate_case_ids、related_referenced_unit_ids 和完整 facet 引用。

`referenced_unit_count` 为测试用例引用的单元数；`referenced_facet_count` 为至少一个成员单元被用例引用的 facet 数，`all_units_referenced_facet_count` 才表示成员单元全部被引用。该统计仅反映复核单元的引用情况，由 Agent 进一步判断业务分支覆盖。

它用于提示尚待核实的引用关系；业务测试覆盖质量由 Agent 结合实际场景判断。

## run-state.json 与查询计量

当前 `run_schema_version=4`，只支持本版状态结构。编排记录三项 Agent 子阶段：

- `agent_source_review`：源码查证；
- `agent_analysis_authoring`：综合业务分析、用例设计、报告编写；
- `agent_rework`：失败后修复。

`agent_between_steps` 是上述子阶段累计总时间，不能与它们重复加总。完整口径：`TRACKED_TOTAL = SCRIPT_TOTAL + agent_between_steps`。失败脚本尝试另记于 `stage.attempts`。

`review_query.py` 只在符合 run 布局与版本的 `.work` 下追加 `query-metrics.jsonl`（仅遥测，不是新的复核材料）。每条事件包含：匿名请求哈希、查询模式、视图、成功/失败、生成响应字节数、终端输出字节数、返回项目数。不会记录原始搜索词、源码内容或完整命令参数。

下一次 `run_delivery.py` 写入状态时，从事件日志汇总为 `run-state.json.query_stats`，包含调用数、失败数、重复调用数、模式/视图分布、返回项目数及字节数。对完全相同的请求条件（包括分页 cursor）计算重复。审计前后两次调用若参数相同也计为重复，不表示一定是无效查询。

查询输出字节数不等于 Agent 实际阅读的 Token；只作为趋势指标。原始复核事实及证据结构保持不变。
