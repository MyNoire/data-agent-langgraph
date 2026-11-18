# 10: 检索组运行与总结

**What to build:** 学习者看到检索组（⑤ 列名、⑥ 建表语句）在 88 题上的真实结果，以及六组的完整对比，能回答 R5 的三个问题：检索到的知识能不能帮模型写对 SQL、知识内容有没有用、建表语句是不是关键。

**Blocked by:** 07, 09

**Status:** ready-for-agent

会调用收费模型，正式运行还会调用 Embedding 和重排服务：运行前向用户确认，并记录模型、配置和运行目录。

- [x] ⑤⑥ 先用 lexical 各跑一次（跑通链路，结果保留）
- [x] ⑤⑥ 再用 hybrid + 重排各跑一次（正式结果）
- [x] 模型、提示词模板、执行上限、题集与 07 的四次运行相同；不同之处写明
- [x] 六组总表：执行匹配（整体和分库）、有效 SQL 数、`prompt_tokens` 的中位数和平均数、耗时；lexical 的两次作为额外的行
- [x] 检索组按"表拿全 / 没拿全"拆开的题数和执行匹配
- [x] 建表语句的作用：①②、③④、⑤⑥ 三对的差
- [x] 72 道非冻结题的逐题分析：全量知识答对而检索答错的题，区分"表没拿全"和"拿全仍错"；±2 道题以内当作噪声
- [x] 写明：toxicology 只有 20 个 Chunk，`top_k=10` 已是一半；hybrid + 重排的检索耗时；每组只跑一次
- [x] 不根据结果调整 `top_k`、渲染规则、检索配置或提示词
- [x] 文档更新：SQL 评测文档（结果和结论）、路线图（R5 移到已完成）、README、模块说明
- [x] 列出留给用户决定的事项：命令行和 HTTP 入口的默认 Context Source 要不要换、预测 SQL 与金标准 SQL 的执行时间对比、`top_k` 的可选对比

## Comments

- 运行（2026-11-17，用户同意后依次运行；配置与工单 07 相同：`gpt-6.1-sol`、`direct-v2`、`single-v2`、`r5-schema-v1`、题集 sha256 `673c8a45…`、行数上限 10000、超时 20 秒、4 个线程、无 evidence；另加 `--retrieval-cases evals/retrieval/retrieval_cases.json`）。运行目录都在 `artifacts/runs/evaluation/2026-11-17/` 下：
  - ⑤a lexical columns `d64bbf8cc86843d29bdcf32709986755`，⑥a lexical ddl `68dc9080dec0456a819b37d3d57eb831`
  - ⑤ hybrid + 重排 columns `46a797e82f674e3198bf5f53cf377ed8`，⑥ hybrid + 重排 ddl `6a38d4c3c55048b3a200da1faca192f2`
  - 检索版本：hybrid + 重排为 `r4-hybrid-v1+r4b-rerank-v1`（Embedding `siliconflow:Qwen/Qwen3-Embedding-8B:4096:content-v1:query-instruct-v1`，重排 `siliconflow:Qwen/Qwen3-Reranker-8B:content-v1:task-instruct-v1`）；lexical 为 `r3-bm25-v1`。
- 六组总表、表拿全拆分和结论写在 `docs/evaluation/sql-evaluation.md` 的"R5 结果"一节。要点：执行匹配 ① 42、② 43、③ 44、④ 46、⑤ 44、⑥ 43（lexical ⑤a 41、⑥a 44），非冻结题 35–37/72，全部在噪声内；⑤⑥ 与 ③④ 持平而输入约 37%；表没拿全 9 题只对 1 题。
- 非冻结题逐题（只记题号）：
  - ③ 对 ⑤ 错：1411（表没拿全）、1392（拿全仍错）；⑤ 对 ③ 错：206、1352、1381。
  - ④ 对 ⑥ 错：1411（表没拿全）、201、1317（拿全仍错）；⑥ 对 ④ 错：1381。
  - 六组都错：29 道。
- 检索没有出错的题：hybrid + 重排两次都没有 `no_match` 或服务错误。⑥a 有 1 道题（1387，非冻结）是模型写的 SQL 在 SQLite 执行时报 `OperationalError`，不是检索问题。
- 交叉核对：拿全率 hybrid + 重排 79/88 = 89.8%、lexical 74/88 = 84.1%，与检索评测的 Complete Recall@10 相同（R4b：89.8%；R3：84.1%），说明图里的检索和检索评测是同一份行为。
- 费用：检索组四次的模型费用约 0.077 + 0.080 + 0.084 + 0.088 = 0.33 元，八次合计约 0.77 元；另有硅基流动 Embedding 和重排各 176 次调用（单价未知）。
- 和工单不一样的地方：耗时只有每题 p50（含模型、检索和执行），检索自己的耗时没有单独记；hybrid + 重排比全量知识每题多约 4 秒。
- 没有根据结果调整 `top_k`、渲染规则、检索配置或提示词。
- 留给用户决定的事项：
  1. 命令行 `query ask` 和 HTTP 入口的默认 Context Source 要不要从 `schema` 换成别的。按这一轮的结果，换成 `retrieval` 在准确率上看不出好处，还要多 4 秒和两次付费服务调用；`knowledge` 只适合小库。
  2. 同一配置重跑一次量随机波动（用户在工单 07 时决定不跑；六组差距都在 ±4 道以内，没有这个数就说不出哪组更好）。
  3. 预测 SQL 与金标准 SQL 的执行时间对比。
  4. `top_k` 的可选对比（toxicology 只有 20 个 Chunk，`top_k=10` 已是一半；student_club 的 9 道缺表题是 `top_k` 能影响的部分）。
  5. 29 道六组都错的非冻结题的失败分析：给更多结构和知识都没用，问题可能在 SQL 写法或 BIRD 题目需要的业务口径（evidence 不进知识库）。
