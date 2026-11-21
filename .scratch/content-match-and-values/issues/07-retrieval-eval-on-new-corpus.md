# 07: 新 Corpus 上的检索评测

**What to build:** 用户能看到取值样例和数据约定进入检索文本之后，检索召回有没有变化：在新检索题集上跑 hybrid + 重排和 lexical 各一次，和干净索引上的旧结果对比，写进检索评测文档。

**Blocked by:** 06

**Status:** ready-for-agent

- [x] **征得用户同意后**运行（付费）：`eval retrieval` 用新题集，hybrid + 重排和 lexical 各一次，`top_k=20`，线程数与之前的检索运行相同
- [x] Comments 记录运行目录、配置和费用
- [x] 检索评测文档新增一节：各指标与干净索引上的旧结果并列，逐题的"救回 / 丢失"只看 72 道非冻结题，±1–2 题当作噪声
- [x] 注明导入新 Corpus 会让 BM25 统计量变化，旧结果是导入前测的
- [x] 不根据结果调整取值上限、样例个数或检索配置

## Comments

**2026-11-21 完成**（用户同意运行：「推送，现在跑」）

- 运行（`--workers 8`，`top_k=20`，与干净索引上的 2026-11-13 运行相同）：`artifacts/runs/evaluation/2026-11-21/` 下 lexical `e51cf5ef9e3b474e95c7c87ff78c6d0d/`，hybrid + 重排 `1222801265464fe0a8c6acd9e63cf20b/`。费用：hybrid + 重排 88 次 Embedding、至多 88 次 Rerank；lexical 不收费。
- 额外做了一次不收费的对照：当前索引上用旧题集跑 lexical（`6b655a9684cc428e9af3bb1614a1c3a3/`），和干净索引相比 17 题名次不同、所有指标不变，所以 lexical 的提升来自正文，不是 BM25 统计量。
- 结果（整体 Table Complete Recall@5 / @10）：lexical 57 → 63 / 74 → 78 题，提升集中在 student_club（非冻结 @5 救回 7、丢 1；@10 救回 5）；hybrid + 重排 59 → 61 / 79 → 80，噪声内。详见 `docs/evaluation/retrieval-evaluation.md`「取值样例与数据约定进入检索文本」。
- 没有根据结果调整取值上限、样例个数或检索配置。
