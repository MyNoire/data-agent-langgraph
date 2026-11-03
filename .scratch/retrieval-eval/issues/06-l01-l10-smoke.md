# 06: L01–L10 冒烟测试

**What to build:** 在真实的 toxicology Corpus 上，用 `retrieve_knowledge` 对 L01–L10 做集成测试，作为检索行为的回归保护。它不进评测报告。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] 把本地真实的 toxicology Corpus 导入测试库（只限 `querypilot_knowledge_test`）
- [ ] L01–L09 的目标 Chunk 进入指定类型的前 5 名；L10 为正常无匹配
- [ ] 期望值沿用旧规格的样例表；BM25 的实际结果和期望不一致时，先报告给用户，不改期望值
- [ ] 本地没有 Corpus 文件时跳过并写明原因，但不能当作通过

## Comments
