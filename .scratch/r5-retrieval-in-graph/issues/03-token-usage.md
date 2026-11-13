# 03: token 用量进报告

**What to build:** 学习者在 SQL 评测报告里逐题看到模型接口返回的 `prompt_tokens` 和 `completion_tokens`，以及它们的汇总，用来比较各种 Context Source 的上下文成本和估算费用。不引入 tokenizer。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] SQL 生成函数除 SQL 文本外，返回接口报告的 `prompt_tokens` 和 `completion_tokens`；接口没有返回用量时为空
- [ ] 查询图把用量写进状态，其余行为不变
- [ ] SQL 评测的逐题记录带上两项用量；汇总报各自的中位数、平均数和总数，只对有值的题计算
- [ ] `eval direct` 继续工作，同样记录用量
- [ ] 注入假 SQL 生成器的现有测试改成新的返回形式，断言的行为不变
- [ ] 日志和运行记录仍然不写提示词和问题原文
- [ ] 测试用假 SQL 生成器返回固定用量，覆盖：逐题记录、汇总、用量为空时的处理
