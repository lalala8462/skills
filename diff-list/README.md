# diff-list

基于指定 Git 仓库和修订生成代码差异分析、范围报告和测试用例 Excel。默认目标不是做完整静态证明，而是把 AI Agent 每次都会重复做的机械工作固化到脚本：固定最终差异、解析源码、定位直接影响、整理配置/SQL/外部调用事实，再让模型只复核少量业务结论和编写用例。

## 设计原则

1. **最终差异是底账**：以用户指定修订形成的最终结果作为功能分析基础。
2. **脚本做确定性工作**：AST、字段/方法变化、直接调用、Mapper、配置消费者、参数来源、提交演进等由脚本提取。
3. **分析一层直接关系**：从变化方法向上、向下各扩展一层，聚焦直接影响。
4. **事实与测试设计分开**：脚本提取源码事实，AI 基于已核实事实形成业务结论和测试场景。
5. **模型直接写最终用例**：依据已复核事实生成 test-cases.json。
6. **校验最终交付证据**：核对最终用例实际引用的事实和源码证据。
7. **业务关键词用于展示归组**：refund/pay/notify 等名称用于提高快审材料可读性。

## 当前默认流程

运行环境：Windows PowerShell、Git、Python 3.8+、JDK 8+。脚本不构建目标项目、不执行 SQL、不修改来源仓库。

### 1. 固定修订并生成静态分析

```powershell
py -3 .\scripts\analyze.py --repo https://github.com/example/project --rev FULL_SHA --outdir G:\tmp\analysis-run
```

支持：
- 单提交、base/head、离散提交和多项目 manifest；
- Java AST、字段/方法变化、直接调用关系；
- Mapper/XML、配置文件与配置消费者；
- 参数来源变化、返回值、字段来源、提交演进；
- 固定 baseline/result 快照和源码证据。

每项目每快照默认最多索引 10,000 个文件；调用关系默认最多 **1 层**、每侧最多 100 个方法。相同 blob 在同一次分析中复用解析结果。

### 2. 生成快审材料

```powershell
py -3 .\scripts\review_workbench.py --analysis G:\tmp\analysis-run\analysis.json --output G:\tmp\delivery\fast10-review.json
```

快审材料只包含：

- 本次净变化事实；
- 与变化方法直接相连的一层关系；
- 与这些变化使用同一源码证据的关键事实；
- hard_errors / scope_errors / 证据缺失。


### 3. AI 直接生成 test-cases.json

默认格式为 `schema_version=3`，固定四类用例：

- acceptance：需求验收
- core_regression：既有核心流程回归
- related_regression：关联回归
- boundary：业务规则与边界验证

示例：

```json
{
  "schema_version": 3,
  "analysis_id": "本次 analysis_id",
  "purpose_cases": {
    "acceptance": [
      {
        "case_id": "A-001",
        "title": "验证退款单号来源调整",
        "preconditions": "准备可触发本次退款逻辑的数据",
        "steps": ["发起退款请求", "检查渠道请求和退款记录"],
        "expected": ["渠道请求使用修改后的退款单号来源"],
        "observable_evidence": [
          {
            "surface": "下游行为",
            "target": "渠道退款请求",
            "assertion": "退款单号等于本次规则确定的字段值"
          }
        ],
        "fact_ids": ["实际引用的 fact id"],
        "evidence_ids": ["实际引用的 evidence id"]
      }
    ],
    "core_regression": [],
    "related_regression": [],
    "boundary": []
  }
}
```

`fact_ids/evidence_ids` 只关联本条用例真正依赖的源码事实。纯用户需求场景允许为空；引用源码事实时必须带对应源码证据。

### 4. 导出 Excel 和报告

```powershell
py -3 .\scripts\export_test_cases.py --input G:\tmp\delivery\test-cases.json --output G:\tmp\delivery\test-cases.xlsx --analysis G:\tmp\analysis-run\analysis.json --review G:\tmp\delivery\fast10-review.json

py -3 .\scripts\generate_report.py --data G:\tmp\delivery\report-data.json --analysis G:\tmp\analysis-run\analysis.json --review G:\tmp\delivery\fast10-review.json --cases G:\tmp\delivery\test-cases.json --excel G:\tmp\delivery\test-cases.xlsx --output G:\tmp\delivery\report.md

py -3 .\scripts\append_run_dirs.py G:\tmp\delivery\report.md --source G:\tmp\analysis-run\analysis.json

py -3 .\scripts\validate_report.py G:\tmp\delivery\report.md --analysis G:\tmp\analysis-run\analysis.json --review G:\tmp\delivery\fast10-review.json --cases G:\tmp\delivery\test-cases.json --finalize
```

Excel 保留四个工作表和五列：用例编号、用例名称、前置条件、步骤描述、预期结果。预期结果必须包含可观察、可判定的接口/页面/日志/数据库/文件/下游行为等证据。

## 静态分析范围

- Git 最终净差异和提交说明；
- Java 类、方法、字段变化；
- 变化方法的一层直接调用者和直接被调用者；
- DB/Mapper、HTTP/RPC/MQ、渠道等明确外部出口；
- 配置项及其直接消费者；
- 参数来源变化；
- 返回值和字段来源事实；
- 动态 SQL、未知自定义注解等明确风险事实；
- 变更源码无法索引、关键证据缺失等硬错误。

## 校验策略

默认 `schema_version=3`：

- 校验 analysis_id 与本次分析一致；
- 校验用例引用的 fact_id 存在；
- 校验 evidence_id 属于对应 fact；
- 重新核对最终用例引用的源码证据。

## 性能取舍

本工具优先减少 **AI 阅读和二次归并时间**。全量源码索引用于提供稳定的类型与调用基础，快审只消费最终净变化及其直接影响。

默认处理链路：

```text
Git 修订
  ↓
全量静态索引（确定性）
  ↓
最终净变化
  ↓
一层直接关系 + 配置/DB/外部出口事实
  ↓
AI 复核
  ↓
test-cases.json
  ↓
Excel + Markdown
```

## 验证

离线回归：

```powershell
py -3 -B -m unittest discover -s tests -v
```

校验通过只说明当前静态材料和结构关联一致，不代表已执行真实业务测试、数据库验证或渠道联调。
