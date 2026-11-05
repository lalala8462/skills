---
name: diff-list
description: "基于指定仓库与 Git 修订生成差异范围报告和独立测试用例 Excel；默认以最终净变化和一层直接影响为范围，减少 AI 二次代码复核。"
---

# diff-list

输入仓库与 Git 修订，可多项目。仓库可为 HTTP(S) 地址或本地 Git 工作区；始终基于已提交 Git 对象在新的隔离目录分析，不纳入未提交改动。来源仓库只读。

默认目标：把 AST、最终差异、直接调用、配置/SQL/外部出口等机械分析固化到脚本；AI 只负责核实业务含义、风险和最终测试用例。不要为了“全量覆盖”主动扩大默认范围。

## 默认流程

1. 执行 `scripts/analyze.py` 固定 baseline/result 并生成 `analysis.json/context.jsonl`。合并提交明确 parent，多项目用 manifest。仓库专有配置注解/作用域通过 `--rules` 显式传入。
2. 执行 `scripts/review_workbench.py --fast10`。默认快审只消费：
   - 本次净变化事实；
   - 变化方法直接向上/向下 **1 层**关系；
   - 与这些变化直接相关的配置、Mapper/DB、HTTP/RPC/MQ、渠道、参数来源和返回事实；
   - hard_errors / scope_errors / 关键证据缺失。
3. AI 按上述材料核实业务变化和风险，直接生成 `schema_version=3` 的 `test-cases.json`。不要创建默认 `point-plan.json` 或 `test-points.json`。
4. 用 `export_test_cases.py --scope-only` 导出四类测试用例 Excel，再用 `generate_report.py --scope-only` 生成 Markdown 候选并用 `validate_report.py --scope-only --finalize` 定稿。
5. 报告第 5 章只列 Excel 文件名和四类用例数量，不重复用例正文。报告说明静态分析边界以及未执行的构建、测试、数据库和真实渠道联调。

## 默认快审边界

默认保留：
- 方法/字段/类/配置/SQL 的最终净变化；
- 变化方法的一层直接调用者与直接被调用者；
- DB/Mapper、HTTP/RPC/MQ、渠道等明确外部出口；
- 配置直接消费者；
- 参数来源变化、返回值和字段来源事实；
- 动态 SQL、自定义注解等明确风险；
- 变更源码无法索引、关键证据缺失等硬错误。

默认不再生成或扩展：
- branch true/false obligation；
- size() 的 zero/one/many obligation；
- contains() 的存在/不存在 obligation；
- switch 每个 case 的独立 obligation；
- 同名字段全仓弱关联；
- 跨方法同名字段来源比较；
- unresolved relation 批量 review obligation；
- 第二层及更远调用链；
- 为关闭全量 obligation 而追加的扫描。

branch/switch/contains 等若直接存在于变化源码中，可以作为源码事实提供给 AI，但不应自动拆分测试场景。

业务关键词（refund/pay/notify/status 等）只用于可读归组，不参与是否入选。新业务不能因为名称不匹配被排除。

## 直接测试用例格式

默认 `test-cases.json`：

```json
{
  "schema_version": 3,
  "analysis_id": "本次 analysis_id",
  "purpose_cases": {
    "acceptance": [],
    "core_regression": [],
    "related_regression": [],
    "boundary": []
  }
}
```

每条用例必须包含：

- `case_id`
- `title`
- `preconditions`
- `steps`
- `expected`
- `observable_evidence`
- `fact_ids`
- `evidence_ids`

四类唯一归属顺序仍为：**需求验收 > 既有核心流程回归 > 关联回归 > 业务规则与边界验证**。同一逻辑不要在多个 sheet 重复。

每条用例写清配置/数据前提、可直接执行的步骤和至少一个可观察、可判定结果。不能只写“处理成功”“符合预期”。

源码事实型用例引用 `fact_ids` 时，要引用对应的 `evidence_ids`；纯用户需求场景可以不引用源码事实。

## 校验

默认 `schema_version=3` 只校验最终交付真正引用的材料：

- analysis_id 属于当前分析；
- fact_id 存在；
- evidence_id 属于对应 fact；
- 只重新核对这些被最终用例引用的源码片段。

不要求关闭 analysis.json 中全部内部 obligation，不为了校验通过而补造测试或 disposition。

旧 `schema_version=2`、`test_points.py`、完整 obligation/disposition 校验只保留兼容；用户明确要求旧严格全量覆盖时才使用，默认不得进入该流程。

## 输出

Excel 固定四个工作表：

1. 需求验收
2. 既有核心流程回归
3. 关联回归
4. 业务规则与边界验证

固定五列：用例编号、用例名称、前置条件、步骤描述、预期结果。

Markdown 使用 `assets/diff-list-template.md` 固定章节。正文不展示本机源码路径、内部 fact/evidence ID、密钥或大段源码。分析目录清单仅用于后续清理。

## 处理限制

默认：
- Java/XML 索引：每项目每快照最多 10,000 文件；
- 单文件 1 MiB；
- 总源码 128 MiB；
- 调用关系最多 1 层；
- 每侧最多 100 个方法；
- 源码上下文最多 8 MiB。

变更 Java 无法索引、变更方法超过上限或关键变化证据缺失时，必须报告真实阻塞。限额外远距离调用不逐项展开，也不得描述为“已核实无影响”。

静态分析通过不代表真实业务测试、运行时绑定或联调已通过。
