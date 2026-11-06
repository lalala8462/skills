# diff-list

基于指定 Git 仓库和修订生成代码差异分析、范围报告和测试用例 Excel。脚本负责固定修订、解析源码、定位一层直接影响和整理源码证据，AI 只处理业务复核、测试设计和章节正文。

## 设计原则

1. **最终差异是底账**：以指定修订形成的最终结果作为分析基础。
2. **确定性工作脚本化**：AST、字段/方法变化、直接调用、Mapper、配置消费者、参数来源等由脚本提取。
3. **直接影响一层展开**：从变化方法向上、向下各分析一层。
4. **快审按变化点聚合**：不因多个事实共享同一源码 evidence 而把整个方法的所有事实平铺给模型。
5. **机器结构由脚本负责**：用例顶层结构、报告标题和章节顺序不交给 AI 手工构造。
6. **交付证据可回查**：最终用例引用的 fact/evidence 会重新核对固定 Git 快照。

## 当前流程

运行环境：Windows PowerShell、Git、Python 3.8+、JDK 8+。来源仓库以只读方式使用，分析在独立 clone 中完成。

### 1. 静态分析

```powershell
py -3 .\scripts\analyze.py --repo https://github.com/example/project --rev FULL_SHA --outdir G:\tmp\analysis-run
```

输出：

- `analysis.json`：完整静态事实、关系、Git 摘要、限制和错误；
- `context.jsonl`：源码证据片段。

`analysis.json` 使用 `schema_version=2`、`analyzer_version=2.1.0`。调用关系最多 1 层、每侧最多 100 个方法；相同 blob 在同一次分析中复用解析结果。

### 2. 聚合快审和用例骨架

```powershell
py -3 .\scripts\review_workbench.py --analysis G:\tmp\analysis-run\analysis.json --output G:\tmp\delivery\fast10-review.json --cases-template G:\tmp\delivery\test-cases.json --report-template G:\tmp\delivery\report-data.json
```

`fast10-review.json` 以 `review_units` 为主要入口。每个变化方法只携带直接关联的方法、fact ID、relation ID 和 evidence ID；`fact_catalog` 只保存关键字段预览，完整 fact 仍在 `analysis.json` 中按 ID 回查。

项目级配置文件变化等无法绑定到单一方法的主事实放在 `project_fact_ids`。需要进一步核对时，再根据 fact/evidence ID 回查 `analysis.json` 和 `context.jsonl`。

同时生成的 `test-cases.json` 已经是合法的 `schema_version=3` 四类空骨架，`report-data.json` 也已包含固定语义字段；AI 只填内容。四类用例为：

- `acceptance`：需求验收；
- `core_regression`：既有核心流程回归；
- `related_regression`：关联回归；
- `boundary`：业务规则与边界验证。

`steps`、`expected` 都必须是非空字符串数组。

### 3. Excel

```powershell
py -3 .\scripts\export_test_cases.py --input G:\tmp\delivery\test-cases.json --output G:\tmp\delivery\test-cases.xlsx --analysis G:\tmp\analysis-run\analysis.json --review G:\tmp\delivery\fast10-review.json
```

Excel 固定四个工作表和五列：用例编号、用例名称、前置条件、步骤描述、预期结果。预期结果必须包含可判定的页面、接口、日志、数据库、文件或下游行为证据。

### 4. 报告

`report-data.json` 只接受固定语义字段，不接受模板标题作为 key：

```json
{
  "sections": {
    "scope": "example FULL_SHA",
    "requirements": "本次最终生效的需求改动",
    "ddl": "无",
    "dml": "无",
    "config": "无",
    "flow": "无",
    "mermaid": "无",
    "special": "无",
    "verification": "仅完成静态分析与交付物校验。"
  }
}
```

正文中不要自行加入 Markdown 标题。生成器负责映射到模板：

```powershell
py -3 .\scripts\generate_report.py --data G:\tmp\delivery\report-data.json --analysis G:\tmp\analysis-run\analysis.json --review G:\tmp\delivery\fast10-review.json --cases G:\tmp\delivery\test-cases.json --excel G:\tmp\delivery\test-cases.xlsx --output G:\tmp\delivery\report.md

py -3 .\scripts\append_run_dirs.py G:\tmp\delivery\report.md --source G:\tmp\analysis-run\analysis.json

py -3 .\scripts\validate_report.py G:\tmp\delivery\report.md --analysis G:\tmp\analysis-run\analysis.json --review G:\tmp\delivery\fast10-review.json --cases G:\tmp\delivery\test-cases.json --finalize
```

`append_run_dirs.py --source` 读取当前 `schema_version=2` 的 `analysis.json`，直接取得本轮隔离 clone。

## 执行约束

较大的 `test-cases.json` 和 `report-data.json` 直接写文件，不把完整 JSON 放进命令行参数。这样可避免 Windows 命令行长度限制，也不依赖 JavaScript 环境提供 `TextEncoder`、`btoa` 等能力。

## 处理链路

```text
Git 修订
  ↓
静态索引与完整事实
  ↓
按变化方法聚合的一层快审
  ↓
AI 业务复核 + 填充测试用例骨架
  ↓
Excel
  ↓
AI 填写固定语义报告字段
  ↓
脚本生成 Markdown + 最终校验
```

## 校验

交付时校验 analysis/review/cases 的 `analysis_id`、用例引用 fact/evidence 的归属、固定 Git 快照源码、聚合快审范围以及 Excel/报告中的四类用例数量。

离线回归命令：

```powershell
py -3 -B -m unittest discover -s tests -v
```

报告第 6 章只记录实际执行过的静态分析、构建测试、数据库验证和环境联调。
