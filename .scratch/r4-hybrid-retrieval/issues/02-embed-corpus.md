# 02: 生成向量（Embedder 与 `knowledge store embed`）

**What to build:** 开发者用一条命令，为指定 Knowledge Scope 的全部 Chunk 生成向量并写进向量表。所有向量在一个事务里一次写入：要么全部写入，要么一条不写；已有就报告"已存在"，不再调用 API。

**Blocked by:** 01、01b

**Status:** ready-for-agent

- [ ] knowledge 模块对外公开 Embedder 接口：`profile_id`、`embed_documents(texts)`、`embed_query(text)`；测试用的假 embedder 从"文本 → 3 维向量"字典取值，profile id 为 `fake:3:test-v1`
- [ ] 真实客户端走 OpenAI 兼容的 `/embeddings`，读取 `QUERYPILOT_EMBEDDING_*` 配置；Chunk 原样用 `content` 嵌入；查询前拼上一句固定的英文任务指令（不参考任何题目）；Profile 为 `siliconflow:Qwen/Qwen3-Embedding-8B:4096:content-v1:query-instruct-v1`
- [ ] 返回的条数或维度不对、请求失败，都报 `embedding_error`；消息不含查询原文，也不含密钥
- [ ] `embed_corpus(scope, settings, embedder)`：已存在就返回"已存在"，不调用 embedder；否则读出全部 Chunk，在内存里算完，一个事务写入；返回写入条数、Profile 和维度，不返回正文和向量
- [ ] 真实 PG 测试：全部写入且维度正确；embedder 中途抛错时一行不写；第二次执行不调用 embedder；Corpus 原有的行不变，`store verify` 仍然通过；reader 写不了向量表
- [ ] CLI `querypilot knowledge store embed`，用 Scope 文件或三元组指定范围；输出不含正文、向量和密钥
- [ ] **征得用户同意后**：真实冒烟一次（嵌入一条查询，检查是 4096 维），再给 toxicology、student_club 两个正式 Corpus 生成向量；向量表行数分别是 20、64；记录命令和配置

## Comments
