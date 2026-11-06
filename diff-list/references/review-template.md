# 复核材料

`review.json` 使用 `review_schema_version=3`。

## review_units

每个 unit 对应一个变化方法或项目级主事实，是测试用例引用源码事实的唯一入口。

关键字段：

- `id / scope / symbol`
- `change_summary`
- `direct_fact_groups`
- `context_fact_groups`
- `fact_ids`
- `relation_ids`
- `facet_ids`

`change_summary` 只摘要直接绑定当前变化点的事实；一层相关事实不删除，只放在 context 统计和完整 fact 中供进一步核查。

## review_facets

Facet 按单一精确锚点建立，可重叠，不做传递合并。

当前锚点：

- `owner`
- `direct_call`
- `config`
- `mapper`
- `field`

例如 A/B 共享调用 X、B/C 共享调用 Y，会形成两个 facet：A+B 与 B+C，不会形成 A+B+C。

Facet 只用于复核导航，不代表业务需求归属。

## 用例绑定与覆盖

Agent 在测试用例里只填写 `review_unit_ids`。脚本自动展开 fact/evidence。

第一版用例完成后生成结构覆盖矩阵，检查未引用 unit 和部分覆盖 facet。覆盖矩阵是遗漏检查，不是测试充分性的证明，也不能驱动机械补用例。
