# 01: 内容匹配进入 SQL 评测

**What to build:** SQL 评测在严格执行匹配之外，同时给出内容匹配（[ADR 0003](../../../docs/adr/0003-content-match-as-primary-sql-metric.md)、规格「实现决定 / 内容匹配」）。用户跑 `eval graph` 或 `eval direct`（fixture 也行），在逐题记录、报告汇总、`tables_complete` 拆分和 `export-public` 副本里都能看到两个口径并列。严格口径的含义和数字不变。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] 评测模块公开一个纯函数：输入预测结果行和金标准结果行，返回是否内容匹配；评测运行和离线重算共用它
- [ ] 规则测试覆盖四条形式差异的正例：多出列 / 列顺序不同、重复行、数字字符串（含末尾 `%`、`4` 与 `4.0`）、单空格拼接列
- [ ] 规则测试覆盖反例：四舍五入才相等、比例与百分比换算、行数或行集合不同、拼接用了别的分隔符、金标准为空而预测不为空
- [ ] 测试体现"严格执行匹配为真则内容匹配为真"
- [ ] 这组"算对 / 不算对"的例子交给用户审阅，用户确认后再定稿
- [ ] `eval graph` 与 `eval direct` 的逐题记录新增 `content_match`；没有预测或执行出错记为 `false`；`outcome` 和 `execution_match` 不变
- [ ] 报告汇总在执行匹配旁边给出内容匹配的题数和比例；检索组的 `tables_complete` 拆分同样并列两个口径
- [ ] `export-public` 白名单带上 `content_match` 与对应汇总字段，有测试
- [ ] SQL 评测文档的「计分」一节写明两个口径的定义（用 GLOSSARY 的名字），不称 EX
- [ ] 模块边界测试和全部单元测试通过
