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
| 检索评测并发 | `eval retrieval --workers N`：线程池同时检索多道题，线程里只做检索，计分和记录留在主线程，报告顺序不变；出错或中断时不再开始新题。hybrid + 重排的 88 题评测从 18.9 分钟降到 45 秒（8 个线程贡献约 8 倍，其余是服务当时更快），指标不变 | [检索评测](evaluation/retrieval-evaluation.md) |
| 工程基础 | 模块边界测试，运行记录与安全事件 | [模块](architecture/modules.md)、[运行记录](architecture/observability.md) |

## 下一步

| 阶段 | 要回答的问题 | 内容 |
|---|---|---|
| R5 检索接入查询图 | 检索结果能帮模型写对 SQL 吗？ | 按表组织命中的内容，控制上下文预算；加入检索节点和上下文节点；在两个库的 88 题上用 SQL 评测对比"完整 Schema""全量知识""检索上下文"三组 |
| 知识结构评测 | 粒度怎么切最好？ | 比较列级、表级和 small-to-big，在**相同的上下文 token 预算**下评测，同时看检索指标和 SQL 准确率 |
| R6 外键扩展 | JOIN 路径还缺什么？ | 检索后按外键补上桥接表，做成独立开关并做消融 |
| 选库、修复与多轮 | 系统能不能自己选库、自己修错、记住上下文？ | 带拒答的选库；SQL 错误分类与有限次修复；checkpoint 和长期记忆的边界 |
| 正式评测 | 哪些机制真的有效，代价是多少？ | 固定知识快照、模型、提示词和检索配置，跑冻结的 100 题，报告配对差异和成本 |

每一步只加一种机制，并在固定的题集上和上一步的基线对比，单独量出它的贡献。冻结题只用来评测，不用来调参（见 [ADR 0001](adr/0001-frozen-set-for-component-eval.md)）。
