# diff-list

基于指定 Git 修订的最终净变化生成 **Markdown 变更与测试报告**、**四类测试用例 Excel** 和 **coverage.json 复核覆盖清单**。

脚本负责固定 Git 快照、静态取证、复核索引、校验与交付；Agent 根据事实、提交演进和业务上下文判断既有功能影响、风险与测试预期。

## 快速使用

环境：Git、Python 3.8+、JDK 8+。分析器目标为 **Java 8 源码语法**，运行 JDK 与目标 Java 源码版本需要区分。

~~~powershell
py -3 .\scripts\run_delivery.py --repo G:\Code\project --rev FULL_SHA
~~~

首次执行输出 `RUN_DIR` 和查询命令。Agent 复核并填写 `.work/test-cases.json`、`.work/report.md`，之后执行：

~~~powershell
py -3 .\scripts\run_delivery.py --resume "<RUN_DIR>"
~~~

若进入 `waiting_coverage_review`，核实未引用 review unit 后补充用例或引用并再次 resume。成功交付时，运行目录根部得到 `report.md`、`test-cases.xlsx`、`coverage.json`。

**参考文档**：[使用指南（完整命令、参数、硬约束、脚本职责）](docs/usage-guide.md) · [分析契约（字段权威）](references/analysis-contract.md) · [业务分析检查清单](references/analysis-checklist.md)
