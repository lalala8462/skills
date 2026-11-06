# diff-list

基于指定 Git 修订生成最终净变化分析、四类测试用例 Excel 和 Markdown 报告。脚本负责固定范围、源码事实、一层直接影响、复核导航、证据绑定和交付校验；Agent 负责业务解释、测试场景和报告业务正文。

## 设计原则

1. **最终差异是底账**：只以指定修订形成的最终代码结果作为分析范围。
2. **确定性工作脚本化**：Git、AST、Mapper、配置、参数/返回来源、直接关系、证据绑定和报告固定结构由脚本处理。
3. **直接影响只展开一层**：不尝试静态证明完整业务链。
4. **复核优化不裁剪事实**：`change_summary` 只压缩当前变化点直接事实；完整 `fact_ids/fact_catalog/context.jsonl` 始终保留。
5. **Facet 只做精确导航**：同一类、同一直接调用、同一配置键、同一 Mapper、同一字段分别形成可重叠 facet，不做传递闭包，不把弱相关变化串成大组。
6. **覆盖矩阵只做遗漏检查**：未引用 unit/facet 会提示，但不自动判失败，也不自动生成低价值用例。
7. **Agent 不操作 fact/evidence ID**：测试用例只引用 `review_unit_ids`，最终源码 fact/evidence 由脚本自动展开。
8. **固定报告内容不交给 Agent**：项目/SHA、配置变化、Excel 数量和静态验证说明由脚本生成。

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

### 2. 复核材料和输入骨架

```powershell
py -3 .\scripts\review_workbench.py --analysis G:\tmp\analysis-run\analysis.json --output G:\tmp\delivery\review.json --cases-template G:\tmp\delivery\test-cases.json --report-template G:\tmp\delivery\report-data.json
```

`review.json` 使用 `review_schema_version=3`。

每个 `review_unit` 对应一个变化方法或项目级变化，包含：

- `id / scope / symbol`
- `change_summary`：只摘要直接绑定当前变化点的事实
- `direct_fact_ids / context_fact_ids`：明确区分直接事实与一层上下文事实
- `direct_fact_groups / context_fact_groups`：对应事实类型统计
- `fact_ids / relation_ids`
- `facet_ids`

`change_summary` 只是阅读入口。需要确认完整条件、返回来源、Mapper、分支或其它上下文时，仍按 fact ID 回查 `fact_catalog / analysis.json / context.jsonl`。

### 3. review_facets

`review_facets` 按单一精确锚点生成，可重叠，一个 unit 可以属于多个 facet。当前锚点包括：

- `owner`：同一变化类；
- `direct_call`：同一直接调用目标；
- `config`：同一配置键；
- `mapper`：同一 Mapper statement；
- `field`：同一字段。

Facet 不做 A-B、B-C ⇒ A-B-C 的传递合并。它只帮助减少重复阅读，不表示同一业务需求。

### 4. 测试用例

`test-cases.json` 使用 `schema_version=4`。每条用例只引用实际支撑该场景的 `review_unit_ids`：

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

### 5. 结构覆盖检查

Agent 完成第一版用例后运行：

```powershell
py -3 .\scripts\validate_analysis.py --analysis G:\tmp\analysis-run\analysis.json --review G:\tmp\delivery\review.json --cases G:\tmp\delivery\test-cases.json --coverage-output G:\tmp\delivery\coverage.json
```

`coverage.json` 使用 `coverage_schema_version=1`，给出：

- 每个 review unit 被哪些用例、哪些用例类型引用；
- 未被引用的 unit；
- 每个 facet 内已覆盖/未覆盖的 unit；
- 完全覆盖、部分覆盖、完全未覆盖的 facet。

**这不是覆盖率门槛。** Agent 只对真正有业务价值的遗漏补用例；不能为了让数字变成 100% 而重复或制造无意义场景。结构覆盖也不证明分支覆盖、业务语义正确或预期结果正确。

### 6. Excel

```powershell
py -3 .\scripts\export_test_cases.py --input G:\tmp\delivery\test-cases.json --output G:\tmp\delivery\test-cases.xlsx --analysis G:\tmp\analysis-run\analysis.json --review G:\tmp\delivery\review.json
```

导出前脚本根据 `review_unit_ids` 自动展开 fact/evidence，并重新核对固定 Git tree/blob。

Excel 固定四个工作表和五列：用例编号、用例名称、前置条件、步骤描述、预期结果。每条用例必须包含至少一个可观察、可判定结果。

### 7. 报告

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

脚本自动生成项目/SHA、配置变化、第 4 章固定结构、Excel 数量和本技能静态验证说明。

```powershell
py -3 .\scripts\generate_report.py --data G:\tmp\delivery\report-data.json --analysis G:\tmp\analysis-run\analysis.json --review G:\tmp\delivery\review.json --cases G:\tmp\delivery\test-cases.json --excel G:\tmp\delivery\test-cases.xlsx --output G:\tmp\delivery\report.md

py -3 .\scripts\append_run_dirs.py G:\tmp\delivery\report.md --source G:\tmp\analysis-run\analysis.json

py -3 .\scripts\validate_report.py G:\tmp\delivery\report.md --analysis G:\tmp\analysis-run\analysis.json --review G:\tmp\delivery\review.json --cases G:\tmp\delivery\test-cases.json --finalize
```

## 质量与性价比

本流程优先消除重复搜索、重复读源码和机械证据绑定，但不以减少 Agent 阅读量为唯一目标：

- 摘要没有删除完整事实；
- facet 没有改变分析范围；
- 未形成 facet 的 unit 仍必须正常复核；
- 覆盖矩阵只提示遗漏，不替代业务判断；
- 高风险状态、金额、幂等、配置、DB/渠道副作用、接口契约和异常恢复仍按检查清单复核；
- 任何摘要不清晰、context fact 较多或存在 unresolved/hard error 的变化点，都应回查完整 fact/evidence。

## 校验

最终校验包括：

- `analysis_id`
- `review_schema_version=3`
- `test-cases schema_version=4`
- review unit 与 analysis fact 的归属
- 自动展开后的 evidence 与固定 Git 快照
- 结构覆盖提示
- Excel/报告用例数量
- 报告范围、章节和配置块结构

离线回归命令：

```powershell
py -3 -B -m unittest discover -s tests -v
```
