# 02: 硅基流动 Reranker 客户端与指令验证

**What to build:** 写出向硅基流动发重排请求的代码：用 `QUERYPILOT_RERANK_BASE_URL / MODEL / API_KEY` 调用重排接口，把返回的分数对回每个候选，并检查返回是否正常。`retrieve_knowledge` 在开启重排而没有传入 Reranker 时，按这份配置创建它。另外用一次真实调用确认 `instruction` 参数是否生效，据此确定 Rerank Profile 的指令版本。

**Blocked by:** 01

**Status:** ready-for-agent

规格：`.scratch/r4b-reranker/spec.md`。真实调用要用户明确同意后才做。

- [ ] 配置 `QUERYPILOT_RERANK_BASE_URL`、`QUERYPILOT_RERANK_MODEL`、`QUERYPILOT_RERANK_API_KEY`，与 Embedding 和对话模型分开；地址停在 `/v1`，由代码拼上 `/rerank`；密钥不出现在任何输出和错误消息里
- [ ] httpx 升为运行时依赖
- [ ] 请求带模型、查询和文档列表，不要求返回文档原文；分数按返回的 `index` 还原成输入顺序
- [ ] 返回条数与文档数不同、`index` 重复或越界、分数不是有限数、HTTP 错误、超时，都报重排错误；底层原因保留在异常链里
- [ ] Rerank Profile 为 `siliconflow:<模型>:content-v1:<指令版本>`
- [ ] 客户端测试用假的 HTTP 传输，不发真实请求，覆盖以上各条
- [ ] `retrieve_knowledge` 开启重排而没传 Reranker 时按配置创建；不开重排时不创建
- [ ] **指令验证（需用户同意）**：用自编的一个问题和几段文本（不取自评测题），带指令和不带指令各调用几次。带指令被拒绝，或分数差异不超过两次不带指令调用之间的抖动，就按 `no-instruct` 实现、不传该参数；否则为 `task-instruct-v1`，传 Embedding 查询指令的同一句话（代码里只保留一份）。验证结果记在本工单的 Comments 里
- [ ] 同一次真实调用顺带确认：地址和响应格式与规格一致，记录一次调用的耗时

## Comments
