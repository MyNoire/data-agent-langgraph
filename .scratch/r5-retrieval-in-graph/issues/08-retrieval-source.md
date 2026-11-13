# 08: `retrieval` 来源进图

**What to build:** 图的调用方选 `retrieval` 作为 Context Source 时，图用问题原文检索一次，把命中的知识按表组织后交给模型：模型只看到被命中的表。检索默认用 hybrid + 重排、`top_k=10`。检索失败时这次请求直接失败。评测开发者用 `eval graph --context-source retrieval` 跑检索组。

**Blocked by:** 06

**Status:** ready-for-human

结对分工：`retrieve` 节点和它在图里的连接由**用户写**（乒乓 TDD）。表 Chunk 的读取、评测参数和报告由 Claude 写。

- [ ] 知识读取：按表名取一个 Knowledge Scope 下的表 Chunk，有数量上限，由 retrieval 模块对外提供
- [ ] `retrieve` 节点：按问题原文、当前库的 Knowledge Scope 和编译时的检索配置检索一次，再读出这些 Hit 所属表的表 Chunk，交给 `build_context`
- [ ] 检索配置是编译时参数：`method` 默认 `hybrid`，`rerank` 默认开启，`top_k` 固定为 10；`method` 和 `rerank` 可以在编译时改
- [ ] 给模型的 Schema 只包含被命中的表
- [ ] 五种情况都让请求失败，不调用模型，不退回 `schema` 路径：没有命中、查询没有有效词、Embedding 失败、重排失败、对照表里没有当前库
- [ ] 五种失败的错误类别能分清
- [ ] AST 检查仍允许这个库的全部表：模型用了没被检索到的表不会被拒
- [ ] 检索器可以在测试里换成假的
- [ ] `eval graph --context-source retrieval --method … --rerank / --no-rerank`，默认值与查询图一致；一次运行里所有题共用一个 Embedder 和一个 Reranker
- [ ] 运行配置和报告记录检索方法、Embedding Profile、Rerank Profile、检索配置版本和 `top_k`
- [ ] 评测里检索失败的题记为 error，带错误类别，继续下一题
- [ ] 日志和运行记录不写 Hit 正文
- [ ] 图的测试用假 SQL 生成器和假检索器，覆盖：假生成器收到的 Schema、Scope 和检索配置传到了检索请求、默认配置与编译时覆盖、五种失败且假生成器没被调用、AST 检查的范围
- [ ] 表 Chunk 读取的测试用真实 PG 加原创小 Corpus
- [ ] 在真实知识库上用 lexical 和假 SQL 生成器跑通几道自编的问题（不产生费用），结果记在 Comments
