# 固定分析与覆盖契约

每次使用当前版本重新采集、分析并生成完整材料，不迁移或接受旧版分析数据。

本文件用于内部分析。源码位置、内部证据编号及未解析明细不复制到用户报告；报告验证情况如实说明静态边界。

## 输入与修订

单项目沿用 `--repo` 配合重复的 `--rev SHA[:parent]`，或完整 `--base/--head`；多项目使用 `--manifest`，不能混用。清单示例：

```json
{"projects":[
  {"name":"service-a","repo":"https://github.com/example/service-a","revisions":["FULL_SHA"]},
  {"name":"service-b","repo":"https://github.com/example/service-b","range":{"base":"BASE","head":"HEAD"}}
]}
```

项目名唯一；revisions 亦支持 `{"rev":"FULL_SHA","parent":2}`。统一入口每项目只创建一个分析 clone，后续读取固定树。离散提交的 result 是独立索引合成树，可能与最后选中提交的树不同；冲突不回退其他范围。跨项目合同匹配仅是源码约定推导，不证明服务发现、发布版本或实际部署绑定。

## 规则与限制

可选 JSON 规则包含 `version=1`、`config_annotations`、`config_scopes`、`aliases`、`relation_review_tokens`、`limits`。注解按短名识别，属于可配置约定而非框架类型绑定；不支持或歧义必须复核。`relation_review_tokens` 用于补充仓库中无法从通用类型名称识别的关键调用接收者、调用者或方法名，按精确、有区分度的值配置，不添加宽泛词以绕过范围筛选；参数文本不参与范围匹配。通用配置注解为 Value、ConfigurationProperties；自定义 ParamKey 和企业/商户作用域在 `rules/rhf.json`，必须显式选择。不根据类名中的 Ent/Mch 自动猜测作用域。

`limits` 的正整数键：`files=10000`、`file_bytes=1048576`、`total_bytes=134217728`（每快照），以及调用关系 `depth=1`、每快照 `methods=100`、`context_bytes=8388608`。自定义规则可以调低调用深度和方法数，不可超过 1 层和每侧 100 个方法。相同 blob 在同一次分析内复用解析结果，但事实和证据分别绑定 baseline/result。受影响的变更 Java 无法索引、变更方法超过上限或关键源码证据缺失时写入 `hard_errors`。

## 产物与关系状态

`analysis.json` 包含 schema_version、analyzer_version、analysis_id、rules、limits、projects 和 cross_project_relations。每个项目包含 repository、analysis_clone、snapshots、git、history、facts、relations、obligations、兼容字段 `boundaries`（当前版本为空）和 stats。未解析关系只保留关键业务结果相关项；`hard_errors` 非空或缺少快照禁止完成。

- source_resolved：按源码声明确定的目标，不保证运行时分派。
- convention_inferred：注解访问器、单一源码实现或跨项目合同匹配等约定推导。
- unresolved：重载歧义、多实现、外部依赖或接收类型不明，保留候选和停止原因。

同一方法中明确构造、传入未解析调用边界的对象，其非访问器覆盖方法可作为回调候选展开；例如回调继续提交 Runnable。此类关系始终是 convention_inferred，保留传递位置，不证明实际调度、调用次数或执行顺序。未构造的参数对象及无法关联的动态回调继续保留边界。

新增 `src/test` 方法及新增测试类的字段和类签名不作为变更起点或业务覆盖义务；测试目录不运行补充语法候选扫描。已修改或删除的既有测试方法仍可能进入主索引，均不能替代生产源码、协议或正式需求的证据。其他路径的测试代码未必能被自动识别，需人工区分。

默认流程不再做同名字段全仓扩散，也不再生成跨方法同名字段来源绑定候选；`field_label_candidates` 保持为空。字段影响只依据最终差异、直接字段消费者和一层真实调用关系。复杂别名、跨类型映射或动态消费若未被直接关系覆盖，只作为分析边界说明，不自动扩大快审范围。

事实使用 source_fact 或 risk_candidate 分类；源码差异与潜在业务后果分开。默认关系只保留与本次变化方法直接相连的一层边。未解析调用若发生在变化方法中且属于关键副作用/数据库/外部出口，可保留为风险事实，但不再生成批量 `review_relation` obligation。普通 DTO/枚举访问器、setter、builder 和远距离关系不会扩大默认复核范围。

字段/常量变化包含初始化值和可确定消费者，始终生成 field_behavior 必要覆盖项；直接字段读取与注解访问器用于定位未修改的消费者。继承重载按有效签名和实参类型匹配；不确定类型不标为 source_resolved。switch 分支保留 case/default、分组标签与可能来源，穿透控制流保留显式边界。

`context.jsonl` 每行包含 id、project、side、tree、path、blob、start_line、end_line、digest、snippet。证据是对应树中文件的完整行片段；明显凭据做固定脱敏，校验时按相同方式重算。分析材料在本地留存，不用于对外暴露源码或敏感信息。

当前默认调用关系最多向上、向下扩展 1 层且每侧最多 100 个方法。提交时间线和配置消费者索引继续用于解释本次净变化，但不会为了补齐远距离消费者重复全仓搜索。branch、switch、membership 等可保留为源码事实，不自动生成测试义务。业务关键词只用于快审展示归组，不决定入选范围。旧分析材料不迁移，需重新采集。

## 用例覆盖关联

默认用例直接使用 `schema_version=3`，不再要求 `point-plan.json`、`test-points.json`、`test_point_catalog`、`coverage_links` 和 `test_point_links`。

每条用例固定包含 `case_id/title/preconditions/steps/expected/observable_evidence/fact_ids/evidence_ids`。四类仍为 acceptance、core_regression、related_regression、boundary。

源码事实型用例引用 fact 时，evidence 必须属于这些 fact；最终校验只重新核对被最终用例实际引用的 evidence。纯用户需求场景可不引用源码 fact。

旧 `schema_version=2` 与测试点编译器仍保留兼容，只用于历史材料或用户明确要求的严格覆盖流程，不属于默认快审。

## 快审与推进

- 默认目标仍为约 10 分钟范围交付，但不为清空全量义务而扩大范围。
- `review_workbench.py --fast10` 直接读取本次净变化、一层直接关系和相关事实，不再先生成大量候选再按 8 方法/4 关系截断。
- 无法索引变更源码、变更方法超限或关键变化证据缺失仍属于硬错误。
- 测试用例直接由已核实变化编写；用例去重、四类归属和 Excel 格式继续由现有脚本约束。
- 未执行构建、自动化测试、数据库验证或真实渠道联调时，报告必须明确说明。

## 完成校验

范围报告校验检查章节、草稿标记、Excel 文件名、四类用例数量以及用例引用的 fact/evidence 是否属于本次快审范围。默认不要求关闭未进入最终交付的内部 obligation。旧 schema_version=2 的完整覆盖校验仅作为兼容流程保留。Excel 导出失败不生成新的 Excel。

PASS 只说明静态证据和结构化覆盖声明可追溯、一致、无未处置项；不能证明模型写出的所有文字正确、动态调用绑定正确、用例确实执行或业务需求成立。报告必须继续区分静态分析、构建测试与真实联调。
