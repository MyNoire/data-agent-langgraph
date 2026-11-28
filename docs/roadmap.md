# 路线图

## 目标

一个面向业务人员的多轮自然语言数据助手：在授权范围内确定数据源，检索 Schema 和业务知识，安全地生成 SQL 并只读执行；遇到可恢复的错误时有限次修复；最后根据实际执行结果给出可以追溯依据的回答。数据源不明确、或者操作不安全时，要澄清或拒绝。

目标中的请求链路：

```text
请求 + 运行时权限
  → 选库（排序、确认或拒答）
  → 上下文构建（本轮问题 + 相关的会话状态）
  → Schema / 知识检索子图（词法 + 向量 + 重排 + 外键扩展）
  → 分析计划（可选人工审核）
  → SQL 子图（生成 → AST 检查 → 只读执行 → 错误分类 → 有限次修复）
  → 需要计算时进入分析子图
  → 带查询和结果出处的回答
```

## 已完成

| 阶段 | 内容 | 文档 |
|---|---|---|
| 查询基线 | LangGraph 四节点查询图：读结构 → 生成 SQL → AST 安全检查 → 只读执行；SQL 评测（12 题开发集 + 冻结 100 题） | [SQL 评测](evaluation/sql-evaluation.md)、[SQL 安全](architecture/security.md) |
| R1 知识准备 | 提取结构，绑定字段说明，修正必须经批准，中文语义，确定性的不可变快照 | [知识库](knowledge/knowledge-base.md) |
| R2 知识存储 | PostgreSQL 分角色迁移、导入、读回核对，限定 Scope 的只读会话 | [知识库](knowledge/knowledge-base.md) |
| R3 词法检索 | pg_search BM25，`retrieve_knowledge` | [知识库](knowledge/knowledge-base.md) |
| 检索评测 | 88 题检索题集、按 Hit 计的召回指标、BM25 基线和失败分析 | [检索评测](evaluation/retrieval-evaluation.md) |
| R4 向量与混合检索 | Embedding 向量存在 Corpus 之外（[ADR 0002](adr/0002-embeddings-outside-corpus.md)），精确向量检索，RRF 融合；三种方法对比：hybrid 的 Complete Recall@5 从 64.8% 升到 68.2%，vector 单独用略低于 BM25 | [检索评测](evaluation/retrieval-evaluation.md)、[知识库](knowledge/knowledge-base.md) |
| R4b 重排 | 检索之后可选的一步重排（硅基流动 `Qwen/Qwen3-Reranker-8B`，前 20 个候选，默认关闭）。BM25 + 重排的 Complete Recall@5 为 75.0%（BM25 为 64.8%），是前 5 最好的配置；hybrid + 重排的 @5 没有变化（67.0%），@10 从 85.2% 升到 89.8%。代价是延迟：单次检索的中位数在 1–11 秒之间波动 | [检索评测](evaluation/retrieval-evaluation.md)、[知识库](knowledge/knowledge-base.md) |
| R5 检索接入查询图 | 查询图按编译时选的 Context Source 走一条路径（无条件边）：`schema`、`ddl`、`knowledge`（全部知识）、`retrieval`（用问题检索一次，默认 hybrid + 重排、`top_k=10`，只给命中的表）；Hit 按表组织并精简成 Schema（`r5-schema-v1`）；Knowledge Scope 经运行时 context 传入，不进图状态。两库 88 题六组对比：执行匹配 42–46/88，非冻结题 35–37/72，全部在噪声内；检索与全量知识持平而输入只有约 37%；表没拿全的 9 题只对 1 题 | [SQL 评测](evaluation/sql-evaluation.md) |
| 检索评测并发 | `eval retrieval --workers N`：线程池同时检索多道题，线程里只做检索，计分和记录留在主线程，报告顺序不变；出错或中断时不再开始新题。hybrid + 重排的 88 题评测从 18.9 分钟降到 45 秒（8 个线程贡献约 8 倍，其余是服务当时更快），指标不变 | [检索评测](evaluation/retrieval-evaluation.md) |
| 内容匹配与取值样例 | 新增内容匹配（形式可以不同、内容必须相同，[ADR 0003](adr/0003-content-match-as-primary-sql-metric.md)），与严格执行匹配并列；R5 八组离线重算，内容口径 57–63/88、非冻结 48–53/72，组间结论不变。列 Chunk 加取值样例（隐私列不取值）、8 张表加用户批准的数据约定，构建新 Corpus：BM25 前 5 拿全 64.8% → 71.6%；SQL ③⑤ 内容 64 / 63（非冻结各 +1，噪声内），R5 失败分析点名的题救回 3 道，渲染版本 `r5-schema-v3` | [SQL 评测](evaluation/sql-evaluation.md)、[检索评测](evaluation/retrieval-evaluation.md)、[知识库](knowledge/knowledge-base.md) |
| 知识结构评测 | 列级检索加 Small-to-Big 开关（`eval graph --small-to-big`，命中的表展开成完整 Document）。非冻结内容匹配：列级 54、small-to-big 53、全量知识 54/72，打平；平均输入 token 分别为 1139、1916、3493。按事先定的规则，选 token 最少的列级检索：**定下的知识结构是列级 Chunk + hybrid + 重排、`top_k=10`、不展开**。表级检索推迟到加入大库之后 | [SQL 评测](evaluation/sql-evaluation.md) |
| 工程基础 | 模块边界测试，运行记录与安全事件 | [模块](architecture/modules.md)、[运行记录](architecture/observability.md) |

## 下一步

| 阶段 | 要回答的问题 | 内容 |
|---|---|---|
| R6 外键扩展 | JOIN 路径还缺什么？ | 检索后按外键补上桥接表，做成独立开关并做消融 |
| 模型自主检索 | 让模型自己决定查什么、查几次，比固定查一次更好吗？ | 把检索做成 tool，模型自己写查询、可以多次检索；和固定流程在同一批题上对比准确率、调用次数和耗时。之后可选：把检索包成 MCP server 供其他客户端调用 |
| 选库、修复与多轮 | 系统能不能自己选库、自己修错、记住上下文？ | 带拒答的选库；SQL 错误分类与有限次修复；checkpoint 和长期记忆的边界 |
| 正式评测 | 哪些机制真的有效，代价是多少？ | 固定知识快照、模型、提示词和检索配置，跑冻结的 100 题，报告配对差异和成本 |

未来工作（目前不做）：辅助开发人员按现有数据库建知识库的 Agent。它属于管理面，只起草修正、语义和数据约定，批准由人完成（见 [ADR 0004](adr/0004-admin-and-serving-separation.md)）。

每一步只加一种机制，并在固定的题集上和上一步的基线对比，单独量出它的贡献。冻结题只用来评测，不用来调参（见 [ADR 0001](adr/0001-frozen-set-for-component-eval.md)）。
