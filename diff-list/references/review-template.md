# 聚合复核材料

`review.json` 使用 `review_schema_version=2`。

## review_units

每个 unit 对应一个变化方法或项目级主事实，是测试用例引用源码事实的唯一入口。

关键字段：

- `id`
- `scope`
- `symbol`
- `related_symbols`
- `fact_groups`
- `fact_ids`
- `relation_ids`

## review_groups

脚本按以下确定性锚点做连通聚合：

- 同一变化类；
- 共享直接调用目标；
- 共享接收类型 + 方法；
- 共享配置键；
- 共享 Mapper statement；
- 共享字段。

分组只用于减少重复阅读，不代表业务需求归属。Agent 负责判断最终业务分组。

## 用例绑定

Agent 在测试用例里只填写 `review_unit_ids`。导出和最终校验阶段由脚本自动展开 unit 对应的 fact/evidence。
