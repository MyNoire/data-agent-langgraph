# 04: 混合检索

**What to build:** 检索调用方用 `method=hybrid`，得到 BM25 和向量两路用 RRF 融合后的结果。一路漏掉的 Chunk 可以由另一路补上；BM25 无命中时，只用向量那一路；查询没有有效词时与 lexical 一样报 `input_error`。

**Blocked by:** 03

**Status:** ready-for-human

结对分工：RRF 纯函数由 Claude 用 TDD 写；hybrid 的组装流程由**用户写**（乒乓 TDD，Claude 先写失败的测试，审查实现）。

- [ ] RRF 纯函数：输入若干路已排序的 Chunk 列表，分数为 Σ 1/(60 + rank)，rank 从 1 开始，两路权重相同，分数相同时按 `chunk_key` 排序；k 和权重固定不调
- [ ] RRF 单元测试：两路都出现时分数相加；只在一路出现的 Chunk 也在结果里；同分时的排序；某一路为空
- [ ] hybrid：两路各取 50 个候选，融合后取前 top_k 个；`score` 为 RRF 分
- [ ] 查询没有有效词时 hybrid 与 lexical 一样报 `input_error`，不调用 Embedding 服务
- [ ] 真实 PG 测试（假 embedder）覆盖：融合后的排序、BM25 无命中时的结果、没有有效词时报 `input_error`、`knowledge_types` 过滤、`embedding_missing`、`embedding_error`

## Comments
