# 复核材料

review.json 使用 review_schema_version=4。

## review_units

每个 unit 对应一个变化方法或项目级主事实，是测试用例引用源码事实的入口。

关键字段包括 id/scope/symbol、change_summary、direct_fact_ids/context_fact_ids、fact_ids、relation_ids、facet_ids、navigation_facet_ids。

change_summary 只摘要直接事实，完整 fact/evidence 不删除。

## review_facets

review_facets 保存完整精确结构关系，当前锚点包括 owner、direct_call、config、mapper、field。

Facet 可重叠，不做传递合并，不代表业务需求归属。

## navigation_facets

navigation_facets 是高价值阅读排序，不是分析范围。

配置、Mapper、字段、变化方法之间的直接关系通常优先；普通 Util/Common/Base/Helper 共享调用可能不进入导航层，但仍保留在 review_facets 和 analysis 中。

Agent 可以优先看 navigation_facets，但必须保证所有 review unit 最终都被业务复核覆盖。不得因为某个 unit 没有 navigation facet 就跳过它。

## 按需查证及完整性审计

使用 `review_query.py --overview` 查看所有 unit 与异常计数；使用 `--unit / --query` 获取轻量索引，再使用 `--view facts/relations/evidence/db/calls/conditions` 分页展开。对于关键判断，用 `--fact` 查看事实，`--evidence` 校验 Git 固定快照并读取脱敏源码。

响应 `page.complete=false` 必须沿 `next_cursor` 继续读取；分类视图不能充当全业务影响范围。

`--audit` 检查 change seed 未归属、主要事实未归属、事实/关系未索引、unresolved、hard_errors 和证据缺失。允许正常未归属支持性事实，但必须解释其影响；`coverage.pending_review=0` 不构成审计通过，也不代表业务完整。

## 用例绑定与待复核

Agent 在测试用例里只填写 review_unit_ids，脚本自动展开 fact/evidence。

coverage_schema_version=2 的 pending_review 对尚未引用的 unit 给出 direct_change、high_value_facets、candidate_case_ids、related_covered_unit_ids 和完整 facet 引用。

处理方式只有：

- 已有测试确实覆盖：补充 review_unit_id；
- 没有现成场景：新增必要测试。

不要为了数字清零制造重复用例。
