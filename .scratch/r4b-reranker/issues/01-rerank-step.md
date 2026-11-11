# 01: 检索里的重排流程

**What to build:** 检索调用方在请求里打开 `rerank`，`retrieve_knowledge` 就按所选方法取前 20 个候选，交给传入的 Reranker 打分，按分数重新排序后截到 `top_k`，结果里写明 Rerank Profile。本工单用假 Reranker 把整条流程打通并测好，不联网；真实的 Reranker 在工单 02 接上。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

规格：`.scratch/r4b-reranker/spec.md`。用户决定本功能全部由 Claude 用 TDD 完成，一次一个测试；完成后向用户讲解关键决定。

- [ ] Reranker 接口：`profile_id` 和 `rerank(query, documents)`，按输入顺序返回每个文档的分数；重排失败的错误类别为 `rerank_error`，消息是固定的安全文本
- [ ] `RetrievalRequest.rerank` 默认关闭；开启时 `top_k` 为 20 可以、21 在构造请求时被拒绝；不开时 `top_k` 上限仍是 50
- [ ] 开启重排时，三种 `method` 都固定取 20 个候选，与 `top_k` 无关（hybrid 仍是两路各 50 个融合后取前 20）
- [ ] Reranker 收到查询原文和各候选的 `content` 原文
- [ ] 重排后每个 Hit 的 `score` 是相关性分数，按分数从高到低排，同分保持重排前的顺序，`rank` 从 1 连续
- [ ] `top_k` 小于候选数时，先对全部候选重排再截断：排在候选后段的 Chunk 可以进入结果
- [ ] 没有候选时返回 `no_match`，不调用 Reranker
- [ ] 查询没有有效词时仍报 `input_error`，Embedding 相关错误不变，都不调用 Reranker
- [ ] Reranker 失败时报 `rerank_error`，不返回未重排的 Hit
- [ ] 调用 Reranker 发生在只读会话关闭之后
- [ ] 不开重排时不调用 Reranker，结果和之前完全一样
- [ ] 结果增加 `rerank_profile_id`（没重排时为空），结果版本号升级；重排过的结果配置版本为"方法的版本 + `r4b-rerank-v1`"，其含义写在版本常量旁
- [ ] 真实 PG 测试（假 Reranker、假 Embedder）覆盖以上行为；请求校验用单元测试
- [ ] 知识库文档的检索一节写明重排这一步（现在做什么、默认关闭、候选数、错误类别）；`RetrievalHit.score` 的说明补上重排后的含义

## Comments
