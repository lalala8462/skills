# analysis.json 契约

## 格式

- `schema_version=2`
- `analyzer_version=2.1.0`

顶层字段：

- `schema_version`
- `analyzer_version`
- `analysis_id`
- `rules`
- `limits`
- `status`
- `context_file`
- `projects`
- `cross_project_relations`
- `note`

`analysis_id` 由分析器版本、规则和项目固定快照计算。

## project

每个项目包含：

- `name`
- `repository`
- `analysis_clone`
- `snapshots.baseline/result`
- `change_seeds`
- `git`
- `analyzer_version`
- `facts`
- `relations`
- `hard_errors`
- `stats`

`change_seeds` 是本次最终变化确定出的分析起点方法。

### git

`git.selection` 保存选定修订、父提交和文件列表；`git.net_effect` 保存最终净变化状态；`git.commit_review` 保存提交说明、重复修改文件、文本反向修改线索和范围提示。

分析结果中的 Git 元数据不保存完整 diff 文本。

## facts

事实格式：

```json
{
  "id": "...",
  "kind": "method_change",
  "classification": "source_fact",
  "data": {},
  "evidence_ids": []
}
```

主要事实类型：

- `class_binding_change`
- `method_change`
- `field_change`
- `configuration`
- `configuration_file_change`
- `configuration_parse_boundary`
- `argument_source_change`
- `entrypoint`
- `mapper_statement`
- `field_source`
- `return_sources`
- `return_override`
- `branch`
- `switch_branch`
- `membership_filter`
- `constant_or_collection`
- `unresolved_relation`
- `flow_boundary`
- `annotation_binding_unconfigured`
- `object_copy`
- `constructor_mapping`

fact ID 由项目、kind、data 和 evidence_ids 稳定计算。

## relations

关系保存与本次变化直接相连的一层调用关系。

状态：

- `source_resolved`
- `convention_inferred`
- `unresolved`

跨项目关系使用源码契约匹配结果，记录在 `cross_project_relations`。

## context.jsonl

每条 evidence 包含：

- id
- project
- side
- tree
- path
- blob
- start_line
- end_line
- digest
- snippet

最终交付重新读取用例实际引用的 evidence，并与固定 Git tree/blob 对比。

## 限制

默认：

- 每快照最多 10,000 文件；
- 单文件 1 MiB；
- 总源码 128 MiB；
- 调用深度 1；
- 每侧最多 100 方法；
- 上下文最多 8 MiB。

变更 Java 索引失败、关键证据超过限制或当前分析范围发生硬错误时写入 `hard_errors`。


## review.json

- `review_schema_version=3`
- `review_units`：变化方法或项目级变化的最小复核单元
- `review_facets`：按单一精确锚点生成的可重叠复核导航
- `fact_catalog`：本轮复核所需 fact 的关键字段预览
- `relation_catalog`：本轮复核所需直接关系预览

`change_summary` 只摘要直接事实，完整 fact/evidence 不删除。`review_facets` 不做传递合并，只用于减少重复阅读，不表示业务需求归属。

## test-cases.json

- `schema_version=4`
- 每条用例只引用 `review_unit_ids`
- 不接受用例级 `fact_ids/evidence_ids`

导出和最终校验阶段根据 review unit 自动展开对应 analysis fact 和 evidence。

## report-data.json

只包含 Agent 必须填写的业务字段：

- `requirements`
- `ddl`
- `dml`
- `flow`
- `mermaid`
- `special_rules`
- `existing_impact`

项目/SHA、配置变化、Excel 数量和静态验证说明由脚本生成。


## coverage.json

由 `validate_analysis.py --coverage-output` 生成结构覆盖矩阵：

- unit 是否被用例引用；
- 每个 facet 的已覆盖/未覆盖 unit；
- 完全覆盖、部分覆盖、完全未覆盖 facet 数量。

该矩阵不作为强制通过门槛，也不证明分支覆盖、业务语义或预期结果正确。
