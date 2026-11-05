# diff-list

基于指定 Git 仓库和修订生成代码差异分析、范围报告和测试用例 Excel。脚本负责固定修订、解析源码、定位一层直接影响、整理配置/SQL/外部调用事实和源码证据，AI 负责核实业务含义并编写测试用例。

## 设计原则

1. **最终差异是底账**：以指定修订形成的最终结果作为分析基础。
2. **确定性工作脚本化**：AST、字段/方法变化、直接调用、Mapper、配置消费者、参数来源等由脚本提取。
3. **直接影响一层展开**：从变化方法向上、向下各分析一层。
4. **事实与测试设计分开**：源码事实进入快审材料，AI 根据事实形成业务结论和测试场景。
5. **直接生成最终用例**：AI 生成 `test-cases.json`，随后导出 Excel 和 Markdown。
6. **交付证据可回查**：最终用例引用的 fact/evidence 会重新核对固定 Git 快照。

## 当前流程

运行环境：Windows PowerShell、Git、Python 3.8+、JDK 8+。来源仓库以只读方式使用，分析在独立 clone 中完成。

### 1. 静态分析

```powershell
py -3 .\scripts\analyze.py --repo https://github.com/example/project --rev FULL_SHA --outdir G:\tmp\analysis-run
```

支持：

- 单提交、base/head、离散提交和多项目 manifest；
- Java 类、方法、字段变化；
- 一层直接调用关系；
- Mapper/XML、配置文件与配置消费者；
- 参数来源、返回值和字段来源；
- DB、HTTP/RPC/MQ、渠道等直接出口；
- 固定 baseline/result 快照和源码证据；
- Git 提交说明、重复修改文件和文本反向修改线索。

输出：

- `analysis.json`：静态事实、关系、Git 摘要、限制和错误；
- `context.jsonl`：源码证据片段。

`analysis.json` 使用 `schema_version=2`、`analyzer_version=2.1.0`。Git 元数据保留 SHA、文件列表、提交说明和重复修改线索，完整 diff 不写入分析结果。

每项目每快照默认最多索引 10,000 个文件；调用关系最多 1 层、每侧最多 100 个方法；相同 blob 在同一次分析中复用解析结果。

### 2. 快审材料

```powershell
py -3 .\scripts\review_workbench.py --analysis G:\tmp\analysis-run\analysis.json --output G:\tmp\delivery\fast10-review.json
```

快审材料包含：

- 最终净变化事实；
- 一层直接关系；
- 与变化点相关的配置、Mapper/DB、参数来源、返回和外部调用事实；
- `hard_errors` 和分析统计。

提交说明和重复修改线索位于 `analysis.json.projects[].git.commit_review`，业务意图需要核对时读取该部分即可。

### 3. 测试用例

`test-cases.json` 使用 `schema_version=3`，固定四类：

- `acceptance`：需求验收；
- `core_regression`：既有核心流程回归；
- `related_regression`：关联回归；
- `boundary`：业务规则与边界验证。

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

每条用例包含可执行步骤和至少一个可观察结果。源码事实型用例通过 `fact_ids/evidence_ids` 绑定实际证据。

### 4. Excel 与报告

```powershell
py -3 .\scripts\export_test_cases.py --input G:\tmp\delivery\test-cases.json --output G:\tmp\delivery\test-cases.xlsx --analysis G:\tmp\analysis-run\analysis.json --review G:\tmp\delivery\fast10-review.json

py -3 .\scripts\generate_report.py --data G:\tmp\delivery\report-data.json --analysis G:\tmp\analysis-run\analysis.json --review G:\tmp\delivery\fast10-review.json --cases G:\tmp\delivery\test-cases.json --excel G:\tmp\delivery\test-cases.xlsx --output G:\tmp\delivery\report.md

py -3 .\scripts\append_run_dirs.py G:\tmp\delivery\report.md --source G:\tmp\analysis-run\analysis.json

py -3 .\scripts\validate_report.py G:\tmp\delivery\report.md --analysis G:\tmp\analysis-run\analysis.json --review G:\tmp\delivery\fast10-review.json --cases G:\tmp\delivery\test-cases.json --finalize
```

Excel 固定四个工作表和五列：用例编号、用例名称、前置条件、步骤描述、预期结果。预期结果写明接口、页面、日志、数据库、文件或下游行为中的可判定结果。

## 处理链路

```text
Git 修订
  ↓
静态索引
  ↓
最终净变化
  ↓
一层直接关系 + 配置/DB/外部出口事实
  ↓
AI 业务复核
  ↓
test-cases.json
  ↓
Excel + Markdown
```

## 校验

交付时校验：

- analysis/review/cases 的 `analysis_id`；
- 用例引用的 fact；
- evidence 与 fact 的归属；
- evidence 对应固定 Git 快照中的源码；
- 用例是否属于当前快审范围；
- Excel 与报告中的四类用例数量。

离线回归命令：

```powershell
py -3 -B -m unittest discover -s tests -v
```

验证情况在报告第 6 章记录实际执行的静态分析、构建测试、数据库验证和环境联调。
