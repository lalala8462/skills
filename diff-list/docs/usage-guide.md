# diff-list 使用指南

本指南负责完整命令、限制、校验和工具职责；JSON 版本、字段及报告章节以[分析契约](../references/analysis-contract.md)为唯一权威定义。

## 1. 环境与修订输入

环境：Git、Python 3.8+、JDK 8+。示例均在 `diff-list` 目录使用 PowerShell。

**Java 版本矩阵**：JDK 8+ 是运行工具链的要求；解析器以 `-source 8 -target 8` 编译，并以 `-source 8` 解析目标 Java 代码，支持 **Java 8 源码语法**。Java 9+ 新语法（包括 Java 14+ 箭头 switch）不属于已支持语法，可能产生解析错误；该说明不表示所有 JDK 版本均已验证。

| 场景 | 命令 |
| --- | --- |
| 单提交相对父提交 | `py -3 .\scripts\run_delivery.py --repo G:\Code\project --rev FULL_SHA` |
| 指定前后范围 | `py -3 .\scripts\run_delivery.py --repo G:\Code\project --base BASE_SHA --head HEAD_SHA` |
| 离散提交组合 | `py -3 .\scripts\run_delivery.py --repo G:\Code\project --rev SHA_1 --rev SHA_2` |
| 多项目 | `py -3 .\scripts\run_delivery.py --manifest projects.json` |

`projects.json` 示例：

~~~json
{
  "projects": [
    {"name":"service", "repo":"G:\\Code\\service", "revisions":["FULL_SHA"]},
    {"name":"client", "repo":"https://github.com/example/client", "range":{"base":"BASE_SHA","head":"HEAD_SHA"}}
  ]
}
~~~

`--manifest` 和单项目参数分别选择完整范围。合并提交的父提交编号可用 `{"rev":"FULL_SHA","parent":1}`；旧 `SHA:parent` 写法仍兼容，建议使用结构化输入。隔离 `--no-checkout` clone 不修改原工作区。

## 2. 规则与资源预算

**默认使用通用规则**。不要主动搜索、猜测或补传项目专用 `rules` 文件；只有用户明确提供规则文件时才传入 `--rules`，不得为尝试或补传规则对同一批 SHA 重复分析。用户明确提供的 `--rules rules.json` 可扩展 `config_annotations`、`config_scopes`、`aliases`、`limits`。例：

~~~json
{"version":1,"limits":{"depth":3,"methods":500}}
~~~

`DEFAULT_LIMITS` 与 `MAX_LIMITS` 对应以下六项，字节类单位均为 B：

| 参数 | 默认值 | 最大值 |
| --- | ---: | ---: |
| `files` | 10000 | 20000 |
| `file_bytes` | 1048576 | 4194304 |
| `total_bytes` | 134217728 | 536870912 |
| `depth` | 1 | 3 |
| `methods` | 100 | 500 |
| `context_bytes` | 8388608 | 67108864 |

**最大值**来自 `analyze.py.MAX_LIMITS`；`analysis.json.limits` 记录该次分析的**实际生效值**，不是最大值。超过预算形成 hard_errors，不会静默缩小分析结果。默认 `depth=1`，已唯一绑定的直接 Mapper SQL 仍取证；回调对象来源传播固定 4 轮。

## 3. 运行目录与阶段

默认运行目录 `%LOCALAPPDATA%\diff-list\deliveries\<run>`，或传 `--runs-root G:\tmp\diff-list\deliveries`。

~~~text
<run>/
  .work/
    analysis/
      analysis.json
      context.jsonl
    review.json
    test-cases.json
    report.md
    run-state.json
    query-metrics.jsonl
  test-cases.xlsx
  report.md
  coverage.json
~~~

首次执行：`analyze → review_workbench → report_init → waiting_agent`。脚本在终端提供 `RUN_DIR`、查询命令和 `RESUME`。此时 `.work` 中的证据与用例骨架供 Agent 编辑；根部三个交付文件仅在成功后出现。

Agent 完成复核并填写 `.work/test-cases.json`、`.work/report.md` 后：

~~~powershell
py -3 .\scripts\run_delivery.py --resume "<run>"
~~~

交付链：`preflight → coverage`，preflight **强制要求** `--receipts`；若 `pending_review > 0` 则进入 `waiting_coverage_review`；核对 `.work/coverage.json` 中的 `candidate_case_ids`、相关主事实与可观察断言，补充真实的 `unit_assertions` 或测试后再次 resume。没有待复核单元则依次 `excel → report → finalize`。成功时清理本次建立的分析 clone。

**设计约束**：脚本不猜业务用途或风险，直接相关的 `unresolved` 关系不按名称过滤；YAML 解析失败保留解析边界，不根据路径猜测是否运行配置。普通关键词搜索只用于定位材料，不用于判断结论。

## 4. 复核查询

先概览再核查分类事实：

~~~powershell
$a = "<run>\.work\analysis\analysis.json"
$r = "<run>\.work\review.json"
py -3 .\scripts\review_query.py --analysis $a --review $r --overview
py -3 .\scripts\review_query.py --analysis $a --review $r --audit
py -3 .\scripts\review_query.py --analysis $a --review $r --overview --view commits --limit 10
py -3 .\scripts\review_query.py --analysis $a --review $r --query originalTradeNo
py -3 .\scripts\review_query.py --analysis $a --review $r --query originalTradeNo --view facts --matched-only
py -3 .\scripts\review_query.py --analysis $a --review $r --unit RU-xxxx --view relations
py -3 .\scripts\review_query.py --analysis $a --review $r --fact F-xxxx
py -3 .\scripts\review_query.py --analysis $a --review $r --evidence E-xxxx --source-lines 12
~~~

| 参数 | 精确约束 |
| --- | --- |
| `--overview`、`--audit`、`--query`、`--unit`、`--fact`、`--evidence` | 六种互斥入口，必须指定一种；overview 同时展示 `audit_scope_tiers` 和可选的 `receipt_status` |
| `--view` | `index`、`facts`、`relations`、`evidence`、`db`、`conditions`、`all`；`commits` 仅可配合 `--overview` |
| `--limit` | 默认 10；有效范围 **1–100** |
| `--source-lines` | 默认 8；取证行数 **0–100** |
| `--cursor` | 从当前页 `next_cursor` 获取；绑定分析、视图、返回内容与 offset |
| `--view all` | 当前范围完整展开；**禁止 cursor** |
| `--matched-only` | 仅适用于 `--query --view facts` |
| `--context` | 默认 `analysis.json` 同目录的 `context.jsonl`，显式指定时仍要求同目录 |
| `--output` | 使用 `open('x')` 创建新的 JSON 文件；已有文件**不覆盖** |
| `--audit --view` | 选择下述 11 种审计分类；audit 概览禁止 cursor |

`AUDIT_VIEWS` 11 个分类：

`missing_seeds`、`unassigned_primary_facts`、`unindexed_facts`、`unindexed_relations`、`unresolved_relations`、`missing_evidence`、`unreferenced_evidence`、`evidence_project_mismatch`、`hard_errors`、`invalid_unit_references`、`cross_source_missing`。

分页字段为 `page.total`、`page.returned`、`page.offset`、`page.complete`、`page.next_cursor`。当 `complete=false` 时依据 `next_cursor` 逐页查询，直到完整。`--overview --view commits` 读取的是现有 Git 取证结果而不是重新执行 Git；按项目名称及提交采集顺序分页，`commit_review_summaries` 显示范围、截断数量、重复修改数量与待复核提示。缺失历史显示 `available=false`。该查询仅整理材料，不根据提交说明推断业务实现或优先级。

## 5. 用例与报告硬约束

`.work/test-cases.json` 的四类固定为 `acceptance`、`core_regression`、`related_regression`、`boundary`。每条用例具有 `case_id`、`title`、`preconditions`、`steps`、`expected`、`observable_evidence`、`review_unit_ids`、`unit_assertions`（每个 unit 显式绑定所属直接主事实 `direct_fact_ids` 与 `observable_evidence` 的 0 起始 `observation_indexes`）。`observable_evidence` 必须为非空数组；每一项**恰好**有三个非空字符串字段：`surface`（观察渠道）、`target`（观察对象）、`assertion`（可判定结果）。

Agent 负责报告的需求、DDL、DML、流程、Mermaid、特殊逻辑与既有功能影响。机器章节与精确标题统一见[分析契约](../references/analysis-contract.md)。

**Agent 回执**：首次运行自动生成 `.work/review-receipts.json`。六个 Agent 章节加 `existing_impact` 分别填写 `status` 和对应 `unit_ids/fact_ids/evidence_ids`；无相关变更的章节填 `not_applicable` 且不强造引用，其他章节填 `assessed` 并关联实际复核过的来源。`audit_unresolved_by_unit` 逐 unit 列出未解析关系，记录 `assessed`（必须关联 fact/evidence）或 `deferred`（明确未核实，只记录状态及自动列出的关系 ID，fact/evidence 为空）；概览单独统计 deferred 数量。初始 `pending` 阻止交付。脚本只检查 ID 存在、与对应 unit/关系一致，不判业务解释是否真实。查看 `--overview` 的 `receipt_status` 可了解仍未处理或延期的数量。

**报告的强制校验规则：**

- 整体流程不是“无”时，Mermaid 章节必须且只能有一个 Mermaid 代码块，起始语句须为 `flowchart TD` 或 `sequenceDiagram`；**禁止 `graph TD`**。
- 正式报告不得保留自动生成的 Agent 填写占位符；不能核实的业务结论应说明静态验证边界。脚本不根据自然语言词语判定是否已充分核实。
- 禁止包含 Windows 盘符路径、UNC 共享路径或特定 Unix 本机绝对路径；使用相对路径及 Git 固定证据身份。
- Excel 测试用例保留在四个 sheet；Markdown 禁止重复逐条列出测试点、前提、步骤及预期。
- `observable_evidence.assertion` 应能从页面、接口、日志、数据库、文件或下游行为判断；Agent 应避免“处理成功/符合预期”一类空泛表述，脚本只校验非空与结构，不用关键词判定质量。

**Agent 业务约定（脚本不做语义校验）：**
- 请求、响应、DTO 或数据库字段/接口契约变化时，Agent 应说明既有功能影响；不能未经核实直接写 `既有功能影响：无`。脚本仅检查既有功能影响字段存在且非空，不检查这一业务判断是否合理。
- 断言应包含可观察、可判定的结果，不能仅凭格式校验通过就认为用例充分。

当前 `test-cases.json schema_version=5`，`coverage.json coverage_schema_version=4`。回执校验不替代测试质量判断：一条用例可以关联多个 unit，但必须逐 unit 建立明确的主事实—断言位置引用。**即使全部关联也不证明业务覆盖**。`coverage.json` 的 `primary_assertion_links` 列出每个 unit 对应的用例 ID、主事实 ID 和可观察断言下标，供 Agent 逐项复核。手工运行 `delivery_preflight.py` 必须传入 `--receipts .work/review-receipts.json`，无有效回执不能通过预检。报告 Markdown 结构错误优先归入报告结构检查，不归为回执错误。

静态证据、实际测试执行、数据库验证和外部联调结果应区分；静态分析不足以证明的行为在业务结论中明确验证范围。

## 6. Agent 阶段与统计

~~~powershell
py -3 .\scripts\run_delivery.py --run-dir "<run>" --mark-agent-phase analysis_authoring
~~~

CLI 的 `AGENT_PHASES` 为 `source_review`、`analysis_authoring`、`rework`，其 `run-state.json.stages` 键带前缀：`agent_source_review`、`agent_analysis_authoring`、`agent_rework`。`agent_between_steps` 为这些阶段的总时长，不能重复加总。

**`run-state.json.stages` 记录各脚本/Agent 阶段耗时；`run-state.json.query_stats` 记录查询统计。** `SCRIPT_TOTAL`、`AGENT_TOTAL`、`TRACKED_TOTAL` **三项总计由 `run_delivery.py` 终端打印，并不落盘为同名字段**。`AGENT_TOTAL = agent_between_steps`，`TRACKED_TOTAL = SCRIPT_TOTAL + AGENT_TOTAL`。查询返回字节数不等于模型 Token 消耗。

## 7. 故障恢复与回归

- 首次分析失败：查看 `.work/run-state.json.failed_stage` 和终端错误，修复后使用原参数重新创建运行。
- resume 阶段失败：进入 `waiting_rework`；修复后再次执行 `--resume`。
- 证据/覆盖异常：用 `review_query.py --audit --view hard_errors` 复查固定快照与缺失事实。
- 回归：在 `diff-list` 目录执行 `py -3 -B -m unittest discover -s tests -v`。

## 8. 核心脚本职责

| 脚本 | 职责 |
| --- | --- |
| `repo_access.py`、`collect_git.py`、`net_effect.py`、`commit_review.py` | 隔离仓库、固定修订、组合净变化、提交演进取证 |
| `source_index.py`、`java/JavaSourceIndex.java`、`analysis_engine.py`、`config_changes.py`、`analyze.py` | Java AST、Mapper/配置事实、直接关系、索引预算与 hard_errors |
| `scope_validation.py`、`review_workbench.py`、`review_query.py` | 范围校验、review unit/facet、按需查询和完整性审计 |
| `case_contract.py`、`delivery_preflight.py`、`export_test_cases.py`、`generate_report.py` | 四类用例结构预检、Excel 与 Markdown；导出 CLI 单独使用时可传 `--template` |
| `validate_analysis.py`、`validate_report.py`、`run_delivery.py` | 分析身份、报告校验、阶段计时与交付生命周期 |
| `query_metrics.py` | 复核查询事件记录与聚合；阶段数据由 `run_delivery.py` 维护 |

业务复核维度见[分析检查清单](../references/analysis-checklist.md)。
