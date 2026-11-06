# diff-list

基于指定 Git 修订生成最终净变化分析、四类测试用例 Excel 和 Markdown 报告。脚本负责固定范围、源码事实、一层直接影响、机械关联分组、证据绑定和交付校验；Agent 负责业务解释、测试场景和少量报告业务正文。

## 设计原则

1. **最终差异是底账**：只以指定修订形成的最终代码结果作为分析范围。
2. **确定性工作脚本化**：Git、AST、Mapper、配置、参数/返回来源、直接关系、证据绑定和报告固定结构由脚本处理。
3. **直接影响只展开一层**：不尝试静态证明完整业务链。
4. **机械聚合不替代业务判断**：脚本只按类、直接依赖、配置键、Mapper、字段等确定性锚点生成 `review_groups`。
5. **Agent 不操作 fact/evidence ID**：测试用例只引用 `review_unit_ids`，最终源码 fact/evidence 由脚本自动展开。
6. **固定报告内容不交给 Agent**：项目/SHA、配置变化、Excel 数量和静态验证说明由脚本生成。

## 当前流程

运行环境：Windows PowerShell、Git、Python 3.8+、JDK 8+。来源仓库以只读方式使用，分析在独立 clone 中完成。

### 1. 静态分析

```powershell
py -3 .\scripts\analyze.py --repo https://github.com/example/project --rev FULL_SHA --outdir G:\tmp\analysis-run
```

输出：

- `analysis.json`：完整事实、直接关系、Git 摘要、固定快照和错误；
- `context.jsonl`：可回查源码证据。

当前分析契约：`schema_version=2`、`analyzer_version=2.1.0`。

### 2. 聚合复核材料和输入骨架

```powershell
py -3 .\scripts\review_workbench.py --analysis G:\tmp\analysis-run\analysis.json --output G:\tmp\delivery\review.json --cases-template G:\tmp\delivery\test-cases.json --report-template G:\tmp\delivery\report-data.json
```

`review.json` 使用 `review_schema_version=2`。

每个 `review_unit` 对应一个变化方法或项目级变化，包含：

- `id`
- `scope`
- `symbol`
- `related_symbols`
- `fact_groups / fact_ids`
- `relation_ids`

脚本进一步生成 `review_groups`。同组只表示源码结构或直接依赖相关，例如同一类、共享直接调用、配置键、Mapper 或字段；**不表示它们一定属于同一业务需求**。

Agent 优先按 `review_groups` 阅读，再落到必要的 `review_units`。完整 fact 仍在 `analysis.json`，源码证据仍在 `context.jsonl`。

### 3. 测试用例

`test-cases.json` 使用 `schema_version=4`。Agent 不再填写 `fact_ids/evidence_ids`，每条用例只引用实际支撑该场景的 `review_unit_ids`：

```json
{
  "schema_version": 4,
  "analysis_id": "本次 analysis_id",
  "purpose_cases": {
    "acceptance": [
      {
        "case_id": "A-001",
        "title": "验证修改后的业务规则",
        "preconditions": "准备满足触发条件的数据",
        "steps": ["发起请求", "检查业务结果"],
        "expected": ["修改后的规则生效"],
        "observable_evidence": [
          {
            "surface": "接口",
            "target": "响应字段",
            "assertion": "status=SUCCESS"
          }
        ],
        "review_unit_ids": ["RU-xxxxxxxxxxxx"]
      }
    ],
    "core_regression": [],
    "related_regression": [],
    "boundary": []
  }
}
```

四类固定为：

- `acceptance`：需求验收；
- `core_regression`：既有核心流程回归；
- `related_regression`：关联回归；
- `boundary`：业务规则与边界验证。

导出前，脚本根据 `review_unit_ids` 自动展开对应 fact 和 evidence，并重新核对固定 Git tree/blob。

### 4. Excel

```powershell
py -3 .\scripts\export_test_cases.py --input G:\tmp\delivery\test-cases.json --output G:\tmp\delivery\test-cases.xlsx --analysis G:\tmp\analysis-run\analysis.json --review G:\tmp\delivery\review.json
```

Excel 固定四个工作表和五列：用例编号、用例名称、前置条件、步骤描述、预期结果。每条用例必须有至少一个可观察、可判定结果。

### 5. 报告

`report-data.json` 只保留 Agent 必须填写的业务字段：

```json
{
  "requirements": "最终需求改动",
  "ddl": "无",
  "dml": "无",
  "flow": "业务流程说明",
  "mermaid": "Mermaid 代码块或无",
  "special_rules": "特殊规则或无",
  "existing_impact": "既有功能影响或无"
}
```

脚本自动生成：

- 第 1 章项目/SHA 范围；
- 第 2.4 节配置文件变化及标准化差异块；
- 第 4 章固定“特殊规则 / 既有功能影响”结构；
- 第 5 章 Excel 文件名和四类用例数量；
- 第 6 章本技能实际执行的静态验证说明。

生成和定稿：

```powershell
py -3 .\scripts\generate_report.py --data G:\tmp\delivery\report-data.json --analysis G:\tmp\analysis-run\analysis.json --review G:\tmp\delivery\review.json --cases G:\tmp\delivery\test-cases.json --excel G:\tmp\delivery\test-cases.xlsx --output G:\tmp\delivery\report.md

py -3 .\scripts\append_run_dirs.py G:\tmp\delivery\report.md --source G:\tmp\analysis-run\analysis.json

py -3 .\scripts\validate_report.py G:\tmp\delivery\report.md --analysis G:\tmp\analysis-run\analysis.json --review G:\tmp\delivery\review.json --cases G:\tmp\delivery\test-cases.json --finalize
```

## 处理链路

```text
Git 修订
  ↓
analysis.json + context.jsonl
  ↓
review_units
  ↓
确定性 review_groups
  ↓
Agent 业务归纳 + 测试设计
  ↓
test-cases.json（只引用 review_unit_ids）
  ↓
脚本自动展开 fact/evidence
  ↓
Excel + 自动固定章节 + Agent 业务正文
  ↓
最终静态校验
```

## 校验

最终校验包括：

- `analysis_id`
- `review_schema_version=2`
- `test-cases schema_version=4`
- review unit 与 analysis fact 的归属
- 自动展开后的 evidence 与固定 Git 快照
- Excel/报告用例数量
- 报告范围、章节和配置块结构

离线回归命令：

```powershell
py -3 -B -m unittest discover -s tests -v
```
