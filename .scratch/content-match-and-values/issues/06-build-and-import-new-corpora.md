# 06: 构建并导入两份新 Corpus

**What to build:** toxicology 和 student_club 各有一份带取值样例和数据约定的新 Corpus，导入现有知识库的现有表，与旧 Corpus 并存（靠 Knowledge Scope 区分，旧行不动），并有当前 Embedding Profile 的向量。用新 Corpus 生成一份新的检索题集，题号和 Relevant Set 不变，只有 Scope 不同。这是操作性工单，不写新功能代码。

**Blocked by:** 03, 04

**Status:** ready-for-agent

- [ ] 两个库各生成取值样例文件，构建新 Corpus 并通过 `corpus verify`；`corpus_id` 与旧 Corpus 不同
- [ ] 抽查几个列 Chunk 和表 Chunk 的正文：取值、数据约定和隐私列的处理符合预期（Comments 只记结论，不贴隐私值）
- [ ] **征得用户同意后**导入正式知识库并 `store verify` 读回核对；旧 Corpus 的行数和内容不变
- [ ] 核对 BM25 索引段的 `num_docs` 之和等于表的行数（见 pg_search 维护文档）
- [ ] **征得用户同意后**计算向量（付费），Comments 记录调用量
- [ ] 用 `eval retrieval-cases --corpus-dir`（指向两份新 Corpus）生成新的检索题集，留在本地，manifest 提交；旧题集不动
- [ ] Comments 记录两份新 Corpus 的 `corpus_id`、Scope、产物位置和使用的命令
- [ ] 知识库文档的快照表补上新 Corpus 的 Chunk 数
