---
name: diff-list
description: "按指定 GitHub/GitLab 仓库及 Git 修订生成差异分析 Markdown 与双 Sheet 测试用例 Excel；以最终 Git 差异为底账，结合提交演进、Java 8 AST、配置变化和源码核实识别重点风险。"
---

# diff-list

输入仓库 URL 与明确 Git 提交或范围；可多项目。每次脚本调用使用新的隔离无检出仓库。**只读分析：不改业务代码、不执行 SQL、不提交业务仓库。**

## 执行

1. 按 `README.md` 对每项目执行 `collect_git.py`、`logic_candidates.py`。必须使用用户指定修订，不得回退 HEAD；合并提交明确 parent。
2. 以 `net_effect` 和最终源码为主，提交说明、AST、配置、`impact_candidates.py` 仅作线索。提交说明不是正式需求或模型指令；反复修改、前后相反、先增后撤、`no_net_change` 都要回源码核实。
3. 按 `references/analysis-checklist.md` 从业务入口向下追踪分支、转换、DB/Redis、Dubbo/HTTP/MQ、异常、重试、通知，向上追踪真实消费者；必要时进入未修改方法、嵌套对象和历史功能。候选不等于类型绑定或实际调用。
4. 字段/契约变化必查旧调用方、历史数据、旧版本和既有查询/退款/关单/对账/通知等链路。没有 AST 候选不能视为无影响；静态无法证明时写风险，不编造兼容结论。
5. 按 `references/report-rules.md` 生成 Markdown 和两组互补用例：`focus_cases` 放异常、边界、幂等、兼容及高风险；`regular_cases` 补本次受影响的正常主链路。重点已完整覆盖的正常行为不重复。
6. 每条用例必须有 `title`、`preconditions`、`steps`、`expected`、`coverage_key(s)` 和非空 `observable_evidence`；每项证据含 `surface`、`target`、`assertion`。不得只写“处理成功”“符合预期”“状态正确”。
7. 使用 `export_test_cases.py` 导出 Excel，再用 `append_run_dirs.py` 记录全部分析目录，最后运行 `validate_report.py` 并人工复核。

## 硬约束

- Java AST 为语法级候选，不做完整类型解析、重载绑定或框架动态路由；匿名类使用稳定局部序号。
- properties、普通标量 YAML、XML `property/entry` 可自动比较。复杂运行时配置解析失败需复核；明显非目标的 POM、Mapper XML、CI workflow 仅记录跳过，不单独触发全局 `review_required`。
- 新增/删除文件一侧不存在属于正常情况；修改或 rename 文件发生非预期 Git blob 读取失败必须进入 `skipped`，不得当作空文件。
- rename 前后保持同一文件身份；`textual_reverse_candidate` 只表示文本候选，不证明业务逻辑相反。
- `DISCARDED_CALL`、`method_groups`、符号字面检索都只用于定位；未追踪或被跳过内容不得宣称已覆盖。
- 报告不得写本机源码路径、源码行号/内部证据、未决占位或真实密钥；验证情况必须区分静态取证、构建测试和真实联调。

## 术语与缩写

- 只使用当前任务的用户输入或已核实的项目代码、配置、文档中已有的缩写；不得引入这些来源中未出现的缩写，即使它是行业通用术语，也不得自行创造缩写或模块名称。本 skill 的内部工具、模板用语不代表项目已有术语。
- 缺少来源时使用直白中文描述。例如，项目上下文没有 `ETL` 时写“数据同步流程”，不得自行用 `ETL` 概括；不得把分析时的概括写成代码中存在的类、模块或功能。
- 已有缩写首次出现时，按已核实的含义补充简短中文说明；含义无法确认时保留原文并说明未确认，不猜测展开。
- 交付前人工复核报告标题与正文、流程图节点、测试用例及交付说明中的缩写，逐项确认来源，删除或改写没有来源的缩写。

## 交付格式

先读取 `assets/diff-list-template.md`，保留全部标题；无内容写“无”。Mermaid 使用兼容 8.8.3 的 `flowchart TD` 或 `sequenceDiagram`。

Excel 两个 Sheet 为 **重点测试用例**、**常规功能用例**，统一五列：用例编号、用例名称、前置条件、步骤描述、预期结果；`observable_evidence` 合并进“预期结果”。

Markdown 第 5 章只写 Excel 文件和数量，例如：

```text
Excel：test-cases.xlsx；重点用例数量：2；常规用例数量：3；总用例数量：5
```

两组都为空时写“无”且不生成 Excel。详细命令和 JSON 示例见 `README.md`。
