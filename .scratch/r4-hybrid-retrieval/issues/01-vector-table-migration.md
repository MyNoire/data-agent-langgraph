# 01: 向量表与迁移 002

**What to build:** 知识库里有一张独立的向量表，按 (Chunk, Embedding Profile) 存向量，Corpus 本身不变（ADR 0002）。`knowledge_chunks` 上预留的三个向量列和导入时的 `embedding_absent` 检查一起删掉。已有的导入、`store verify`、BM25 检索照常工作。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] 迁移 002 新建向量表：`chunk_id`（外键引用 Chunk）、profile id、维度和向量；主键 (`chunk_id`, profile id)；维度和向量实际维数一致、向量非零。不另存 Scope 字段
- [ ] `migrate` 按顺序补跑缺少的迁移，已执行的迁移版本和摘要必须与文件一致；`check` 要求迁移为 [001, 002]，预期结构由两个迁移依次推导
- [ ] import 角色可以读写这张表，reader 角色只能读
- [ ] 删除 `knowledge_chunks` 的三个向量列和对应的 CHECK；导入核对去掉 `embedding_absent`，读取和比对这三列的代码一起删掉
- [ ] `store migrate` / `check` 认识迁移 002；已有的导入、`store verify` 和检索集成测试在测试库上全部通过
- [ ] ~~正式知识库执行迁移 002 后，两个正式 Corpus 的 `store verify` 仍然通过~~（移到工单 01b）
- [ ] 知识库文档的存储一节写上向量表和三个角色的权限

## Comments

- 代码已提交（ed39e96、a12f219）。审查发现结构 AST 比对过重、阻碍后续迁移，精简与正式库迁移拆到工单 01b。
