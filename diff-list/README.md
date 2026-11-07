# diff-list

根据 Git 修订分析代码最终净变化，输出 **Markdown 变更与测试报告**、**四类测试用例 Excel** 和 **复核覆盖清单**。适用于提测评审及变更影响分析。

## 工作思路

```text
指定仓库与 Git 修订
    ↓
固定前后快照 → 计算最终净变化 → 保留提交演进信息
    ↓
Java AST / Mapper / 配置解析 → 静态事实、直接关系及源码证据
    ↓
变化点复核单元 → 按需证据查询与结构审计
    ↓
Agent 核对业务语义、历史功能影响并设计测试用例
    ↓
格式预检 → 用例引用复核 → Excel 与报告生成 → 证据校验 → 交付
```

**职责划分**：工具脚本负责可重复的 Git 取证、静态分析、证据索引、结构校验与交付；Agent 基于完整事实、实际代码和业务上下文判断业务变化、风险与测试预期。

- **最终净变化**确定本次分析范围；提交信息帮助解释变化过程和反复调整。
- **`analysis.json` / `context.jsonl`**保存事实及固定快照证据；`review.json`提供变化点与关联索引，`navigation_facets`用于安排阅读优先级。
- **`coverage.json`**列出尚未被测试用例引用的复核单元，支持逐项核实。
- **可观察预期**：测试结果对应页面、接口、日志、数据库、文件或下游行为等可判定证据。

## 工具脚本做了什么

| 阶段 | 核心脚本 | 主要职责 |
| --- | --- | --- |
| Git 范围与快照 | `repo_access.py`、`collect_git.py`、`net_effect.py` | 建立隔离仓库，固定 SHA，组合离散提交或范围差异，提取最终净变化与提交说明 |
| 源码与配置分析 | `source_index.py`、`java/JavaSourceIndex.java`、`analysis_engine.py`、`config_changes.py`、`analyze.py` | 解析 Java/Mapper/配置，建立方法和字段等静态关系，抽取变化事实、上下文与风险边界 |
| 复核索引和查询 | `review_workbench.py`、`review_query.py` | 生成 review unit/facet，按关键词、事实与证据 ID 精确查询，检查未归属事实和分析盲区 |
| 测试与报告 | `case_contract.py`、`delivery_preflight.py`、`export_test_cases.py`、`generate_report.py` | 校验测试用例及报告结构，导出 Excel，刷新脚本维护的报告章节 |
| 证据与交付校验 | `validate_analysis.py`、`validate_report.py`、`run_delivery.py` | 校验分析身份和证据快照，检查用例引用与报告，管理运行状态、失败恢复及最终交付 |
| 运行统计 | `query_metrics.py` | 统计复核查询次数、返回量与脚本/Agent 阶段数据 |

分析器提供静态证据与可能相关的直接调用关系；运行时绑定、跨系统实际行为和业务影响由 Agent 结合证据继续核实。

## 快速使用

环境：**Git、Python 3.8+、JDK 8+**。以 Windows PowerShell 为例：

```powershell
py -3 .\scripts\run_delivery.py --repo https://github.com/example/project --rev FULL_SHA
```

首次运行会输出 `RUN_DIR` 和下一步命令。Agent 完成复核并填写 `.work/test-cases.json`、`.work/report.md` 后，执行：

```powershell
py -3 .\scripts\run_delivery.py --resume "<RUN_DIR>"
```

待复核清单处理完成后，在运行目录根部获得：

- `test-cases.xlsx`：需求验收、既有核心流程回归、关联回归、业务边界四类用例；
- `report.md`：最终变更、流程、DDL/DML、配置及特殊逻辑说明；
- `coverage.json`：复核单元与测试用例的引用情况。

## 文档

- [使用指南](docs/usage-guide.md)：完整运行步骤、参数、按需查证、失败处理与统计。
- [数据与输出契约](references/analysis-contract.md)：JSON 结构、身份、证据与校验语义。
- [分析检查清单](references/analysis-checklist.md)：业务复核、风险和测试设计的检查维度。

## 回归测试

```powershell
py -3 -B -m unittest discover -s tests -v
```
