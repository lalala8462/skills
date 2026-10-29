# 固定分析与覆盖契约

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

可选 JSON 规则包含 `version=1`、`config_annotations`、`config_scopes`、`aliases`、`limits`。注解按短名识别，属于可配置约定而非框架类型绑定；不支持或歧义必须复核。通用配置注解为 Value、ConfigurationProperties；自定义 ParamKey 和企业/商户作用域在 `rules/rhf.json`，必须显式选择。不根据类名中的 Ent/Mch 自动猜测作用域。

`limits` 的正整数键：`files=10000`、`file_bytes=1048576`、`total_bytes=134217728`（每快照），以及 `depth=4`、`methods=400`、`context_bytes=8388608`（关联材料）。相同 blob 复用解析结果，但事实和证据分别绑定 baseline/result。超限、解析失败、方法引用、复杂循环及外部调用均显式记录，不当作无影响。

## 产物与关系状态

`analysis.json` 包含 schema_version、analyzer_version、analysis_id、rules、limits、projects 和 cross_project_relations。每个项目包含 repository、analysis_clone、snapshots、git、history、facts、relations、obligations、boundaries、stats。`hard_errors` 非空或缺少快照禁止完成。

- source_resolved：按源码声明确定的目标，不保证运行时分派。
- convention_inferred：注解访问器、单一源码实现或跨项目合同匹配等约定推导。
- unresolved：重载歧义、多实现、外部依赖或接收类型不明，保留候选和停止原因。

同一方法中明确构造、传入未解析调用边界的对象，其非访问器覆盖方法可作为回调候选展开；例如回调继续提交 Runnable。此类关系始终是 convention_inferred，保留传递位置，不证明实际调度、调用次数或执行顺序。未构造的参数对象及无法关联的动态回调继续保留边界。

事实使用 source_fact 或 risk_candidate 分类；源码差异与潜在业务后果分开。原有语法候选只用于提示，不升级为交易状态等业务事实。

字段/常量变化包含初始化值和可确定消费者，始终生成 field_behavior 必要覆盖项；直接字段读取与注解访问器用于定位未修改的消费者。继承重载按有效签名和实参类型匹配；不确定类型不标为 source_resolved。switch 分支保留 case/default、分组标签与可能来源，穿透控制流保留显式边界。

`context.jsonl` 每行包含 id、project、side、tree、path、blob、start_line、end_line、digest、snippet。证据是对应树中文件的完整行片段；明显凭据做固定脱敏，校验时按相同方式重算。分析材料在本地留存，不用于对外暴露源码或敏感信息。

## 用例覆盖关联

标准用例先按 [测试点契约](test-point-contract.md) 编译结构化目录，使用 schema_version=2、test_point_catalog 和 purpose_cases 四类数组；每条有 test_point_links 及以下 coverage_links。旧 focus_cases/regular_cases 继续兼容，五列内容保持。旧结构示例：

```json
{
  "case_id":"FOCUS-001",
  "coverage_key":"business.branch.empty",
  "coverage_links":[{
    "obligation_id":"复制必要覆盖项的id",
    "fact_ids":["复制该项fact_ids"],
    "evidence_ids":["复制该项evidence_ids，可补充已核实证据"],
    "condition":{"expression":"按覆盖项原样填写","value":true},
    "assertion":{"kind":"按覆盖项原样填写","errors":["提取的明确错误结果"]},
    "observable_evidence_indices":[0]
  }]
}
```

condition/assertion 按覆盖项原样关联，不能自行换成另一个分支；数组位置从0开始，指向实际 observable_evidence。已提取的错误文本必须出现在该用例预期或所引用观察断言中。一个用例可关联多个确实覆盖的项目；不要求为每个语法提示单独建用例，也不能靠挂编号证明实际步骤已经完整覆盖。

新四类输入还会按测试点契约，将可解析的源码触发条件与真实测试点谓词比较，并核对明确来源/错误断言及观察关联。仅复制 condition/assertion 元数据不能替代测试点条件。旧两组关联1.1.0分析材料时，也须在 coverage_links 填 trigger_conditions（同测试点条件数组）及可选 source_bindings，在对应 observable_evidence 填 constraint，执行同样的源码约束检查；无分析材料时保留原格式兼容。分析器与测试点编译器当前版本1.1.0，版本不匹配必须重新生成材料。

## 补查与处置

review 文件结构：

```json
{
  "analysis_id":"对应本次analysis_id",
  "evidence":[],
  "dispositions":[{
    "obligation_id":"必要覆盖项id",
    "status":"reviewed_boundary",
    "review_kind":"model_source_review",
    "evidence_ids":["相关源码证据id"],
    "conclusion":"具体核实到的源码关系和停止位置",
    "limitations":"未能静态核实的业务或运行边界"
  }]
}
```

status 只允许 not_applicable（有证据说明不适用）或 reviewed_boundary（无法静态确定的关系、循环、动态查询、取证限制或历史边界）。已确定的行为不能用 reviewed_boundary 代替测试；不适用结论仍需要具体源码证据与说明。不要批量生成空洞豁免来消除覆盖缺口。

可在 evidence 中补充同格式源码片段，必须来自本次明确项目的 baseline/result 树；校验会重新读取 Git blob 核对。此处补充的是模型源码复核，不改变原关系状态，不声称脚本已解析。需求含义仍不明确时才向用户澄清，无需为普通静态边界逐项请求审批。

## 完成校验

校验依次检查快照/版本身份、Git 文件归属与内容、事实和覆盖引用、测试点确定性重算及四类唯一归属、用例格式与去重后覆盖、例外证据、剩余必要覆盖项。旧用例格式只检查原覆盖契约，不宣称新去重通过；不带分析参数时不验证源码与快照。带参数时必须完整提供 analysis/review/cases；导出失败不生成新的 Excel。

PASS 只说明静态证据和结构化覆盖声明可追溯、一致、无未处置项；不能证明模型写出的所有文字正确、动态调用绑定正确、用例确实执行或业务需求成立。报告必须继续区分静态分析、构建测试与真实联调。
