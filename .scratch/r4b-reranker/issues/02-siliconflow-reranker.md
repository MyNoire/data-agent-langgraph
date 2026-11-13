# 02: 硅基流动 Reranker 客户端与指令验证

**What to build:** 写出向硅基流动发重排请求的代码：通过 OpenAI SDK，用 `QUERYPILOT_RERANK_BASE_URL / MODEL / API_KEY` 调用重排接口，把返回的分数对回每个候选，并检查返回是否正常。`retrieve_knowledge` 在开启重排而没有传入 Reranker 时，按这份配置创建它。另外用一次真实调用确认 `instruction` 参数是否生效，据此确定 Rerank Profile 的指令版本。

**Blocked by:** 01

**Status:** ready-for-agent

规格：`.scratch/r4b-reranker/spec.md`。真实调用要用户明确同意后才做。

- [x] 配置 `QUERYPILOT_RERANK_BASE_URL`、`QUERYPILOT_RERANK_MODEL`、`QUERYPILOT_RERANK_API_KEY`，与 Embedding 和对话模型分开；地址停在 `/v1`，由代码拼上 `/rerank`；密钥不出现在任何输出和错误消息里
- [x] 用 OpenAI SDK 客户端的通用 `post` 向 `/rerank` 发请求，写法与 Embedding 客户端一致（鉴权、60 秒超时、可注入 HTTP 传输）；不新增运行时依赖
- [x] 重试用 SDK 默认行为（连接失败、超时、限流、服务端错误自动重试 2 次），不另写；测试覆盖"第一次返回服务端错误、重试后成功"和"一直失败则报重排错误"
- [x] 请求带模型、查询和文档列表，不要求返回文档原文；分数按返回的 `index` 还原成输入顺序
- [x] 返回条数与文档数不同、`index` 重复或越界、分数不是有限数、HTTP 错误、超时，都报重排错误；底层原因保留在异常链里
- [x] Rerank Profile 为 `siliconflow:<模型>:content-v1:<指令版本>`
- [x] 客户端测试用假的 HTTP 传输，不发真实请求，覆盖以上各条
- [x] `retrieve_knowledge` 开启重排而没传 Reranker 时按配置创建；不开重排时不创建
- [x] **指令验证（需用户同意）**：用自编的一个问题和几段文本（不取自评测题），带指令和不带指令各调用几次。带指令被拒绝，或分数差异不超过两次不带指令调用之间的抖动，就按 `no-instruct` 实现、不传该参数；否则为 `task-instruct-v1`，传 Embedding 查询指令的同一句话（代码里只保留一份）。验证结果记在本工单的 Comments 里
- [x] 同一次真实调用顺带确认：地址和响应格式与规格一致，记录一次调用的耗时

## Comments

- 实现（2026-11-11 至 11-12，Claude，TDD）：7448c35。新增 `SiliconFlowReranker` 和 `RerankSettings`，从 retrieval 模块的公开入口导出；`retrieve_knowledge` 在开启重排而没传 Reranker 时按配置创建。先写失败测试再实现的有：请求格式与按 `index` 还原顺序、各种不正常的返回报 `rerank_error`、按配置创建、带指令的请求和 Profile。重试的两个测试和"不开重排时不创建"一写就通过（重试是 SDK 自带的行为），都用"故意改坏代码"确认过会失败。
- **指令验证（2026-11-12，用户同意后运行，共 6 次真实调用）**：
  - 输入是自编的 1 个问题（哪个仓库上月发出的包裹最多）和 5 段自编的英文短文本（两张表、两个列、一条关系），不取自评测题。模型 `Qwen/Qwen3-Reranker-8B`。
  - 不带指令 3 次：5 段文本的分数约为 0.035 / 0.476 / 0.706 / 0.0002 / 0.321，三次之间最大相差 0.024。
  - 带指令 3 次（Embedding 查询指令的同一句话）：约为 0.639 / 0.858 / 0.521 / 0.002 / 0.934，三次之间最大相差约 0.008。
  - 带与不带之间最大相差 0.638，远大于抖动，排序也变了：不带指令时排第一的是 `shipped_on` 列，带指令后是关系和 `parcel_count` 列，`warehouses` 表从第 4 升到第 3。计费 token 从 470 变为 550。
  - 结论：接口使用 `instruction`，按 `task-instruct-v1` 实现，请求里传 `QUERY_TASK`。这句话只在 Embedding 客户端定义一份，经 knowledge 模块的公开入口导出；改动它会同时改变 Embedding Profile 和 Rerank Profile 的含义。
- **地址、响应格式和耗时（同一批调用）**：
  - 地址 `<base_url>/rerank`（`base_url` 停在 `/v1`）可用。
  - 响应顶层是 `id`、`meta`、`results`；每条结果有 `document`、`index`、`relevance_score`。不传 `top_n` 时 5 段文本全部返回。`results` 的结构与规格一致。
  - 与对齐时的记录有一处不同：用量在 `meta.tokens` 下，不是顶层的 `tokens`。本功能不记录用量，没有影响。
  - 耗时：不带指令 427 / 1048 / 251 毫秒；带指令 8528 / 6879 / 1615 毫秒。只有 3 次，不能下结论，但带指令的调用明显更慢，工单 04 的 P50 / P95 要留意。
- 配置问题：用户的 `.env` 里重排那组的密钥行，变量名写成了 `QUERYPILOT_EMBEDDING_API_KEY`（与 Embedding 的重名），`QUERYPILOT_RERANK_API_KEY` 不存在。这次验证在仓库外的临时脚本里借用了那一行；正式代码只读 `QUERYPILOT_RERANK_API_KEY`，需要用户改名后工单 03、04 才能真实调用。
- 审查（两轴）：规范轴没有硬性违规，规格轴没有阻塞性问题。按审查改了：补了"200 但 JSON 不完整""200 但不是 JSON"两个用例（都做了改坏验证）；超时测试改用 SDK 公开的 `openai.APITimeoutError` 判断；去掉重排测试里一个意图不明的指令前缀断言。
- 没有改的审查意见：
  - `RerankSettings` 和 `EmbeddingSettings` 只差前缀，没有合并：只有两处，不提前抽象。
  - 分数是字符串数字或布尔值时会被接受；分数是几百位的超大整数时抛 `OverflowError` 而不是 `rerank_error`。真实服务不会这样返回，不加检查。
  - 开启重排但缺少 `QUERYPILOT_RERANK_*` 时，抛的是配置校验错误，不是 `rerank_error`，与缺少 Embedding 配置时的现有行为一致。
- 没有验证的：`SiliconFlowReranker` 这个类本身还没有对真实服务调用过（指令验证用的是临时脚本，请求体相同）；第一次真实调用会发生在工单 04。
- 测试结果：`pytest tests/integration --require-postgres` 116 passed；`pytest tests --ignore=tests/integration` 456 passed，1 skipped，1 failed（`test_console_and_module_share_help`，`querypilot` 不在 PATH 上，与本工单无关）；审查后的改动只涉及 `tests/test_rerank_client.py`，单独重跑 15 passed。
