# 03: 计分规则（结对核心）

**What to build:** 检索评测报告里给出可解释的指标：Table Ranking、Table Complete Recall@K 和 Table Recall@K（K=2/3/5，以表计）、Tables to Cover、Column Recall@20（以 Hit 计，只作诊断），以及逐题名次和整体/按库的汇总。口径见 spec 的"计分规则"和 GLOSSARY 的"评测"一节。

**Blocked by:** 02

**Status:** ready-for-human

乒乓 TDD，全部通过 `evaluate_retrieval` 加假检索器来测：Claude 写一个失败的测试并说明它验证什么，用户写实现让它通过，Claude 审查。

- [ ] 同一张表的多个 Hit 只算一次，名次取第一次出现的位置；列 Chunk 也能把它的表带进 Table Ranking
- [ ] 关系类 Hit 按 `chunk_key` 归到源表（`chunk_key` 的格式由测试固定）
- [ ] 相关表排在第 3 张时，K=2 不完整、K=3 完整
- [ ] Tables to Cover 的正常值，以及没有全覆盖时为空
- [ ] 没有相关列的题不参与 Column Recall 平均，并统计这类题的数量
- [ ] `input_error` 题计入分母
- [ ] 汇总分整体和按库；库的表数不超过 K 时，这一项标为"无区分度"
- [ ] 冻结标记原样出现在逐题结果里，汇总不拆分冻结题
- [ ] 用户能讲清楚每个指标怎么算、为什么 K 以表计

## Comments

- 不做名称大小写归一：Hit 的定位字段、关系 Chunk 的 chunk_key 和 Relevant Set（题集准备时已映射回 Corpus 写法）用的是同一份 Corpus 拼写。
