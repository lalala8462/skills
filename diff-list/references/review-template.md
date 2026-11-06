# 快审范围模板

## 目标

围绕最终净变化和一层直接影响完成业务核实、测试用例、Excel 和 Markdown 报告。

## 生成快审材料

```powershell
py -3 .\scripts\review_workbench.py --analysis G:\tmp\analysis-run\analysis.json --output G:\tmp\delivery\review.json --cases-template G:\tmp\delivery\test-cases.json --report-template G:\tmp\delivery\report-data.json
```

`review.json` 使用 `review_schema_version=1`。

主要内容：

- `review_units`：按变化方法聚合的一层复核单元；
- `fact_catalog`：本轮所需 fact 的关键字段预览；
- `relation_catalog`：本轮所需直接关系预览；
- `project_fact_ids`：无法绑定到单一方法的项目级主事实；
- `hard_errors`：当前分析硬错误。

完整 fact 在 `analysis.json`，源码 evidence 在 `context.jsonl`；只有需要进一步核实时才按 ID 回查。

## 复核顺序

1. 按 `review_units` 明确修改点和直接影响；
2. 核实配置、DB/Mapper、渠道、通知和响应结果；
3. 必要时按 fact/evidence ID 回查完整源码；
4. 填充四类测试用例；
5. 导出 Excel；
6. 填写 `report-data.json` 正文并生成报告。

变更源码无法索引、关键证据缺失或 `hard_errors` 非空时，不标记为正式完成。
