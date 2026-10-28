---
name: diff-list
description: "按指定 GitHub 仓库及 Git 修订生成最终行为提测 Markdown，涵盖业务流程、特殊逻辑、DDL、DML、配置及高风险测试场景；Windows 10，Git 净差异与 Java 8 AST 候选由脚本提取。"
---

# diff-list

输入 GitHub 仓库 URL 与对应 Git 提交号或明确比较范围；可以多项目。仓库由脚本自动缓存。**只读分析，不修业务代码、不执行 SQL、不提交 Git**。输出一份面向测试人员的 UTF-8 Markdown。

## 执行

1. Windows 10 使用 Git for Windows、Python 3.8+；**Java 8 AST 分析另需 JDK 8+ 的 `javac`、`java`**。按 `README.md` 运行 `collect_git.py` 和 `logic_candidates.py`，每项目分别取证。缓存由脚本自动管理，未指定修订不得改用 HEAD。
2. `net_effect` 的 `composed` 为选中提交的机械补丁净效果，`no_net_change` 为无净变化，`review_required` 表示合成不可靠、必须核查依赖。`logic_candidates.py` 输出 AST 表达式/调用、分支、状态、异常等变化，以及配置键新增/修改/删除的风险候选和跳过原因。**候选不是精确类型绑定、真实调用证明、环境生效事实或验收预期**。
3. 结合 `references/analysis-checklist.md` 验证候选：向下核实业务入口、分支、调用、DB/Redis、异常、重试、通知；向上追踪消费者。核实改动前行为、指定修订实际行为及已确认的业务预期。代码与需求冲突时说明当前缺陷；关键业务预期不清时在对话中询问，不编造。
4. 按 `references/report-rules.md` 处理需求、DDL、DML、配置新增/变更/废弃，精选有业务意义的特殊分支和高风险场景，不罗列普通回归。

## 文档与校验

每次先读取 `assets/diff-list-template.md`，用 `init_report.py` 建文件，保留全部标题、无关章节写“无”。Mermaid 使用兼容 8.8.3 的 `flowchart TD` 或 `sequenceDiagram`；重点测试逐场景列 **测试点、测试前提、预期结果**，结果可观察可判定。不得写本机源码路径、行号/代码依据、未决项或真实密钥。报告包含完整 40 位修订 SHA、仓库相对脚本路径以及实际验证范围。

运行 `validate_report.py`，核对项目与 SHA 的逐条对应关系、代码围栏、模板及测试标签；随后人工复核 Git 归属、AST/配置候选、业务语义及图文对应关系。默认写入 `G:\tmp`，不覆盖同名文件；最终交付路径、覆盖修订和真实验证范围。证据 JSON/AST 候选仅供内部分析，不复制到报告。
