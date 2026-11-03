# 06: L01–L10 冒烟测试

**What to build:** 对正式知识库里已有的 toxicology 知识，用 `retrieve_knowledge` 只读地跑 L01–L10，作为检索行为的回归保护。不导入 Corpus，也不进评测报告。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] 用正式知识库的只读 DSN 连接，不写入、不清理数据；Knowledge Scope 取自本地 toxicology Corpus 的 manifest
- [ ] L01–L09 的目标 Chunk 进入指定类型的前 5 名；L10 为正常无匹配
- [ ] 期望值沿用旧规格的样例表；BM25 的实际结果和期望不一致时，先报告给用户，不改期望值
- [ ] 和其他实库测试一样需要显式开启；缺少 DSN、知识库不可用或 Scope 不匹配时明确报错或跳过，不能当作通过

## Comments

- 2026-11-03：用户决定不在测试库重复导入 Corpus。导入的正确性已由导入后的回读核对覆盖，冒烟测试直接只读访问正式知识库。
