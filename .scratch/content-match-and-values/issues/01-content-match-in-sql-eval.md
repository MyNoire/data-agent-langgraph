# 01: 内容匹配进入 SQL 评测

**What to build:** SQL 评测在严格执行匹配之外，同时给出内容匹配（[ADR 0003](../../../docs/adr/0003-content-match-as-primary-sql-metric.md)、规格「实现决定 / 内容匹配」）。用户跑 `eval graph` 或 `eval direct`（fixture 也行），在逐题记录、报告汇总、`tables_complete` 拆分和 `export-public` 副本里都能看到两个口径并列。严格口径的含义和数字不变。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [x] 评测模块公开一个纯函数：输入预测结果行和金标准结果行，返回是否内容匹配；评测运行和离线重算共用它
- [x] 规则测试覆盖四条形式差异的正例：多出列 / 列顺序不同、重复行、数字字符串（含末尾 `%`、`4` 与 `4.0`）、单空格拼接列
- [x] 规则测试覆盖反例：四舍五入才相等、比例与百分比换算、行数或行集合不同、拼接用了别的分隔符、金标准为空而预测不为空
- [x] 测试体现"严格执行匹配为真则内容匹配为真"
- [x] 这组"算对 / 不算对"的例子交给用户审阅，用户确认后再定稿
- [x] `eval graph` 与 `eval direct` 的逐题记录新增 `content_match`；没有预测或执行出错记为 `false`；`outcome` 和 `execution_match` 不变
- [x] 报告汇总在执行匹配旁边给出内容匹配的题数和比例；检索组的 `tables_complete` 拆分同样并列两个口径
- [x] `export-public` 白名单带上 `content_match` 与对应汇总字段，有测试
- [x] SQL 评测文档的「计分」一节写明两个口径的定义（用 GLOSSARY 的名字），不称 EX
- [x] 模块边界测试和全部单元测试通过

## Comments

**2026-11-19 实现（665b0a8）**

- 内容匹配函数在评测模块的 `_compare` 里，从评测公开入口导出 `content_match`。逐题记录、汇总（`summary.content_match`、`content_match_rate`）、`tables_complete` 拆分（`content_matched`、`content_rate`）和 `export-public` 都带上了；`eval direct` 也一样。旧运行目录没有这些字段，导出时照常跳过。
- 映射搜索最初是直接枚举「列的分组 × 顺序 × 预测列排列」，最坏情况下太慢：金标准 5 列、预测 8 列都是文本又不匹配时要 66 秒，金标准 8 列、预测 12 列要 442 秒。改成两步剪枝：
  - 每个单元只把「取值集合相同」的预测列当候选，因为行的集合相同，就意味着对应列的取值集合也相同；
  - 拼接单元只用金标准第一行往后拼，拼出的前缀加一个空格不是某个预测文本值的前缀就停下。
  改完后金标准 8 列、预测 12 列、1 万行，不匹配 0.65 秒，匹配 1.1 秒。仍然存在的最坏情况：很多列的取值集合完全相同（比如多列都只有 0/1），回溯要试遍候选的排列。
- 校验运行目录的 `_validate_run_report` 没有加内容匹配的一致性检查，旧运行没有这个字段。
- 真实数据的抽查（不付费）：R5 的 ⑤（`46a797e8`）重放后，严格 44 / 内容 62；非冻结题 36 / 53。逐组的数字放在工单 02。
- 测试：`tests/test_content_match.py` 14 个；图评测新增 2 个，已有的 4 个精确比较汇总的断言补上了新字段。全部单元测试 `pytest tests --ignore=tests/integration`：572 通过，1 个失败是之前就有的 `test_console_and_module_share_help`（`querypilot` 不在 PATH 上）。模块边界测试通过。

**2026-11-19 用户审阅**：例子确认（"没问题"）。分支已推送，Draft PR #5。
