# 04: 两组重排评测运行与分析

**What to build:** 在同一份 88 题、同一份 Corpus 上真实跑两组带重排的检索评测，回答两个问题：精排能不能把已经找到的相关表提到前面；有了重排，向量这一路还值不值得保留。结果和 2026-11-10 的 hybrid 基线对比，写进检索评测文档。

**Blocked by:** 03

**Status:** ready-for-agent

规格：`.scratch/r4b-reranker/spec.md`。两组运行都会调用收费服务，**要用户明确同意后才运行**；运行时记录配置和产物位置。

- [ ] 运行前确认：知识库容器在运行，两份 Corpus 的向量存在，题集 hash 与基线一致
- [ ] 运行 `--method hybrid --rerank`（主实验）和 `--method lexical --rerank`（消融），各一次；hybrid 不重跑，基线用 2026-11-10 的运行
- [ ] 整体和分库报告：Table Complete Recall@5 / @10、Table Recall@5 / @10、Hits to Cover、20 个 Hit 内拿不全的题数、Column Recall@10、检索耗时 P50 / P95
- [ ] 逐题对比只看 72 道非冻结题：相对 hybrid"救回"和"弄丢"的题数与例子，由临时脚本读取 `result.json` 得到；不据此修改知识、参数或代码（ADR 0001）
- [ ] 结论的说法：写明 ±1–2 道题属于噪声范围；写明 toxicology 只有 20 个 Chunk，hybrid + 重排在这个库上等于重排整个 Corpus；写明两次运行各自嵌入查询，候选可能略有出入
- [ ] 回答消融问题：lexical + 重排与 hybrid + 重排的差距，以及各自的延迟
- [ ] 检索评测文档增加重排的结果和分析；路线图把 R4b 移到"已完成"；报告和文档里不出现冻结题的问题原文
- [ ] 把运行目录、配置和实际命令记在本工单的 Comments 里；`rerank` 和 `method` 的默认值是否修改，交给用户决定，不在本工单里改

## Comments
