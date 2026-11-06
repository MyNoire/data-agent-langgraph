# 06: L01–L10 冒烟测试

**What to build:** 对正式知识库里已有的 toxicology 知识，用 `retrieve_knowledge` 只读地跑 L01–L10，作为检索行为的回归保护。不导入 Corpus，也不进评测报告。

**Blocked by:** None (can start immediately)

**Status:** done

- [x] 用正式知识库的只读 DSN 连接，不写入、不清理数据；Knowledge Scope 取自本地 toxicology Corpus 的 manifest
- [x] L01–L09 的目标 Chunk 进入指定类型的前 5 名；L10 为正常无匹配
- [x] 期望值沿用旧规格的样例表；BM25 的实际结果和期望不一致时，先报告给用户，不改期望值
- [x] 和其他实库测试一样需要显式开启；缺少 DSN、知识库不可用或 Scope 不匹配时明确报错或跳过，不能当作通过

## Comments

- 2026-11-03：用户决定不在测试库重复导入 Corpus。导入的正确性已由导入后的回读核对覆盖，冒烟测试直接只读访问正式知识库。
- 完成（2026-11-06）：`tests/integration/test_toxicology_smoke.py`，复用 `live_bm25_settings` 门禁（只认 `querypilot_knowledge` 和 pg_search 0.26.4；不可用时跳过，加 `--require-postgres` 时失败），检索走只读账号。本地没有 toxicology Corpus 时同样跳过或失败；Scope 在库里不存在时 `retrieve_knowledge` 报错，不会算通过。L10 的词已确认不在 Corpus 里。10 个用例全部符合旧规格的期望值。
- 已知局限（报告给用户，期望值未改）：toxicology 只有 4 个表 Chunk、5 个关系 Chunk，所以 L01、L08（限 table）和 L09（限 relationship）的"前 5 名"只要能匹配上就一定满足，验证不了排序；实测这三条的目标都排第 1。

