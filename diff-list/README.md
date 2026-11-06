# diff-list

基于指定 Git 修订生成最终净变化分析、四类测试用例 Excel 和 Markdown 报告。脚本负责固定范围、源码事实、一层直接影响、复核导航、证据绑定、格式预检和交付校验；Agent 负责业务解释、测试设计和业务正文。

## 设计原则

1. **最终差异是底账**：只以指定修订形成的最终代码结果作为分析范围。
2. **完整事实与阅读入口分离**：完整关系保留在 review_facets，Agent 首轮只看高价值 navigation_facets。
3. **不为覆盖率造用例**：coverage 输出待复核清单；已有场景可覆盖多个变化点时，直接把对应 review_unit_id 绑定到已有用例。
4. **格式错误前置**：测试用例 JSON、报告字段、Mermaid 等纯格式问题必须在 Excel/报告生成前一次性检查。
5. **一个入口完成编排**：正常使用只调用 scripts/run_delivery.py；其余脚本作为内部步骤或调试入口。
6. **最终交付唯一**：每次运行使用独立目录，根目录最终只发布 test-cases.xlsx、report.md、coverage.json。

## 推荐执行方式

环境：Windows PowerShell、Git、Python 3.8+、JDK 8+。来源仓库只读，分析在独立 clone 中完成。

首次执行：

    py -3 .\scripts\run_delivery.py --repo https://github.com/example/project --rev FULL_SHA --runs-root G:\tmp\diff-list\runs

脚本自动创建唯一运行目录，例如：

    runs/20261106-140500-123/

内部结构：

    .work/
      analysis/
        analysis.json
        context.jsonl
      review.json
      test-cases.json
      report-data.json
      run-state.json

首次执行完成 analyze → review_workbench 后进入 waiting_agent。Agent 只需要编辑：

- .work/test-cases.json
- .work/report-data.json

然后使用同一个入口继续：

    py -3 .\scripts\run_delivery.py --resume G:\tmp\diff-list\runs\20261106-140500-123

脚本执行：

    preflight → coverage

如果 coverage.json 中仍有 pending_review，流程会暂停为 waiting_coverage_review。Agent 对每个待复核 unit 做二选一：

- 已有测试场景实际覆盖该变化点：把该 review_unit_id 加到已有用例；
- 没有现成场景：补充新的测试用例。

修改后再次执行同一个 --resume 命令。只有 pending_review=0 后才继续：

    excel → report → finalize

成功后运行目录根部只保留唯一正式交付：

    test-cases.xlsx
    report.md
    coverage.json

中间材料继续保存在 .work/，不再生成 *-final.* 双版本文件。

## 复核导航

当前 review_schema_version=4。

每个 review_unit 仍是测试与源码证据绑定的最小单元，包含直接变化摘要、直接事实、一层上下文事实和完整 facet 归属。

### review_facets

review_facets 是完整结构关系，用于 coverage、结构检查和必要时回查。当前精确锚点包括：

- owner：同一变化类；
- direct_call：同一直接调用目标；
- config：同一配置键；
- mapper：同一 Mapper statement；
- field：同一字段。

完整 facet 不裁剪，也不做传递闭包。

### navigation_facets

navigation_facets 只是 Agent 首轮阅读入口，是 review_facets 的高价值子集。优先级为：

1. 配置键；
2. Mapper / SQL；
3. 字段；
4. 与其它变化方法直接关联的调用；
5. 同一变化类；
6. 其它直接业务调用。

Util / Utils / Helper / Common / Base / Support / Framework 等普通共享调用不作为首轮导航，但仍完整保留在 review_facets。

推荐阅读顺序：

1. navigation_facets
2. unit 的 change_summary
3. 必要时回查 review_facets / fact_catalog / analysis.json / context.jsonl

## Coverage 待复核清单

当前 coverage_schema_version=2。

coverage 不再只给“未覆盖数量”，同时输出 pending_review。每项包含：

- unit_id
- 项目、scope、symbol
- direct_change
- high_value_facets
- candidate_case_ids：共享高价值 facet 的现有候选用例
- related_covered_unit_ids：这些候选用例已绑定的相邻变化单元
- 完整 all_facet_ids
- 处置提示

它用于回答“是否需要新增用例”，不是覆盖率门槛，也不证明分支覆盖或业务正确性。

## 前置格式校验

delivery_preflight.py 在 Excel/报告生成前一次性检查：

- test-cases.json 分组、字段、数组、可观察证据和 review unit 引用；
- report-data.json 固定字段、空值、非法 Markdown 标题；
- Mermaid 代码围栏；
- Mermaid 起始语法只允许 flowchart TD 或 sequenceDiagram，graph TD 会直接在预检阶段报错；
- analysis / review / cases 身份是否属于同一次分析。

因此格式问题不会再等到最终报告阶段串行暴露。

## 真实耗时

每个阶段的开始时间、结束时间、耗时和状态记录在：

    .work/run-state.json

典型阶段：

    analyze
    review_workbench
    agent_between_steps
    preflight
    coverage
    excel
    report
    finalize

agent_between_steps 会累计首次业务复核和 coverage 待复核阶段的真实等待时间，从而区分脚本耗时与 Agent 业务判断耗时。

## report-data 输入

Agent 只填写以下业务字段：

- requirements
- ddl
- dml
- flow
- mermaid
- special_rules
- existing_impact

项目/SHA、配置变化、Excel 数量和静态验证说明仍由脚本生成。

## 测试用例契约

test-cases.json 当前 schema_version=4。四类固定为：

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

Agent 不手工填写 fact/evidence ID，脚本根据 review_unit_ids 自动展开源码事实与证据。预期结果必须至少包含一个可观察、可判定证据。

## 质量边界

- 摘要和导航不删除完整 fact/evidence；
- facet 不代表同一业务需求；
- coverage 不自动生成测试；
- unresolved、hard_errors、状态、金额、幂等、配置、数据库/渠道副作用、接口契约和异常恢复仍需按风险回查；
- 未实际执行的构建、单元测试、数据库验证或环境联调不得写成已验证；
- 手工调用 analyze.py、review_workbench.py、validate_analysis.py 等只用于调试，不再作为推荐操作流程。

## 回归测试

    py -3 -B -m unittest discover -s tests -v
