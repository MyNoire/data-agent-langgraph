# 03: token 用量进报告

**What to build:** 学习者在 SQL 评测报告里逐题看到模型接口返回的 `prompt_tokens` 和 `completion_tokens`，以及它们的汇总，用来比较各种 Context Source 的上下文成本和估算费用。不引入 tokenizer。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [x] SQL 生成函数除 SQL 文本外，返回接口报告的 `prompt_tokens` 和 `completion_tokens`；接口没有返回用量时为空
- [x] 查询图把用量写进状态，其余行为不变
- [x] SQL 评测的逐题记录带上两项用量；汇总报各自的中位数、平均数和总数，只对有值的题计算
- [x] `eval direct` 继续工作，同样记录用量
- [x] 注入假 SQL 生成器的现有测试改成新的返回形式，断言的行为不变
- [x] 日志和运行记录仍然不写提示词和问题原文
- [x] 测试用假 SQL 生成器返回固定用量，覆盖：逐题记录、汇总、用量为空时的处理

## Comments

- 实现（2026-11-14，Claude，TDD）：`generate_sql` 返回 `GeneratedSql(sql, prompt_tokens, completion_tokens)`，`SqlGenerator` 的约定随之改变。查询图的生成节点把三项都写进状态；图评测从流里取，direct 评测直接取。报告多一项 `token_usage`，放在报告顶层而不是 `summary` 里：`summary` 是七个计数，多处按整个字典比对。
- 用量取模型接口返回的 `usage`；`completion_tokens` 包含推理模型的推理 token，这正是估算费用需要的数。接口返回的 `usage` 为空时两项记为 None，不影响这道题的计分。
- 约 40 处返回字符串的假生成器改成返回 `GeneratedSql(...)`，断言没有改。另有两个假的模型响应补了 `usage` 字段：真实的响应对象一定有这个属性（值可以为空），所以没有在代码里为"没有这个属性"做兼容。
- 测试：`tests/contracts/test_sql_generation.py` 新增 2 个（有用量、没有用量），`tests/test_graph_eval.py` 新增 1 个（逐题和汇总，含用量为空的题），`tests/test_eval.py` 新增 1 个（direct 的 model 与 fixture）。
- 没验证：真实接口返回的用量数值。新模型是否返回 `usage`、推理 token 占多少，要到工单 07 先跑 ① 一组时才知道。
