# analysis.json 契约

## 目标

`analysis.json` 只保存静态分析事实和直接关系，为 AI 快审提供稳定输入。

当前分析格式：

- `schema_version=2`
- `analyzer_version=2.0.0`

## 顶层字段

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

`analysis_id` 由分析器版本、规则和各项目固定快照计算，用于保证 analysis/review/cases 属于同一次分析。

## project

每个项目包含：

- `name`
- `repository`
- `analysis_clone`
- `snapshots.baseline/result`
- `change_seeds`：本次最终变化确定出的分析起点方法
- `git`
- `history`
- `method_timeline`
- `facts`
- `relations`
- `hard_errors`
- `stats`
- `candidates`
- `candidate_skipped`

## facts

事实统一包含：

```json
{
  "id": "...",
  "kind": "method_change",
  "classification": "source_fact",
  "data": {},
  "evidence_ids": []
}
```

主要事实类型包括：

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
- `history_boundary`

事实 ID 由项目、kind、data 和 evidence_ids 稳定计算。

## relations

关系只保存与本次变化直接相连的一层调用关系。

关系状态：

- `source_resolved`：源码声明可确定目标；
- `convention_inferred`：基于源码约定推导；
- `unresolved`：无法静态唯一确定目标。

跨项目关系只在源码契约可匹配时标记为 `convention_inferred`，不代表实际部署或服务发现绑定。

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

最终交付校验只重新读取用例实际引用的源码证据。

## 限制

默认：

- 每快照最多 10,000 文件；
- 单文件 1 MiB；
- 总源码 128 MiB；
- 调用深度 1；
- 每侧最多 100 方法；
- 上下文最多 8 MiB。

变更 Java 无法索引、关键证据缺失或范围硬限制影响本次变化时写入 `hard_errors`，阻止正式交付。
