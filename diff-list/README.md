# diff-list

基于指定 Git 修订生成最终净变化分析、四类测试用例 Excel 和 Markdown 报告。

核心边界：**脚本负责完整、确定、可验证的源码事实和索引；Agent 负责业务语义、业务影响和测试设计。** 不因为已有数据结构方便，就让脚本猜业务归属或自动生成看似完整的业务结论。

## 设计原则

1. **最终差异是底账**：指定修订形成的最终代码结果是分析范围。
2. **事实优先于摘要**：analysis.json / context.jsonl 保存完整事实与证据；任何摘要、facet、导航都不能删除底账。
3. **索引不等于结论**：review unit / facet / review_query 只帮助查找材料，不代表同一业务需求。
4. **navigation_facets 只排序，不过滤**：它可以提示先看什么，但不能定义 Agent 只看什么。
5. **脚本不自动归纳业务**：不生成“业务变化候选组”作为正式范围，不按代码关系直接生成业务测试结论。
6. **Agent 直接维护业务产物**：测试写入 test-cases.json，报告业务章节直接写入 report.md。
7. **coverage 只防遗漏**：pending_review 表示某个 review unit 尚未绑定测试场景，不证明业务覆盖充分。
8. **一个入口完成编排**：正常使用 scripts/run_delivery.py；其它脚本是内部能力或查证工具。
9. **最终交付唯一**：每次运行根目录只发布 test-cases.xlsx、report.md、coverage.json。

## 推荐执行方式

环境：Windows PowerShell、Git、Python 3.8+、JDK 8+。来源仓库只读，分析在独立 clone 中完成。**Skill 不内置任何业务项目的专用规则文件**，直接使用通用默认规则分析；`--rules` 只用于用户明确指定的自定义通用扩展，不是运行前需要寻找或补齐的依赖。

首次执行：

    py -3 .\scripts\run_delivery.py --repo https://github.com/example/project --rev FULL_SHA

默认运行目录位于系统用户数据目录，不向被分析的源仓库写入产物：

    %LOCALAPPDATA%\diff-list\deliveries\20261106-140500-123\

可选 `--runs-root G:\tmp\diff-list\deliveries` 指定其它仓库外目录。脚本只在成功交付后删除自己创建的隔离 clone；失败时保留以便重试，删除失败会在 `run-state.json` 留下错误说明。交付后原始快照源码查询不再可用，完整分析事实仍保留在 `.work/analysis/`。

内部结构：

    .work/
      analysis/
        analysis.json
        context.jsonl
      review.json
      test-cases.json
      report.md
      run-state.json

首次执行完成：

    analyze → review_workbench → report_init

随后进入 waiting_agent。报告初稿已预置“既有功能影响”的填写位置；Agent 的六个业务章节均须填写结论或明确写“无”；特殊逻辑非“无”时必须在“既有功能影响：”同行写具体结论或“无”，空标签与占位符均不合格。Agent 直接编辑：

- .work/test-cases.json
- .work/report.md

report.md 中以下章节由 Agent 维护：

- 需求改动
- DDL
- DML
- 整体流程
- Mermaid
- 特殊逻辑与测试关注点（含既有功能影响）

以下章节由脚本维护并在交付前刷新：

- 分析范围与完整 SHA
- 配置文件变化
- Excel 文件名与四类用例数量
- 静态验证说明

然后继续：

    py -3 .\scripts\run_delivery.py --resume "$env:LOCALAPPDATA\diff-list\deliveries\<run>"

流程：

    preflight → coverage

若 coverage.json 仍有 pending_review，流程暂停为 waiting_coverage_review。Agent 对每个 unit 判断：

- 已有测试确实覆盖：把 review_unit_id 加到对应已有用例；
- 没有合适场景：新增必要测试。

再次 --resume，pending_review=0 后继续：

    excel → report → finalize

最终只发布：

    test-cases.xlsx
    report.md
    coverage.json

## 参数及分析限制

- 输入：`--repo` 搭配一个或多个 `--rev SHA`，或者 `--base SHA --head SHA`；多项目使用 `--manifest projects.json`，与单项目参数互斥。
- 自定义规则：仅在明确需要时传 `--rules file.json`。规则文件使用 `version: 1`，可选 `config_annotations`、`config_scopes`、`aliases`、`relation_review_tokens`、`limits`；未知键拒绝。
- 限制：默认 `depth=1`、`methods=100`。可显式设置至 `depth=3`、`methods=500`；其余上限为 `files=20000`、`file_bytes=4194304`、`total_bytes=536870912`、`context_bytes=67108864`，超过直接报错，不做静默截断。depth 指复核方法调用遍历，回调对象来源传播使用独立固定 4 轮。
- 查询：`review_query.py --context` 可显式指定同一分析目录的 context 文件；`--output` 写新 JSON 文件，不覆盖；`--view index/all` 为索引/一次性展开视图，`--limit` 默认 10 且范围 1–100，`--source-lines` 默认 8 且范围 0–100。
- `analysis.json` 使用 `schema_version=2`。分析身份包含版本和生效规则（含限制参数），因此不同显式 limits 产生不同 analysis_id。

```json
{"version":1,"limits":{"depth":3,"methods":500}}
```

## 完整事实与复核索引

analysis.json / context.jsonl 是事实底账。

review_schema_version=4 的 review.json 提供：

- review_units：变化点级复核入口；
- review_facets：完整精确结构关系；
- navigation_facets：高价值阅读排序；
- fact_catalog：复核单元对应的事实预览；完整关系通过 analysis.json 或 `--view relations` 查证。

review_facets 当前锚点包括 owner、direct_call、config、mapper、field。它们允许重叠，不做传递闭包，也不表示业务需求归属。

navigation_facets 只用于“先看什么”。普通 Util/Common/Base/Helper 等共享调用虽然可能不进入导航层，但仍保留在 review_facets 和完整事实中。

**不得把 navigation_facets 当成复核范围过滤器。**

## 按需证据查询与全局审计

`review_query.py` 是 Agent 可主动调用的脚本接口，使用同一次分析的 `analysis.json`、`review.json`、`context.jsonl`，不会修改原始事实底账。

PowerShell 先定义复核材料路径：

    $a = "<run>\.work\analysis\analysis.json"
    $r = "<run>\.work\review.json"

三层查证：

1. **全局变化概览**：`py -3 .\scripts\review_query.py --analysis $a --review $r --overview`。返回可分页的 unit 索引、事实/关系数量、分析限制与审计异常计数。
2. **按需精确查询**：`--unit RU-xxx` 或 `--query originalTradeNo` 默认只返回结构索引；`--unit RU-xxx --view facts`、`--view db`、`--view conditions`、`--view relations`、`--view evidence` 才会展开详细数据。
3. **原始源码复核**：`--fact F-xxx` 返回完整事实及 evidence ID；`--evidence E-xxx --source-lines 12` 会校验固定 Git tree/blob 并读取脱敏源码上下文。关键业务判断必须核实 before/after，不能只看摘要。

新增精确命中：`--query originalTradeNo --view facts --matched-only` 只返回关键词直接匹配的事实及关联单元入口，不把命中 unit 的全部事实一并展开；不带 `--matched-only` 时保持完整 unit 上下文模式。`--unit RU-xxx` 的索引直接包含 `direct_fact_ids / direct_evidence_ids / context_fact_count`，可按 ID 回查固定快照。

分页默认 10 项，`--limit` 最多 100。分页查询视图的响应包含 `page.total/returned/complete/next_cursor`；`complete=false` 必须按 `--cursor <next_cursor>` 继续读取，不能将一页当作全部结果。针对分类的 `db/conditions` 是确定性已知类别索引，**不是完整业务范围**；必要时查询 `facts/relations/evidence` 全部分类。只有明确使用 `--view all` 才返回当前命中范围的完整展开，可能占用大量 Token。

Agent 在分析开始和交付前分别执行：

    py -3 .\scripts\review_query.py --analysis $a --review $r --audit

审计按“直接变化 / 触及变更方法的未解析关系 / 既有背景”分层提示。配置读取若标记为 `retained_or_consumers_changed`，不再误列为配置新增；所有原始明细仍可分页查证。

审计报告包含：change seed 是否有 unit、未归属主要变化事实、未索引支持性事实/关系、unresolved、缺失或未引用的证据、跨项目证据归属不一致、hard_errors、分析限制及跨项目来源缺失等。按类别翻页查看：

    py -3 .\scripts\review_query.py --analysis $a --review $r --audit --view unassigned_primary_facts

`--audit` 是**结构与分析边界检查**，不是业务测试覆盖率证明。部分支持性事实不绑定 unit 是正常情况，不能为了指标把它们硬塞进某个业务组。Agent 必须检查异常是否影响业务结论，不得因为 coverage.pending_review=0 就忽略审计结果。

## Agent 时间与查询统计

run_schema_version=4。只保留三个 Agent 工作阶段：

- `source_review`：源码查证；首次进入 `waiting_agent` 时自动开始；
- `analysis_authoring`：业务分析、测试设计、报告编写（不再强行划分成三个手工阶段）；
- `rework`：resume 的 preflight / coverage / Excel / report / finalize 失败后，自动进入 `waiting_rework` 并记录修复时间。

从源码查证转入综合编写时执行一次：

    py -3 .\scripts\run_delivery.py --run-dir <run> --mark-agent-phase analysis_authoring

coverage 有待复核项时自动记录为 `analysis_authoring`；修复结束后继续使用 `--resume`，自动结算 `rework`。不兼容上一版运行状态或旧阶段名称。

`agent_between_steps` 是三个子阶段的 Agent 总时间，**不得与子阶段重复相加**。输出：

- `SCRIPT_TOTAL`：全部脚本执行尝试（含失败重试）的累计耗时；
- `AGENT_TOTAL`：`agent_between_steps`；
- `TRACKED_TOTAL`：`SCRIPT_TOTAL + AGENT_TOTAL`。

`review_query.py` 仅在本次 run 处于 Agent 等待阶段时向 `.work/query-metrics.jsonl` 逐次追加轻量统计；不修改 Agent 计时状态，也不保存查询关键词、源码片段或完整参数，只保存请求哈希、模式、视图、状态和体量。下一次编排状态写入时汇总到 `run-state.json.query_stats`，交付后的 run-state 包含完整统计：

- `calls / successes / failures`：调用与失败次数；
- `distinct_requests / repeated_calls`：完全相同的模式、查询参数、视图和分页条件重复调用次数（正常的两次 audit 也会算重复）；
- `by_mode / by_view / repeated_by_mode`：查询方式、视图和重复调用分布；
- `returned_items`：工具实际返回的项目计数；
- `returned_bytes`：生成 JSON 的 UTF-8 字节数；
- `stdout_bytes`：实际写入终端的响应字节数；`--output` 只在终端输出文件位置；
- `invalid_events`：日志异常行数。

**字节数不等于 Agent Token 消耗或真实阅读量**，只能用于比较查询输出体量。独立分析目录不记录此运行级指标。


## Coverage 待复核清单

coverage_schema_version=3。

pending_review 每项包含 direct_change、high_value_facets、candidate_case_ids、related_referenced_unit_ids 和完整 facet 引用。

统计字段使用 `referenced_unit_count`、`referenced_facet_count`（至少一个关联 unit 被引用）、`all_units_referenced_facet_count` 等名称；它们仅表示测试用例引用了复核单元，不代表实际业务覆盖。

它只回答“这个变化点是否已被测试用例显式引用”，不回答：

- 测试是否覆盖所有分支；
- 多个 unit 是否属于同一业务需求；
- 某条用例的业务语义是否正确；
- 是否应该为了覆盖率新增用例。

报告的草稿标记和状态由脚本维护，Agent 可以只复制业务正文；缺失标记时，刷新/最终验证能恢复或接受正常状态，重复及不配对的机器标记仍拒绝。无需为了复用已完成 run 开发旧 schema 迁移。

## 前置格式校验

delivery_preflight.py 在 Excel 和报告刷新前检查：

- test-cases.json 固定结构；
- review unit 引用；
- report.md 标题结构和 Agent 必填章节；
- Mermaid 围栏及起始语法；
- analysis / review / cases 身份一致性。

Mermaid 只允许 flowchart TD 或 sequenceDiagram，graph TD 会直接失败。

## 测试用例契约

test-cases.json 当前 schema_version=4。四类固定：

- acceptance：需求验收；
- core_regression：既有核心流程回归；
- related_regression：关联回归；
- boundary：业务规则与边界验证。

每条用例固定包含：

- case_id
- title
- preconditions
- steps
- expected
- observable_evidence
- review_unit_ids

Agent 不手工填写 fact/evidence ID，脚本根据 review_unit_ids 自动展开源码事实与证据。预期结果必须至少包含一个可观察、可判定结果。

## 质量边界

- 摘要、facet、导航、查询结果都不能替代完整 analysis/context；
- 代码关系不等于业务关系；
- 脚本不自动声明业务变化归属；
- coverage 不证明业务正确性；
- unresolved、hard_errors、状态、数值精度、幂等、配置、数据库及外部依赖副作用、接口契约、异常恢复仍需 Agent 按风险回查；
- 未实际执行的构建、单元测试、数据库验证或环境联调不得写成已验证。

## 回归测试

    py -3 -B -m unittest discover -s tests -v
