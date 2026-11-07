# 03: 向量检索

**What to build:** 检索调用方用 `method=vector` 在一个 Knowledge Scope 里做语义检索：对查询嵌入一次，在当前 Embedding Profile 的向量里按余弦距离做精确全表扫描。结果写明方法和 Profile。`lexical` 的行为不变，也不调用 embedder。

**Blocked by:** 02

**Status:** ready-for-human

结对分工：按 `method` 分流的流程由**用户写**（乒乓 TDD，Claude 先写失败的测试，审查实现）；向量检索 SQL、请求和结果模型的改动由 Claude 写。

- [ ] `RetrievalRequest.method`：`lexical` / `vector` / `hybrid`，默认 `lexical`
- [ ] `retrieve_knowledge(request, settings, embedder=None)`：非 lexical 且没传 embedder 时按配置创建；lexical 不创建，也不调用
- [ ] vector：在同一个只读 Scope 会话里先检查当前 Profile 的向量是否存在，不存在报 `embedding_missing`；再嵌入查询，按 Scope、Profile 和 `knowledge_types` 过滤，按余弦距离排序；`score` 为余弦相似度；相同时按 `chunk_key`、`chunk_id` 排序
- [ ] embedder 抛错时报 `embedding_error`，不退回 BM25
- [ ] `RetrievalResult.method` 是实际用的方法，增加 Embedding Profile 字段（lexical 时为空），结果版本号升级
- [ ] 真实 PG 测试（假 embedder）覆盖：相似度排序、`knowledge_types` 过滤、`embedding_missing`、`embedding_error`、lexical 不调用 embedder，原有 lexical 测试全部通过
- [ ] 单条检索的 CLI 增加 `--method`
- [ ] 征得用户同意后，在正式 Corpus 上用 `--method vector` 跑几条非冻结题，看看效果

## Comments
