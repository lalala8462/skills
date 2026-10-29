# 结构化测试点、四类归属与用例契约

标准用例流程先编译测试点，再编写用例。不要先生成自由文本再让模型语义去重。`analysis.json` 的原始覆盖义务继续保留；结构化测试点不能替代源码证据与覆盖校验。

## 四类目的

固定顺序为 `acceptance`（需求验收）> `core_regression`（既有核心流程回归）> `related_regression`（关联回归）> `boundary`（业务规则与边界验证）。顺序只决定唯一存放位置，不代表风险等级或执行顺序。每类可为空，不凑数量。

- 需求验收：直接验证已确认的本次业务目标、规则或兼容承诺，包含决定需求是否成立的正向、反向及边界条件。必须关联用户输入、协议或已确认约束；代码和提交说明不能单独充当需求。
- 既有核心流程回归：已登记为核心的既有规则，且本次存在有证据的影响路径。不能铺开无关的整套资金流程回归。
- 关联回归：共享方法、配置、数据或上下游契约的既有消费者受到本次影响，有对应事实与证据。
- 业务规则与边界验证：有提取覆盖义务的剩余规则、状态、数量、空值或数据关系场景。

需求来源、规则语义、核心属性及影响说明仍需人工/模型核实；脚本检查来源类别和引用，不声称理解业务或证明运行时影响。若没有正式需求材料，不得编造需求验收：如实记录静态分析边界；结论依赖缺失需求时在对话中澄清。

## 测试点身份与数据

身份由项目、规则结构（kind/subject/operation）、入口、规范化条件、观察对象（surface/target）和断言类型确定。使用 SHA-256 摘要生成 `TP-` 编号；目的、规则注册名称、实例编号、标题和源码行号不参与身份。预期值不参与身份，相同身份预期不同直接报冲突，不生成两条绕过。分析及编译器版本分别绑定；不承诺跨规则版本编号一致。

普通订单号 A 换成 B 只改变 data_groups。请求值与记录值相同/不同、同企业/跨企业、不同接口、不同观察对象分别保留。相同入口、前提及操作下的不同断言可以组织成一条用例，但每个测试点只有一个承载位置；首期不允许同一条用例跨目的。

条件是合取数组，脚本排序、去掉完全重复项；不做逻辑定理证明或自然语言相似度判断。字段标识和字符串值区分大小写，保留布尔、数字、字符串类型差别。条件支持：

| operator | 字段 | 含义 |
| --- | --- | --- |
| missing/is_null/is_empty/nonempty/exists | subject | 缺省、null、空值、非空、存在，默认互不等价 |
| eq/ne/gt/ge/lt/le | subject；value 或 other 二选一 | 与具体值或另一个角色比较；eq/ne 的角色顺序可规范化 |
| in/not_in | subject、value 数组 | 明确状态或取值集合，集合排序去重 |
| sequence | subject、value 数组 | 明确事件顺序，不排序 |

零笔/一笔可以写 records.count eq 0/1，多笔写 records.count gt 1；配置使用真实键和实际值，不把所有非零值假定为开启。不要将零笔、一笔、多笔或不同允许状态随意合并。

仅允许显式、有来源、限定规则与入口的等价声明。equivalences 每项有 id、rule_id、entry、alternatives（至少两组条件）、canonical（其中一组）、fact_ids、evidence_ids、reason。不支持链式/循环等价；没有依据时保守保留。未声明替代关系的不同 subject/operation 名称不会自动认作相同；优先复用已有规则，模型不得仅换名字规避去重。

## 测试点计划

`point-plan.json` 是经来源核实的结构输入。规则优先取自脚本已有事实与覆盖义务，subject 使用已核实的字段、配置键或符号，operation 使用明确入口/操作；缺少业务含义的原始语法提示不能强行升级为验收行为。

最小完整示例（编号替换为当前 analysis 中真实值）：

```json
{
  "version": 1,
  "analysis_id": "本次analysis_id",
  "requirements": [{
    "id": "REQ-1",
    "source": {"kind": "user_input", "reference": "本次用户确认的规则", "statement": "选单时使用原记录订单号"},
    "rule_ids": ["select-source"]
  }],
  "rules": [{
    "id": "select-source", "project": "sample", "kind": "source",
    "subject": "selected.orderNo", "operation": "refund.select", "core": false,
    "fact_ids": ["实际事实编号"], "evidence_ids": ["该事实中的实际证据编号"]
  }],
  "impacts": [],
  "equivalences": [],
  "candidates": [{
    "id": "candidate-1", "rule_id": "select-source",
    "entry": {"kind": "http", "target": "POST /refund"},
    "conditions": [{"subject": "request.orderNo", "operator": "ne", "other": "record.orderNo"}],
    "observation": {"surface": "数据库", "target": "退款选中的交易订单号"},
    "expected": {"kind": "source_is", "value": "record.orderNo"},
    "purposes": [{"kind": "acceptance", "requirement_id": "REQ-1"}],
    "obligation_ids": ["实际覆盖义务编号"],
    "source_bindings": {"req.getOrderNo()": "request.orderNo", "original.getOrderNo()": "record.orderNo"},
    "data_groups": [{"request.orderNo": "A", "record.orderNo": "B"}]
  }]
}
```

规则 kind 支持 behavior/source/selection/configuration/contract/state/amount/idempotency/boundary；entry.kind 支持 http/method/event/job/rpc/ui。断言 kind 支持 equals/not_equals/source_is/in/not_in/exists/absent/unchanged/count/state，均必须给明确 value。

requirements 中的每一条 rule_ids 都必须有需求验收候选。requirements 允许为空，但不能删除已有明确需求来绕过覆盖。

impacts 中每项含 id、project、rule_ids、fact_ids、evidence_ids、description。回归目的写 `{"kind":"related_regression","impact_id":"IMPACT-1"}`；核心回归使用 core_regression，并要求规则 core=true。boundary 写 `{"kind":"boundary"}` 且必须有 obligation_ids。一个候选可以有多个目的；合并时保存全部来源，脚本选择最高目的。非需求规则必须有源码事实，影响/等价声明必须同时有事实及所属证据；严格校验拒绝跨项目或不存在的引用。

data_groups 是角色到实例值的对象数组。角色要与条件及真实字段一致，不用 A/B 等实例编号作为规则身份。脚本核对已提供角色是否符合可计算条件；没有提供的角色仍需由前提/步骤落实，不能声称已执行或数据准备完整。

源码覆盖条件直接与候选的 conditions/expected 对应，不能仅复制原始覆盖链接。默认使用源码表达式作为角色，`.size()` 规范为 `.count`；若业务角色名不同，在候选的 source_bindings 中逐项登记“源码角色 → 测试角色”。键必须出现在关联义务的可解析条件或来源断言中，值不得重复；映射不是类型或业务语义证明，须核对实际源码。上例 getter 名称仅示意，以当前事实的 before/after 为准。

| 源码维度 | 必须对应的测试约束 |
| --- | --- |
| cardinality | 零条 eq 0、一条 eq 1、多条 gt 1；允许可证明更严格的数值范围 |
| branch | 可解析的比较/null 条件及真假分支；复杂表达式用 `java:规范化表达式` eq true/false；明确错误结果用 equals 断言 |
| membership | 可确定成员集合时使用实际成员角色的 in/not_in；未确定集合时保留 `java:集合.contains(成员)` 布尔条件 |
| argument_source | before/after 值不一致、请求缺省或记录字段 null/空；source_is 断言对应 after 来源 |
| switch_case | selector 等于具体 case；default 使用不在显式 case 集合中的条件 |

源码条件与原始、等价后的条件都要一致；等价声明不能覆盖明确数量或状态约束。观察关联必须指向该测试点实际引用的观察对象。配置启停值和其余只要求源码复核的维度不自动推导业务断言；自然语言步骤仍需核实。分析器/编译器1.1.0与旧版本材料不混用。

编译命令：

```powershell
py -3 .\scripts\test_points.py --input G:\tmp\point-plan.json --analysis G:\tmp\analysis-run\analysis.json --output G:\tmp\test-points.json
```

输出拒绝覆盖已有文件。目录内保存规范化 plan、points、candidate_map、analysis_id 和 compiler_version。每个合并点保留全部候选、条件变体、数据组及 obligation_ids。条件、预期冲突或缺少需求覆盖时编译失败，不输出完成目录。

## 用例输入与覆盖校验

新输入使用 schema_version=2，把完整编译目录放入 test_point_catalog。purpose_cases 必须包含四个数组，键为 acceptance/core_regression/related_regression/boundary。每条仍保留 case_id/title/preconditions/steps/expected/observable_evidence 和原有 coverage_links；不再用模型自由填写的 coverage_key 决定身份。

新增关联示例：

```json
{
  "schema_version": 2,
  "test_point_catalog": {"说明": "此处放完整test-points.json对象，不是路径或摘要"},
  "purpose_cases": {
    "acceptance": [{
      "case_id": "AC-001",
      "title": "请求与原记录订单号不一致时按原记录选单",
      "preconditions": "准备归属和状态符合规则的原始记录及实际交易",
      "steps": ["逐组使用测试数据申请退款", "检查选中交易"],
      "expected": ["选中交易的订单号来自原记录"],
      "observable_evidence": [{
        "surface": "数据库", "target": "退款选中的交易订单号",
        "assertion": "选中交易的订单号等于原记录订单号",
        "constraint": {"kind": "source_is", "value": "record.orderNo"}
      }],
      "test_point_links": [{
        "point_id": "编译出的TP编号",
        "conditions": [{"subject": "record.orderNo", "operator": "ne", "other": "request.orderNo"}],
        "expected": {"kind": "source_is", "value": "record.orderNo"},
        "data_group_ids": ["该点全部DATA编号"],
        "observable_evidence_indices": [0]
      }],
      "coverage_links": []
    }],
    "core_regression": [], "related_regression": [], "boundary": []
  }
}
```

示例中的 conditions 以实际编译结果为准，不手动改变字段顺序后的规范形式。coverage_links 不能照抄空数组：必须保留该点全部原始 obligation_ids 的完整关联，格式见 analysis-contract.md；纯需求点确无源码覆盖义务时才可为空。

校验重新编译目录，并在严格模式核对当前 analysis；拒绝目录篡改、重复承载、错误分类、遗漏测试点/数据组/原始覆盖关联，以及条件、观察对象或结构断言不一致。原始源码义务仍须全部通过用例或有依据的补查处置闭环。新测试点存在但没有任何用例承载时不能通过。

导出器把固定条件、全部合并数据组补入“前置条件”，把结构断言补入“预期结果”，避免只有内部挂号、可读用例却没有实际数据和断言。自然语言步骤和额外预期仍需核实，结构校验不证明其语义正确或测试已执行。

## 输出与兼容

新标准 Excel 按固定顺序生成四个工作表：需求验收、既有核心流程回归、关联回归、业务规则与边界验证。五列不变；不增加内部编号/证据列。所有类为空时报告写“无”，不导出 Excel。

Markdown 第5章只列文件及四类数量、总数，不复制用例：

```text
Excel：test-cases.xlsx；需求验收用例数量：2；既有核心流程回归用例数量：1；关联回归用例数量：1；业务规则与边界验证用例数量：3；总用例数量：7
```

旧 cases 或 focus_cases/regular_cases 输入及两个工作表继续兼容，其通过不表示完成新测试点去重与四类归属校验。旧两组关联1.1.0分析材料时，coverage_links 须补 trigger_conditions（同 conditions 数组）及可选 source_bindings，观察对象补 constraint，执行相同源码条件/断言检查；导出到原五列。没有分析材料时保留原格式检查。新输入禁止混用旧数组。编译与导出均只读分析材料，不重新克隆、修改业务项目、提交或推送。
