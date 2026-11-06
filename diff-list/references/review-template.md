# 复核材料

`review.json` 使用 `review_schema_version=4`。

## review_units

每个 unit 对应一个变化方法或项目级主事实，是测试用例引用源码事实的唯一入口。

关键字段：

- `id / scope / symbol`
- `change_summary`
- `direct_fact_ids / context_fact_ids`
- `direct_fact_groups / context_fact_groups`
- `fact_ids`
- `relation_ids`
- `facet_ids`
- `navigation_facet_ids`

`change_summary` 只摘要直接绑定当前变化点的事实；一层相关事实不删除，只放在 context 统计和完整 fact 中供进一步核查。

## review_facets

`review_facets` 保存完整的精确结构关系。Facet 按单一锚点建立，可重叠，不做传递合并。

当前锚点：

- `owner`
- `direct_call`
- `config`
- `mapper`
- `field`

例如 A/B 共享调用 X、B/C 共享调用 Y，会形成两个 facet：A+B 与 B+C，不会形成 A+B+C。

完整 facet 用于结构检查、coverage 和必要时回查，不代表业务需求归属。

## navigation_facets

`navigation_facets` 是 `review_facets` 的高价值子集，只作为 Agent 首轮阅读入口。

优先保留：

1. 配置键；
2. Mapper / SQL；
3. 字段；
4. 与其它变化方法直接关联的调用；
5. 同一变化类；
6. 其它非通用直接业务调用。

普通 Util / Utils / Helper / Common / Base / Support / Framework 等共享调用不作为首轮导航，但仍保留在完整 `review_facets` 中。

推荐阅读顺序：

1. `navigation_facets`
2. unit 的 `change_summary`
3. 必要时回查 `review_facets / fact_catalog / analysis.json / context.jsonl`

## 用例绑定与待复核清单

Agent 在测试用例里只填写 `review_unit_ids`。脚本自动展开 fact/evidence。

第一版用例完成后生成 `coverage_schema_version=2` 的 `coverage.json`。其中 `pending_review` 对每个尚未引用的 unit 给出：

- 直接变化摘要；
- 高价值 facet；
- `candidate_case_ids`；
- `related_covered_unit_ids`；
- 完整 facet 引用。

处理方式：

- 已有测试场景确实覆盖该变化点：把对应 `review_unit_id` 加到该用例；
- 没有现成场景：补充必要测试用例。

`pending_review` 是遗漏复核工具，不是测试覆盖率证明，也不能驱动机械补用例。
