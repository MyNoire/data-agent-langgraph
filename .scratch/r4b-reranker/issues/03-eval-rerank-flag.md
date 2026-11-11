# 03: 评测的 `--rerank` 开关

**What to build:** 评测开发者用 `eval retrieval --method <方法> --rerank` 跑一次带重排的检索评测。运行器、指标和 `top_k=20` 都不变，运行配置和报告能看出这次有没有重排、用的哪个 Rerank Profile。

**Blocked by:** 01, 02

**Status:** ready-for-agent

规格：`.scratch/r4b-reranker/spec.md`。本工单不做真实运行。

- [ ] `evaluate_retrieval` 增加 `rerank`，默认关闭，传进每一道题的检索请求
- [ ] `eval retrieval` 增加 `--rerank`，可与 `--method` 任意组合；不带时行为和输出与现在一样
- [ ] 默认检索器在一次运行里只创建一个 Reranker，所有题共用；不开重排时不创建，不需要 Reranker 配置
- [ ] 运行配置记录是否重排；报告的 `config.retrieval` 列出结果实际用到的 Rerank Profile 和带重排的配置版本
- [ ] 某一题重排失败时中止整批运行，和现在 Embedding 失败时一样（运行器只对 `input_error` 按题计分，其他检索错误都中止）；不为重排新增处理
- [ ] 假检索器测试覆盖：`rerank` 传进请求、写进运行配置和报告
- [ ] 检索评测文档"怎么跑"一节补上 `--rerank` 的用法、费用提示和报告里的新字段

## Comments
