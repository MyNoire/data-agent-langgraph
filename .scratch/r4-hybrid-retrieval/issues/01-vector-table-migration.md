# 01: 向量表与迁移 002

**What to build:** 知识库里有一张独立的向量表，按 (Chunk, Embedding Profile) 存向量，Corpus 本身不变（ADR 0002）。`knowledge_chunks` 上预留的三个向量列和导入时的 `embedding_absent` 检查一起删掉。已有的导入、`store verify`、BM25 检索照常工作。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] 迁移 002 新建向量表：带 Knowledge Scope 定位、profile id、维度和向量；维度和向量实际维数一致、向量非零；同一个 (Chunk, Profile) 只有一行；通过外键引用这份 Corpus 里的 Chunk，向量挂不到别的 Corpus 上
- [ ] import 角色可以读写这张表，reader 角色只能读
- [ ] 删除 `knowledge_chunks` 的三个向量列和对应的 CHECK；导入核对去掉 `embedding_absent`，读取和比对这三列的代码一起删掉
- [ ] `store migrate` / `check` 认识迁移 002；已有的导入、`store verify` 和检索集成测试在测试库上全部通过
- [ ] 正式知识库执行迁移 002 后，两个正式 Corpus 的 `store verify` 仍然通过
- [ ] 知识库文档的存储一节写上向量表和三个角色的权限

## Comments
