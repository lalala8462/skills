# diff-list 使用指南

本指南提供命令、交付步骤、复核查询与故障处理。项目目标及工具职责见 [README](../README.md)，数据字段与版本定义见 [分析契约](../references/analysis-contract.md)。

## 1. 环境与输入

环境：Git、Python 3.8+、JDK 8+；示例在 Windows PowerShell 下执行，工作目录为 `diff-list`。

单提交相对父提交分析：

```powershell
py -3 .\scripts\run_delivery.py --repo https://github.com/example/project --rev FULL_SHA
```

指定首尾修订范围：

```powershell
py -3 .\scripts\run_delivery.py --repo G:\Code\project --base BASE_SHA --head HEAD_SHA
```

离散提交：

```powershell
py -3 .\scripts\run_delivery.py --repo G:\Code\project --rev SHA_1 --rev SHA_2
```

多项目：

```powershell
py -3 .\scripts\run_delivery.py --manifest projects.json
```

`projects.json` 示例：

```json
{
  "projects": [
    {"name": "service", "repo": "G:\\Code\\service", "revisions": ["FULL_SHA"]},
    {"name": "client", "repo": "https://github.com/example/client", "range": {"base": "BASE_SHA", "head": "HEAD_SHA"}}
  ]
}
```

`--manifest` 与单项目参数各自提供一种完整范围选择。仓库读取在隔离的 `--no-checkout` clone 中完成，来源工作区保持原状。

默认使用通用规则。需要扩展配置注解、配置域、别名或遍历限制时，可提供规则文件：

```json
{"version": 1, "limits": {"depth": 3, "methods": 500}}
```

```powershell
py -3 .\scripts\run_delivery.py --repo G:\Code\project --rev FULL_SHA --rules rules.json
```

规则可使用 `config_annotations`、`config_scopes`、`aliases`、`relation_review_tokens`、`limits`。默认遍历为 `depth=1`、`methods=100`，上限为 3 和 500；文件数、单文件、源码总量与证据上下文也有独立预算，详细生效值记录在 `analysis.json.limits`。扩展到更深层次适用于需要更多上下文的方法调用复核。

## 2. 运行目录与阶段

默认运行目录：`%LOCALAPPDATA%\diff-list\deliveries\<run>`；也可通过 `--runs-root G:\tmp\diff-list\deliveries` 放在其它仓库外目录。

```text
<run>/
  .work/
    analysis/
      analysis.json
      context.jsonl
    review.json
    test-cases.json
    report.md
    run-state.json
  test-cases.xlsx       # 成功交付后
  report.md             # 成功交付后
  coverage.json         # 成功交付后
```

首次运行由 `run_delivery.py` 编排：

```text
analyze → review_workbench → report_init → waiting_agent
```

脚本打印 `RUN_DIR`、复核查询命令、Agent 编辑目标与 `RESUME` 命令。此时 `.work` 下的 JSON 是完整结构化材料，`report.md` 是待填写的初稿。

## 3. Agent 复核与报告编写

首先查看全局变化概览和结构审计：

```powershell
$a = "<run>\.work\analysis\analysis.json"
$r = "<run>\.work\review.json"
py -3 .\scripts\review_query.py --analysis $a --review $r --overview
py -3 .\scripts\review_query.py --analysis $a --review $r --audit
```

按方法、字段、Mapper、配置键或 review unit 定位材料：

```powershell
py -3 .\scripts\review_query.py --analysis $a --review $r --query originalTradeNo
py -3 .\scripts\review_query.py --analysis $a --review $r --unit RU-xxxx --view facts
py -3 .\scripts\review_query.py --analysis $a --review $r --query originalTradeNo --view facts --matched-only
py -3 .\scripts\review_query.py --analysis $a --review $r --unit RU-xxxx --view relations
py -3 .\scripts\review_query.py --analysis $a --review $r --fact F-xxxx
py -3 .\scripts\review_query.py --analysis $a --review $r --evidence E-xxxx --source-lines 12
```

事实、关系、数据库、分支和证据使用 `--view facts/relations/db/conditions/evidence` 查询；`--view all` 用于需要完整展开的选定范围。查询默认每页 10 条，最大 `--limit 100`。返回 `page.complete=false` 时，使用 `--cursor <next_cursor>` 读取后续页面。通过 `--output <new-file.json>` 保存大体量查询结果。

审计可使用 `--audit --view unassigned_primary_facts` 等分类视图定位遗漏。未解析关系和来源缺失需要结合变更点实际调用及外部契约判断风险。

Agent 直接修改两个文件：

- `.work/test-cases.json`：按 `acceptance`、`core_regression`、`related_regression`、`boundary` 四类编写用例。每个用例包含 `case_id`、`title`、`preconditions`、`steps`、`expected`、`observable_evidence`、`review_unit_ids`。脚本根据 `review_unit_ids` 展开事实及证据。
- `.work/report.md`：填写需求改动、DDL、DML、整体流程、Mermaid、特殊逻辑与测试关注点；在特殊逻辑章节填写“既有功能影响”。确认没有相关内容时填写“无”。图支持 `flowchart TD` 与 `sequenceDiagram`。

业务判断依据是最终差异、提交说明及源码 before/after。预期结果指定接口响应、日志、数据库状态或其它可观察证据。对于静态分析未能验证的运行行为，报告中明确验证范围与待验证项。

开始集中编写时，可记录阶段：

```powershell
py -3 .\scripts\run_delivery.py --run-dir "<run>" --mark-agent-phase analysis_authoring
```

## 4. 预检、覆盖复核与交付

```powershell
py -3 .\scripts\run_delivery.py --resume "<run>"
```

编排顺序：

```text
preflight → coverage
  ├─ pending_review > 0 → waiting_coverage_review → Agent 核实 → resume
  └─ pending_review = 0 → excel → report → finalize
```

`waiting_coverage_review` 时，阅读 `.work/coverage.json` 中的 `pending_review`、`candidate_case_ids` 和对应变化证据。已有用例确实覆盖该复核单元时绑定它的 `review_unit_id`；需要独立业务场景时补充用例。覆盖统计衡量引用情况，实际业务覆盖由 Agent 核实。

交付前再次运行 `--audit`。最终目录根部会生成 `test-cases.xlsx`、`report.md` 和 `coverage.json`；详细的分析数据和运行状态保留在 `.work`。成功后清理本次创建的隔离 clone。

## 5. 恢复与核查

- **首次分析阶段失败**：查看 `.work/run-state.json` 的 `failed_stage` 及终端错误，修复 Git/JDK/规则或源码解析环境后，使用原参数启动新的一次运行。
- **resume 阶段失败**：状态进入 `waiting_rework`；修复 `test-cases.json`、`report.md` 或相应输入后再次 `--resume`。
- **证据校验失败**：核对分析身份、引用的 review unit、固定快照与解析异常；`--audit` 可查看 `hard_errors` 和未解析/缺失证据。
- **运行统计**：`.work/run-state.json` 的 `stages` 和 `query_stats` 记录 `SCRIPT_TOTAL`、`AGENT_TOTAL`、`TRACKED_TOTAL` 及查询次数和返回体量。查询字节数用于衡量输出大小，模型 Token 消耗需使用模型侧指标核算。

## 6. 本地回归测试

```powershell
py -3 -B -m unittest discover -s tests -v
```

数据结构和各版本的权威字段定义见 [分析契约](../references/analysis-contract.md)，业务复核维度见 [分析检查清单](../references/analysis-checklist.md)。
