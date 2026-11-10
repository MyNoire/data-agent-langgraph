# 06: 三方法评测与分析

**What to build:** 在同一份 88 题、同一份 Corpus 上，用 `lexical`、`vector`、`hybrid` 各跑一次检索评测，得到可以追溯的对比数字，并用非冻结题说明语义召回补上了什么、又让什么变差了。

**Blocked by:** 04, 05

**Status:** ready-for-agent

- [x] **征得用户同意后**，三种方法各跑一次：top_k=20，K=5/10，Column Recall@10；记录命令、题集 sha256、Knowledge Scope、Profile 和三个运行目录
- [x] 中文对比表：三种方法的主指标、Table Recall、Hits to Cover、Column Recall@10 和检索耗时，分整体和两个库
- [x] 用临时脚本读三份 `result.json`，逐题标出非冻结题中"BM25 失败、vector 或 hybrid 救回"和"hybrid 比 BM25 变差"的题，并归纳原因；按 ADR 0001，只分析，不改知识、参数或代码
- [x] 更新检索评测文档（第 3 节加三方法结果）、知识库文档（向量与混合检索一节）和路线图
- [x] 提醒用户更新面试笔记 `检索评测指标设计.md`

## Comments

- 真实运行（2026-11-10，代码 b5f3355）。用户选择只跑 vector 和 hybrid，lexical 沿用 2026-11-06 的 BM25 基线运行。共约 176 次 Embedding 调用。
  - 命令：`python -m querypilot eval retrieval --cases evals/retrieval/retrieval_cases.json --method vector`（hybrid 同理）。
  - 题集 sha256 `6014f798…`；Scope：toxicology `d4792b3c…`，student_club `66f8085b…`；Profile `siliconflow:Qwen/Qwen3-Embedding-8B:4096:content-v1:query-instruct-v1`；配置版本 `r4-vector-v1` / `r4-hybrid-v1`。三份报告的题集和 Scope 一致。
  - 运行目录：lexical `artifacts/runs/evaluation/2026-11-06/3c83f0afc2de4befb16035186fbd7ed9`，vector `artifacts/runs/evaluation/2026-11-10/5b1da09ba55d47c08f33e259d68ed35c`，hybrid `artifacts/runs/evaluation/2026-11-10/fe098b1338194d0faaa39cd5ce185cec`。三份 `result.json` 里都没有问题原文。
- 结果（整体 Complete Recall@5 / @10）：lexical 64.8% / 84.1%，vector 62.5% / 80.7%，hybrid 68.2% / 85.2%。Column Recall@10：67.5% / 66.8% / 73.6%。P50 检索耗时：276 / 445 / 489 ms。分库：toxicology 上 vector 最好（77.5% / 97.5%），student_club 上 hybrid 最好（64.6% / 79.2%）。
- 非冻结题分析：用临时脚本读三份 `result.json`（在 scratchpad，没有提交）。与 BM25 相比，K=5 时 vector 救回 11 道、弄丢 12 道；hybrid 救回 7 道、弄丢 3 道，到 K=10 弄丢 0 道。分析写进检索评测文档第 4 节：向量补语义和词形，丢字面的结构词；hybrid 变差的题都在 K=5 边界上；等权 RRF 会冲淡只被一路找到的 Chunk。按 ADR 0001，只分析，不做修改。
- 文档：检索评测第 3、4 节，知识库第 7 节加链接，路线图把 R4 移到"已完成"，README 的状态和结果表。
- 没做的：`method` 默认值仍为 lexical（规格用户故事 12：等评测结果出来再由用户决定）。hybrid 提升有限，还多了延迟和费用，是否改默认值交给用户。
