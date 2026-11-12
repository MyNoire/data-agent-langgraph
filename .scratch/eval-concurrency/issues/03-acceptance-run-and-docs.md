# 03: 验收运行与文档

**What to build:** 学习者拿到一组可追溯的数字：同样的 88 题、同样的 hybrid + 重排，并发之后一次评测用多久，召回指标是否和串行时一致。检索评测文档写明 `--workers` 怎么用、耗时该怎么比较。

**Blocked by:** 01, 02

**Status:** ready-for-agent

规格：`.scratch/eval-concurrency/spec.md`。由 Claude 完成。真实运行收费（约 26 万 Token），**要用户明确同意后才跑**。

- [ ] 运行前确认：知识库在运行，`QUERYPILOT_EMBEDDING_*` 和 `QUERYPILOT_RERANK_*` 已配置，用户同意
- [ ] 跑一次 `eval retrieval --method hybrid --rerank --workers 8`，题集和 Corpus 与 R4b 的 hybrid + 重排运行相同；记录命令、运行目录和 `config.json`
- [ ] 总时长与串行那次的 18.9 分钟对比，写明服务本身有波动，一次运行只能说明量级
- [ ] 召回指标与 R4b 的 hybrid + 重排运行对比，±1–2 道题属于噪声；差异超出时先查原因，不直接下结论
- [ ] 运行中没有出现多线程共用客户端导致的错误；出现了就如实记录并停下来和用户商量
- [ ] 不为了对比重跑串行；不看任何题的结果来改检索（ADR 0001）
- [ ] 检索评测文档补上：`--workers` 的用法；报告和运行配置里的 `workers`；耗时 P50 / P95 只在 `workers=1` 的运行之间比较；进度事件按完成顺序写、`completed_cases` 的含义；验收运行的结果
- [ ] `docs/roadmap.md` 记下这项已完成

## Comments
