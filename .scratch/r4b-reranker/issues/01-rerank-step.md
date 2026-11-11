# 01: 检索里的重排流程

**What to build:** 检索调用方在请求里打开 `rerank`，`retrieve_knowledge` 就按所选方法取前 20 个候选，交给传入的 Reranker 打分，按分数重新排序后截到 `top_k`，结果里写明 Rerank Profile。本工单用假 Reranker 把整条流程打通并测好，不联网；真实的 Reranker 在工单 02 接上。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

规格：`.scratch/r4b-reranker/spec.md`。用户决定本功能全部由 Claude 用 TDD 完成，一次一个测试；完成后向用户讲解关键决定。

- [x] Reranker 接口：`profile_id` 和 `rerank(query, documents)`，按输入顺序返回每个文档的分数；重排失败的错误类别为 `rerank_error`，消息是固定的安全文本
- [x] `RetrievalRequest.rerank` 默认关闭；开启时 `top_k` 为 20 可以、21 在构造请求时被拒绝；不开时 `top_k` 上限仍是 50
- [x] 开启重排时，三种 `method` 都固定取 20 个候选，与 `top_k` 无关（hybrid 仍是两路各 50 个融合后取前 20）
- [x] Reranker 收到查询原文和各候选的 `content` 原文
- [x] 重排后每个 Hit 的 `score` 是相关性分数，按分数从高到低排，同分保持重排前的顺序，`rank` 从 1 连续
- [x] `top_k` 小于候选数时，先对全部候选重排再截断：排在候选后段的 Chunk 可以进入结果
- [x] 没有候选时返回 `no_match`，不调用 Reranker
- [x] 查询没有有效词时仍报 `input_error`，Embedding 相关错误不变，都不调用 Reranker
- [x] Reranker 失败时报 `rerank_error`，不返回未重排的 Hit
- [x] 调用 Reranker 发生在只读会话关闭之后
- [x] 不开重排时不调用 Reranker，结果和之前完全一样
- [x] 结果增加 `rerank_profile_id`（没重排时为空），结果版本号升级；重排过的结果配置版本为"方法的版本 + `r4b-rerank-v1`"，其含义写在版本常量旁
- [x] 真实 PG 测试（假 Reranker、假 Embedder）覆盖以上行为；请求校验用单元测试
- [x] 知识库文档的检索一节写明重排这一步（现在做什么、默认关闭、候选数、错误类别）；`RetrievalHit.score` 的说明补上重排后的含义

## Comments

- 实现（2026-11-11，Claude，TDD）：2816c2d。新增 `Reranker` 接口、`RerankError`、`rerank_rows` 和两个常量（候选数 20、重排步骤版本 `r4b-rerank-v1`）；`RetrievalRequest.rerank`；`retrieve_knowledge` 多一个 `reranker` 参数。先写失败测试再实现的有 5 个：按分数重排、先重排再截断、无候选不调用、`rerank_error`、请求校验。另外 5 个守护测试一写就通过（同分保序、候选数上限、hybrid、检索失败时不调用、不开重排时不变），都用"故意改坏代码"确认过会失败。
- 审查（两轴，c081af0）：规范轴没有硬性违规，规格轴没有实现错误。按审查改了：
  - `search_chunks` / `vector_chunks` 改为由调用方传 `limit`，不再靠复制请求改 `top_k` 来取候选。原写法在开启重排时会造出一个 `top_k=50` 的请求，绕过了本工单新加的"开启重排时 `top_k` 不超过 20"校验。
  - 补了两个测试：lexical 的候选数不跟 `top_k` 走；hybrid 融合后截到候选数。两个都做了改坏验证。
  - 常量改名为 `RERANK_STEP_VERSION`，避免和 Rerank Profile 混淆（GLOSSARY 把"rerank config"列为 Rerank Profile 的避免用词）；`zip(strict=True)` 补了说明它挡住什么的注释；`modules.md` 补上新的导出。
- 语义澄清（已同步到规格，用户可否决）：`rerank_profile_id` 和带重排的配置版本表示"请求按哪套配置执行"。开启了重排但没有候选、没有调用 Reranker 时也照常写明；请求没有开启重排时才为空。理由：评测报告按这两项汇总一次运行用到的配置，零候选的题若记成"没重排"，一次运行里会出现两套配置。
- 没有测试的验收项："调用 Reranker 发生在只读会话关闭之后"只由代码结构保证（重排写在 `with` 块之外）。要测它得查数据库的连接状态，而连接关闭有延迟，测试容易不稳定。
- 留给工单 02：开启重排但没有传 Reranker 时，现在会抛 `AttributeError`；按配置创建真实 Reranker 在工单 02 完成。Reranker 违反接口约定（条数不对、分数不是有限数）时不是 `rerank_error`，这些检查按规格放在真实客户端里。
- 测试结果：`pytest tests/integration --require-postgres` 114 passed；`pytest tests --ignore=tests/integration` 443 passed，1 skipped，1 failed（`test_console_and_module_share_help`，`querypilot` 不在 PATH 上，与本工单无关）。本工单没有真实调用 Reranker 服务。
