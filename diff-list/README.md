# diff-list

基于指定 Git 修订生成最终净变化分析、四类测试用例 Excel 和 Markdown 报告。

核心边界：**脚本负责完整、确定、可验证的源码事实和索引；Agent 负责业务语义、业务影响和测试设计。** 不因为已有数据结构方便，就让脚本猜业务归属或自动生成看似完整的业务结论。

## 设计原则

1. **最终差异是底账**：指定修订形成的最终代码结果是分析范围。
2. **事实优先于摘要**：analysis.json / context.jsonl 保存完整事实与证据；任何摘要、facet、导航都不能删除底账。
3. **索引不等于结论**：review unit / facet / review_query 只帮助查找材料，不代表同一业务需求。
4. **navigation_facets 只排序，不过滤**：它可以提示先看什么，但不能定义 Agent 只看什么。
5. **脚本不自动归纳业务**：不生成“业务变化候选组”作为正式范围，不按代码关系直接生成业务测试结论。
6. **Agent 直接维护业务产物**：测试写入 test-cases.json，报告业务章节直接写入 report.md，不再经过 report-data.json 中转。
7. **coverage 只防遗漏**：pending_review 表示某个 review unit 尚未绑定测试场景，不证明业务覆盖充分。
8. **一个入口完成编排**：正常使用 scripts/run_delivery.py；其它脚本是内部能力或查证工具。
9. **最终交付唯一**：每次运行根目录只发布 test-cases.xlsx、report.md、coverage.json。

## 推荐执行方式

环境：Windows PowerShell、Git、Python 3.8+、JDK 8+。来源仓库只读，分析在独立 clone 中完成。

首次执行：

    py -3 .\scripts\run_delivery.py --repo https://github.com/example/project --rev FULL_SHA --runs-root G:\tmp\diff-list\runs

脚本创建唯一运行目录：

    runs/20261106-140500-123/

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

随后进入 waiting_agent。Agent 直接编辑：

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

    py -3 .\scripts\run_delivery.py --resume G:\tmp\diff-list\runs\20261106-140500-123

流程：

    preflight → coverage

若 coverage.json 仍有 pending_review，流程暂停为 waiting_coverage_review。Agent 对每个 unit 判断：

- 已有测试确实覆盖：把 review_unit_id 加到对应已有用例；
- 没有合适场景：新增必要测试。

再次 --resume，pending_review=0 后继续：

    excel → report(refresh) → finalize

最终只发布：

    test-cases.xlsx
    report.md
    coverage.json

## 完整事实与复核索引

analysis.json / context.jsonl 是事实底账。

review_schema_version=4 的 review.json 提供：

- review_units：变化点级复核入口；
- review_facets：完整精确结构关系；
- navigation_facets：高价值阅读排序；
- fact_catalog / relation_catalog：复核所需事实和关系预览。

review_facets 当前锚点包括 owner、direct_call、config、mapper、field。它们允许重叠，不做传递闭包，也不表示业务需求归属。

navigation_facets 只用于“先看什么”。普通 Util/Common/Base/Helper 等共享调用虽然可能不进入导航层，但仍保留在 review_facets 和完整事实中。

**不得把 navigation_facets 当成复核范围过滤器。**

## 显式证据查询

当 Agent 已发现一个值得深查的字段、方法、配置键、Mapper 或 review unit 时，使用 review_query.py 直接展开完整关联材料，而不是手工在多个 JSON 和源码之间反复定位。

按关键词：

    py -3 .\scripts\review_query.py ^
      --analysis <run>\.work\analysis\analysis.json ^
      --review <run>\.work\review.json ^
      --query originalTradeNo

按复核单元：

    py -3 .\scripts\review_query.py ^
      --analysis <run>\.work\analysis\analysis.json ^
      --review <run>\.work\review.json ^
      --unit RU-xxxxxxxxxxxx

查询结果会展开：

- 命中的 review unit / facet；
- unit 已绑定的完整 fact；
- unit 已绑定的完整 relation；
- cross-project relation；
- 对应 context evidence。

它只按显式查询和已有确定性引用展开，不做相似度聚类或业务猜测。**未命中的材料仍然存在于原始 analysis/review 中，不表示无关。**

## Agent 子阶段耗时

run_schema_version=2。

首次进入 Agent 阶段时自动从 source_review 开始计时。Agent 在工作重心变化时主动切换：

    py -3 .\scripts\run_delivery.py --run-dir <run> --mark-agent-phase business_reasoning
    py -3 .\scripts\run_delivery.py --run-dir <run> --mark-agent-phase test_design
    py -3 .\scripts\run_delivery.py --run-dir <run> --mark-agent-phase report_edit

coverage 暂停时自动记录 coverage_review。

run-state.json 会同时保留：

- agent_source_review
- agent_business_reasoning
- agent_test_design
- agent_report_edit
- agent_coverage_review
- agent_between_steps 总等待时间

这些计时用于判断下一轮到底应该优化“查源码”“业务判断”“测试设计”还是“报告编写”，而不是先假设某一步一定值得脚本化。

## Coverage 待复核清单

coverage_schema_version=2。

pending_review 每项包含 direct_change、high_value_facets、candidate_case_ids、related_covered_unit_ids 和完整 facet 引用。

它只回答“这个变化点是否已经被测试设计考虑”，不回答：

- 测试是否覆盖所有分支；
- 多个 unit 是否属于同一业务需求；
- 某条用例的业务语义是否正确；
- 是否应该为了覆盖率新增用例。

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
- unresolved、hard_errors、状态、金额、幂等、配置、DB/渠道副作用、接口契约、异常恢复仍需 Agent 按风险回查；
- 未实际执行的构建、单元测试、数据库验证或环境联调不得写成已验证。

## 回归测试

    py -3 -B -m unittest discover -s tests -v
