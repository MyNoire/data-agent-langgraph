# 03: 验收运行与文档

**What to build:** 学习者拿到一组可追溯的数字：同样的 88 题、同样的 hybrid + 重排，并发之后一次评测用多久，召回指标是否和串行时一致。检索评测文档写明 `--workers` 怎么用、耗时该怎么比较。

**Blocked by:** 01, 02

**Status:** ready-for-agent

规格：`.scratch/eval-concurrency/spec.md`。由 Claude 完成。真实运行收费（约 26 万 Token），**要用户明确同意后才跑**。

- [x] 运行前确认：知识库在运行，`QUERYPILOT_EMBEDDING_*` 和 `QUERYPILOT_RERANK_*` 已配置，用户同意
- [x] 跑一次 `eval retrieval --method hybrid --rerank --workers 8`，题集和 Corpus 与 R4b 的 hybrid + 重排运行相同；记录命令、运行目录和 `config.json`
- [x] 总时长与串行那次的 18.9 分钟对比，写明服务本身有波动，一次运行只能说明量级
- [x] 召回指标与 R4b 的 hybrid + 重排运行对比，±1–2 道题属于噪声；差异超出时先查原因，不直接下结论
- [x] 运行中没有出现多线程共用客户端导致的错误；出现了就如实记录并停下来和用户商量
- [x] 不为了对比重跑串行；不看任何题的结果来改检索（ADR 0001）
- [x] 检索评测文档补上：`--workers` 的用法；报告和运行配置里的 `workers`；耗时 P50 / P95 只在 `workers=1` 的运行之间比较；进度事件按完成顺序写、`completed_cases` 的含义；验收运行的结果
- [x] `docs/roadmap.md` 记下这项已完成

## Comments

- 验收运行（2026-11-12，用户同意后执行，只跑了这一次）：
  - 命令：`python -m querypilot eval retrieval --cases evals/retrieval/retrieval_cases.json --method hybrid --rerank --workers 8`
  - 运行目录：`artifacts/runs/evaluation/2026-11-12/e5feb5b32e0742ababdf35d67e4d363e/`（本地，Git 忽略）。`config.json` 里 `method=hybrid`、`rerank=true`、`workers=8`、题集 sha256 `6014f798…`。
  - 对比对象：R4b 的串行运行 `artifacts/runs/evaluation/2026-11-11/3af18ae175bf488cb150592438006648/`。题集 hash、两个库的 Knowledge Scope、Embedding Profile、Rerank Profile 和配置版本（`r4-hybrid-v1+r4b-rerank-v1`）都相同。
- 时长：
  - 从 `eval_started` 到 `eval_finished`：串行 1134.2 秒（18.9 分钟），并发 45.3 秒，相差约 25 倍。
  - 这 25 倍不全是并发的功劳。并发那次各题检索耗时之和是 5.9 分钟，被 8 个线程压到 45 秒，约 7.8 倍；而 5.9 分钟本身只有串行那次的三分之一，是服务这次更快（串行那次有 17 道题超过 20 秒，这次最慢 13.1 秒，没有超过 20 秒的）。
  - 单题耗时 P50 / P95：串行 10.6 / 39.9 秒，并发 3.4 / 10.3 秒。并发的反而更低，说明两次的服务状态不同，这组数字不能比较。
- 指标：
  - 整体和两个库各自的 Table Complete Recall@5 / @10、Table Recall@5 / @10、拿不全的题数都相同。
  - Hits to Cover 平均数 4.98 → 4.94，Column Recall@10 78.2% → 78.4%。
  - 72 道非冻结题里，K=5 和 K=10 的"拿全 / 没拿全"没有一道变化。只统计了数量，没有看具体是哪道题。
  - 88 题里 83 题各相关表的首次命中名次完全相同，其余 5 题是名次接近的 Hit 互换，属于已知的抖动（查询向量和重排分数每次调用略有不同）。
- 多线程共用客户端：88 题、8 个线程共用一个 Embedding 客户端和一个 Reranker 客户端，运行以退出码 0 结束，没有错误。SDK 内部有没有发生过自动重试，报告里看不出来。只有这一次运行作为依据。
- 进度事件：88 条 `eval_case_finished`，`completed_cases` 从 1 递增到 88，`case_index` 不是递增的（按完成顺序写出）。
- 文档：`docs/evaluation/retrieval-evaluation.md` 第 3 节新增"并发运行"小节（结果和耗时的比较规则），第 5 节补上 `--workers` 的用法、字段、事件和出错行为；`docs/roadmap.md` 的"已完成"加了一行。
- 没有做的：
  - 没有重跑串行来做同一时段的对比（按规格）。所以"并发贡献约 8 倍"是从并发那次自己的数据算出来的，不是两次运行相除。
  - 断点续跑仍然没有。并发后一次 hybrid + 重排的评测在 1 分钟以内，这次的数据不支持为它增加逐题落盘；是否要做由用户决定。
