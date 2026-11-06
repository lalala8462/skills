# diff-list 报告规则

报告以指定修订的最终业务效果为准。

## Agent 直接维护

Agent 直接编辑 report.md 中：

- 需求改动；
- DDL；
- DML；
- 整体流程；
- Mermaid；
- 特殊逻辑与测试关注点，其中需要明确既有功能影响。

不再把这些内容先写入 report-data.json。

## 脚本维护

以下章节由脚本生成或刷新，Agent 不重复维护：

- 项目与完整 Git SHA；
- 配置文件变化；
- Excel 文件名与四类用例数量；
- 本技能执行的静态验证说明。

配置变化由 configuration_file_change / configuration_parse_boundary 生成标准化内容，敏感值继续使用分析阶段的脱敏值。

脚本 refresh 只能改写这些机器维护章节，必须保留 Agent 已填写的业务章节。

## 测试用例

四类固定为需求验收、既有核心流程回归、关联回归、业务规则与边界验证。

每条预期至少包含一个页面、接口、日志、数据库、文件、缓存或下游行为中的可观察结果。

Agent 只引用 review_unit_ids，fact/evidence 由脚本自动展开和校验。

## 验证

结构校验和静态源码证据校验不代表构建、单元测试、数据库验证或真实环境联调通过。
