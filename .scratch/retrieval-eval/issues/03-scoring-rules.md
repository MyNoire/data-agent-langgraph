# 03: 计分规则（结对核心）

**What to build:** 检索评测报告里给出可解释的指标：首次命中名次、Table Complete Recall@K 和 Table Recall@K（K=5/10）、Hits to Cover、Column Recall@10（只作诊断），K 都以 Hit 计，以及逐题名次和整体/按库的汇总。口径见 spec 的"计分规则"和 GLOSSARY 的"评测"一节。

**Blocked by:** 02

**Status:** ready-for-human

乒乓 TDD，全部通过 `evaluate_retrieval` 加假检索器来测：Claude 写一个失败的测试并说明它验证什么，用户写实现让它通过，Claude 审查。

- [x] 同一张表取第一次出现的 Hit rank；列 Chunk 也算它所属的表命中；没命中的相关表名次为空
- [x] 关系类 Hit 按 `chunk_key` 归到源表（`chunk_key` 的格式由测试固定）
- [x] 相关表首次命中在第 7 个 Hit 时，K=5 不完整、K=10 完整
- [x] Hits to Cover 的正常值，以及没有全覆盖时为空
- [x] 没有相关列的题不参与 Column Recall 平均，并统计这类题的数量
- [x] `input_error` 题计入分母
- [x] 汇总分整体和按库
- [x] 冻结标记原样出现在逐题结果里，汇总不拆分冻结题
- [ ] 用户能讲清楚每个指标怎么算、为什么 K 以 Hit 计

## Comments

- K 改为以 Hit 计（2026-11-05 用户决定）：系统里能调的只有 top_k，以表计的 K 预设了"按表取整个 Document"的上下文组装，而这一步还没有。K 取 5/10，最大不超过 10；两个库的 Chunk 数（20、64）都大于 K，不再需要"无区分度"标记。已写好的首次命中名次和 Recall@K 由 Claude 按新口径重构。

- 不做名称大小写归一：Hit 的定位字段、关系 Chunk 的 chunk_key 和 Relevant Set（题集准备时已映射回 Corpus 写法）用的是同一份 Corpus 拼写。
