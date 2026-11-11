# 04: 两组重排评测运行与分析

**What to build:** 在同一份 88 题、同一份 Corpus 上真实跑两组带重排的检索评测，回答两个问题：精排能不能把已经找到的相关表提到前面；有了重排，向量这一路还值不值得保留。结果和 2026-11-10 的 hybrid 基线对比，写进检索评测文档。

**Blocked by:** 03

**Status:** ready-for-agent

规格：`.scratch/r4b-reranker/spec.md`。两组运行都会调用收费服务，**要用户明确同意后才运行**；运行时记录配置和产物位置。

- [x] 运行前确认：知识库容器在运行，两份 Corpus 的向量存在，题集 hash 与基线一致
- [x] 运行 `--method hybrid --rerank`（主实验）和 `--method lexical --rerank`（消融），各一次；hybrid 不重跑，基线用 2026-11-10 的运行
- [x] 整体和分库报告：Table Complete Recall@5 / @10、Table Recall@5 / @10、Hits to Cover、20 个 Hit 内拿不全的题数、Column Recall@10、检索耗时 P50 / P95
- [x] 逐题对比只看 72 道非冻结题：相对 hybrid"救回"和"弄丢"的题数与例子，由临时脚本读取 `result.json` 得到；不据此修改知识、参数或代码（ADR 0001）
- [x] 结论的说法：写明 ±1–2 道题属于噪声范围；写明 toxicology 只有 20 个 Chunk，hybrid + 重排在这个库上等于重排整个 Corpus；写明两次运行各自嵌入查询，候选可能略有出入
- [x] 回答消融问题：lexical + 重排与 hybrid + 重排的差距，以及各自的延迟
- [x] 检索评测文档增加重排的结果和分析；路线图把 R4b 移到"已完成"；报告和文档里不出现冻结题的问题原文
- [x] 把运行目录、配置和实际命令记在本工单的 Comments 里；`rerank` 和 `method` 的默认值是否修改，交给用户决定，不在本工单里改

## Comments

- 运行前检查（2026-11-12）：知识库容器在运行；题集 sha256 `6014f798…` 与 2026-11-10 的 hybrid 基线一致；toxicology 和 student_club 在当前 Embedding Profile 下都有向量；`QUERYPILOT_RERANK_*` 三项都能读到。
- 两组运行（用户同意后执行，各一次，都完整跑完 88 题，没有 `input_error`，没有中途失败）：
  - `python -m querypilot eval retrieval --cases evals/retrieval/retrieval_cases.json --method hybrid --rerank`
    - 运行目录 `artifacts/runs/evaluation/2026-11-11/3af18ae175bf488cb150592438006648/`（目录按 UTC 日期命名），UTC 16:35–16:54，用时 18.9 分钟。
    - 配置版本 `r4-hybrid-v1+r4b-rerank-v1`。
  - `python -m querypilot eval retrieval --cases evals/retrieval/retrieval_cases.json --method lexical --rerank`
    - 运行目录 `artifacts/runs/evaluation/2026-11-11/913415bbac814780b01e4662753461f4/`，UTC 16:54–16:59，用时 4.9 分钟。
    - 配置版本 `r3-bm25-v1+r4b-rerank-v1`。
  - 两组的 Rerank Profile 都是 `siliconflow:Qwen/Qwen3-Reranker-8B:content-v1:task-instruct-v1`；hybrid 的 Embedding Profile 与基线相同。
  - 合计 176 次 Reranker 调用（不含 SDK 的自动重试，重试次数没有记录）和 88 次 Embedding 调用。这是 `SiliconFlowReranker` 第一次对真实服务调用。
- 对比的基线：hybrid 用 2026-11-10 的 `fe098b13…`，没有重跑；另外加了 2026-11-06 的 lexical 基线 `3c83f0af…`。规格只要求和 hybrid 比，加 lexical 是因为 BM25 的候选是确定的，"lexical 对 lexical + 重排"是最干净的重排前后对比。四份报告的题集 hash 和 Scope 都相同。
- 结果（整体 88 题，完整表格、分库数字和分析在 `docs/evaluation/retrieval-evaluation.md` 第 3、4 节）：

  | | lexical | hybrid | lexical + 重排 | hybrid + 重排 |
  |---|---|---|---|---|
  | Table Complete Recall@5 | 64.8% | 68.2% | 75.0% | 67.0% |
  | Table Complete Recall@10 | 84.1% | 85.2% | 85.2% | 89.8% |
  | 20 个 Hit 内拿不全的题 | 3 | 2 | 3 | 2 |
  | Column Recall@10 | 67.5% | 73.6% | 74.4% | 78.2% |
  | 检索耗时 P50 / P95 | 0.28 / 0.33 s | 0.49 / 2.4 s | 0.97 / 15.5 s | 10.6 / 39.9 s |

- 非冻结题（72 道）逐题对比，K=5：lexical → lexical + 重排救回 12、弄丢 4；hybrid → hybrid + 重排救回 8、弄丢 11。K=10：两组都是救回 3、弄丢 1。±1–2 道题属于噪声范围。没有据此修改知识、参数或代码。
- 两个问题的回答：
  - 精排能不能把已经找到的相关表提到前面：能，但取决于候选。BM25 候选上 @5 多 9 道题；hybrid 候选上 @5 没有变化，@10 多 4 道题。
  - 有了重排还要不要向量：没有单一的答案。前 5 是 lexical + 重排更好（差 7 道题），前 10 和"能不能找全"是 hybrid + 重排更好。
- 延迟：两组的耗时差了 10 倍，但 50 道两组都是 20 个候选的题上也是 1.0 秒对 11.3 秒，所以主要是服务在两个时段的波动，不能说明 lexical + 重排更快。
- 分析用的临时脚本没有进仓库。除了读四份 `result.json`，还在本地用 lexical 检索数了一遍 88 道题的 BM25 候选数（只用到条数和总字符数，不调用收费服务），用来判断延迟差距是不是候选数造成的：toxicology 有 35 道题不到 20 个，student_club 有 45 道题是 20 个。
- 没有验证的：
  - "Reranker 给每个候选单独打分，两组的差别只来自候选"是按模型的工作方式推断的。
  - "被挤出前 5 是因为同一张表的多个列块排到了前面"是推测。报告不保存 Hit 列表，这两点都没有逐个 Hit 核对；要核对需要再调用 Reranker。
- 留给用户决定，本工单没有改：`rerank` 和 `method` 的默认值（现在是关闭和 `lexical`）。
