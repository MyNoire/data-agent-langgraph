# R4 向量检索与混合检索

Status: ready-for-agent

## 问题

用户（项目开发者，也是学习者）已经有了 BM25 关键词检索 `retrieve_knowledge`，并在 88 题检索题集上得到了基线：Table Complete Recall@5 为 64.8%，@10 为 84.1%。非冻结题的失败分析里，有两类是关键词检索本身解决不了的：

- **英文词形对不上**：问题里写 `elements`，知识里只有 `element`，BM25 零命中。
- **常用词误配**：句首动词 State 把 `zip_code.state` 顶到第 1 位。

此外，Chunk 正文是中文标签加英文解释，评测题是英文，只靠字面匹配很难跨语言。用户想知道：**加入语义召回，能补上什么？** 现在既没有向量，也没有融合，无法回答这个问题。

## 解决方案

1. 用一个 Embedding Profile（硅基流动的 `Qwen/Qwen3-Embedding-8B`，4096 维）为 Corpus 的每个 Chunk 离线计算一次向量，存进 Corpus 之外的独立表（ADR 0002）。所有向量在一个事务里一次写入：要么全部写入，要么一条也不写。
2. 检索增加 `method`，可选 `lexical` / `vector` / `hybrid`：
   - `vector`：只对查询做一次嵌入，查询前加一句固定的任务指令，在当前 Knowledge Scope 和 Profile 的向量里按余弦距离做精确全表扫描；
   - `hybrid`：BM25 和向量各取候选，用 RRF 融合。
3. 检索评测增加 `--method`，在同一份 88 题、同一份 Corpus 上各跑一次三种方法，与 BM25 基线对比。失败分析只看非冻结题，逐题标出"被向量救回"和"hybrid 反而变差"。

## 用户故事

1. 作为学习者，我想让每个 Chunk 有一个语义向量，这样问题和知识即使用词不同，也能按意思匹配上。
2. 作为学习者，我想让向量存在 Corpus 之外，这样"Corpus 校验后不再改动"这一条仍然成立，导入核对也不受影响。
3. 作为评测开发者，我想让同一份 Corpus 能同时存放多个 Embedding Profile 的向量，这样以后比较模型时不用互相覆盖。
4. 作为开发者，我想用一条命令为指定 Knowledge Scope 生成向量，这样不用手工调用 API、手工写库。
5. 作为开发者，我想让生成向量是原子操作，这样中途网络或 API 出错时，库里不会留下只有一半向量的 Corpus。
6. 作为开发者，我想在这份 Corpus 在当前 Profile 下已有向量时，让命令报告"已存在"并且什么都不做，这样重复执行是安全的，也不会多花钱。
7. 作为开发者，我想让生成向量的命令输出写入的 Chunk 数、Profile 和维度，这样能确认写对了，而且输出里不包含 Chunk 正文和向量本身。
8. 作为开发者，我想让写向量使用 import 角色，读向量使用 reader 角色，并且 reader 只能读向量表，这样延续知识库现有的分角色权限。
9. 作为学习者，我想让 Chunk 原样用 `content` 嵌入，查询前加一句固定的任务指令，这样用法符合 Qwen3-Embedding 的非对称设计，并且两种文本取法都记在 Profile 里。
10. 作为评测开发者，我想让任务指令只按任务的一般描述来写，不参考任何一道题，这样符合 ADR 0001 的判断标准。
11. 作为检索调用方，我想在检索请求里选 `lexical`、`vector` 或 `hybrid`，这样同一个入口就能做三种检索。
12. 作为检索调用方，我想让 `method` 默认仍是 `lexical`，这样现有的调用方和测试不受影响；默认值要不要改，等评测结果出来再决定。
13. 作为检索调用方，我想让向量检索只在当前 Knowledge Scope 和当前 Profile 的向量里查找，这样不会跨库，也不会混入别的模型算出的向量。
14. 作为检索调用方，我想让 hybrid 的结果来自 BM25 和向量两路的 RRF 融合，这样一路漏掉的 Chunk 可以由另一路补上。
15. 作为检索调用方，我想让融合结果在分数相同时按 `chunk_key` 排序，这样同一个请求每次的名次都一样。
16. 作为检索调用方，我想让 hybrid 在 BM25 一个都没命中时仍然返回向量那一路的结果，这样一个全是英文变形词的问题不会落空。
17. 作为检索调用方，我想让 `knowledge_types` 过滤对三种方法同样生效，这样三种方法可以公平比较。
18. 作为检索调用方，我想让结果里写明实际使用的方法和 Embedding Profile，这样拿到结果就知道它是怎么来的。
19. 作为检索调用方，我想让结果说明 `score` 的含义随方法而变（BM25 分、余弦相似度或 RRF 分），这样不会拿不同方法的分数直接相比。
20. 作为检索调用方，我想在 Embedding 调用失败时收到 `embedding_error`，而不是悄悄退回 BM25，这样评测里不会混进一批其实是 BM25 的结果。
21. 作为检索调用方，我想在这份 Corpus 没有当前 Profile 的向量时收到 `embedding_missing`，这样 `.env` 换了模型却没重新生成向量时，能立刻发现。
22. 作为检索调用方，我想让 `lexical` 检索完全不调用 Embedding 服务，这样只用 BM25 时不依赖外部服务，也不花钱。
23. 作为安全负责人，我想让查询原文、Chunk 正文和向量都不写进日志和运行记录，这样延续现有的敏感信息底线。
24. 作为安全负责人，我想让 Embedding 服务的地址、模型和密钥单独配置，密钥不出现在任何输出里，这样它和对话模型的配置互不影响。
25. 作为评测开发者，我想用 `eval retrieval --method` 选择检索方法，这样三种方法用同一个运行器、同一套指标。
26. 作为评测开发者，我想让运行配置和报告记录方法和 Embedding Profile，这样每个数字都能追溯到当时的配置。
27. 作为评测开发者，我想让三种方法使用相同的题集、Corpus、top_k=20、K=5/10 和 Column Recall@10，这样差异只来自检索方法。
28. 作为评测开发者，我想让 RRF 的 k 和权重固定为默认值、从不调整，这样结果不受针对题集调参的影响。
29. 作为学习者，我想看到非冻结题中"BM25 失败、向量救回"的题和"hybrid 反而变差"的题，这样能讲清语义召回的作用和代价。
30. 作为学习者，我想用三方法的对比结果更新面试笔记，这样能用真实、可追溯的数字讲混合检索。
31. 作为开发者，我想在测试里注入一个返回固定向量的假 embedder，这样测试不联网、不花钱，并且结果完全确定。
32. 作为开发者，我想让真实的 Embedding 客户端只在我同意后做一次冒烟调用，这样不会在我不知情时产生费用。

## 实现决定

### 知识存储

- **迁移 002**：
  - 新建向量表，每行对应一个 (Chunk, Embedding Profile)：`chunk_id`、profile id、维度和向量，向量类型为 pgvector 的 `vector`，维度不固定。
  - 约束：维度和向量实际维数一致、向量非零。主键是 (`chunk_id`, profile id)。`chunk_id` 通过外键引用 `knowledge_chunks`。不另存 Scope 字段：`chunk_id` 由 `corpus_id` 和 chunk key 派生，一个 Chunk 只属于一份 Corpus，按 Scope 查询时 JOIN `knowledge_chunks` 即可。
  - `migrate` 改为按顺序补跑缺少的迁移；已执行的迁移版本和摘要必须与文件一致。结构核对按 001、002 依次推导预期结构，权限核对包括新表。
  - import 角色可写，reader 角色只能读。
- **删除** `knowledge_chunks` 上预留的三个向量列和对应的 CHECK 约束。导入核对中的 `embedding_absent` 检查，以及读取、比对这三列的代码一起删掉。打开 Scope 时的检查（计数、引用完整性）不涉及向量，不改。
- **Embedding Profile 的身份**：一个可读的字符串，和每条向量一起存，不单独建 profile 表。真实 Profile 为 `siliconflow:Qwen/Qwen3-Embedding-8B:4096:content-v1:query-instruct-v1`：
  - `content-v1`：Chunk 原样用 `content` 嵌入；
  - `query-instruct-v1`：查询前加一句固定的英文任务指令。

### Embedder

- 一个小接口，knowledge 模块对外公开，retrieval 通过 knowledge 的公开入口使用：
  - `profile_id`：这批向量的身份；
  - `embed_documents(texts)`：批量嵌入 Chunk 文本；
  - `embed_query(text)`：嵌入一条查询，内部拼上任务指令。
- 真实实现走 OpenAI 兼容的 `/embeddings` 接口（硅基流动），配置为 `QUERYPILOT_EMBEDDING_BASE_URL`、`QUERYPILOT_EMBEDDING_MODEL`、`QUERYPILOT_EMBEDDING_API_KEY`，与对话模型分开。维度用模型默认的 4096，不截短。
- 真实客户端检查返回的条数和维度与预期一致，不一致就当作 Embedding 调用失败处理。
- Embedding 调用失败统一抛出 `embedding_error`，错误消息是固定的安全文本，不包含查询原文；底层原因通过异常链保留。

### 生成向量

- knowledge 模块新增公开函数 `embed_corpus(scope, settings, embedder)`，CLI 增加 `querypilot knowledge store embed`，按 Scope 文件或三元组指定 Knowledge Scope。
- 流程：
  1. 用 import 角色检查这份 Corpus 在该 Profile 下是否已有向量，已有就返回"已存在"，不调用 API；
  2. 读出这份 Corpus 的全部 Chunk；
  3. 在内存里算完全部向量；
  4. 在一个事务里一次插入全部向量。
- 任何一步失败都不写入。返回写入数、Profile 和维度，不返回正文和向量。

### 检索

- `RetrievalRequest` 增加 `method`，可选 `lexical` / `vector` / `hybrid`，默认 `lexical`。
- `retrieve_knowledge(request, settings, embedder=None)`：`method` 不是 `lexical` 且 `embedder` 为 None 时，按配置创建真实 embedder；`lexical` 不创建 embedder，也不调用它。
- **vector**：
  1. 在同一个只读 Scope 会话里检查当前 Profile 的向量是否存在，不存在就报 `embedding_missing`；
  2. 嵌入查询；
  3. 在 Scope、Profile 和 `knowledge_types` 范围内按余弦距离做精确全表扫描，不建 ANN 索引。
  - `score` 为余弦相似度；距离相同时按 `chunk_key`、`chunk_id` 排序。
- **hybrid**：BM25 和向量各取 50 个候选（`TopK` 的上限），用 RRF 融合后取前 top_k 个。
  - BM25 查询没有有效词时，hybrid 只用向量那一路。lexical 在这种情况下仍报 `input_error`，行为不变。
- **RRF** 是一个纯函数：输入若干路已排好序的 Chunk 列表，每个 Chunk 的分数为 Σ 1/(60 + rank)，rank 从 1 开始；两路权重相同；分数相同时按 `chunk_key` 排序。k=60 和权重都不调整。
- `RetrievalResult.method` 改为实际使用的方法，并增加 Embedding Profile 字段（lexical 时为空），结果版本号随之升级。Hit 结构不变，不增加"各路名次"字段。
- 单条检索的 CLI 也增加 `--method`。

### 评测

- `evaluate_retrieval` 和 `eval retrieval` 增加 `--method`，默认 `lexical`。方法和 Profile 写进运行配置和报告。指标、K、top_k 都不变；不加 MRR，不用 nDCG。
- 三种方法各跑一次，得到三个运行目录。逐题对比由临时脚本读取三份 `result.json` 完成，结果写进工单，不新增对比命令。

### 分工（AGENTS.md 第 3 节）

- **用户写（结对，乒乓 TDD）**：`retrieve_knowledge` 中按 `method` 分流、组装 hybrid 结果的流程。Claude 先写失败的测试，用户写实现，Claude 审查。
- **Claude 写**：迁移 002 和权限、Embedder 接口和真实客户端、`embed_corpus` 和命令、向量检索 SQL、RRF 纯函数、评测和 CLI 的 `--method`、文档。

## 测试决定

- 好的测试只看对外行为：返回了哪些 Hit、按什么顺序、报什么错误类别、库里有没有写入行。不看内部调用了哪些函数。模型和检索的效果由评测回答，不由测试回答。
- 测试用**假 embedder**：从手写的"文本 → 3 维向量"字典取向量，profile id 为 `fake:3:test-v1`，不联网。另有一个调用即抛异常的版本，用来测失败路径。
- **Seam 1：`retrieve_knowledge`，真实 PG 加原创小 Corpus**。沿用现有检索集成测试的夹具，需要 `--require-postgres`。覆盖：
  - vector 按相似度排序；
  - hybrid 的排序是两路融合的结果；
  - hybrid 在 BM25 无命中、或查询没有有效词时只用向量；
  - `knowledge_types` 过滤；
  - 结果里的 `method` 和 Profile；
  - `embedding_missing`、`embedding_error`；
  - lexical 不调用 embedder，行为与之前一致。
  - 用户写的分流流程在这个 seam 上做乒乓 TDD。
- **Seam 2：`embed_corpus`，真实 PG**。覆盖：
  - 全部 Chunk 都写入，维度和 Profile 正确；
  - embedder 中途抛错时一行都不写；
  - 第二次执行返回"已存在"且不调用 embedder；
  - Corpus 原有的行不变，`verify-import` 仍然通过；
  - reader 不能写向量表。
  - 现有的导入、核对测试中与三个向量列相关的部分随迁移 002 一起调整。
- **Seam 3：RRF 纯函数**，单元测试，不需要 PG。覆盖：
  - 两路都出现的 Chunk 分数相加；
  - 只出现在一路的 Chunk 也会出现在结果里；
  - 分数相同时按 `chunk_key` 排序；
  - 某一路为空。
- **Seam 4：评测运行器**，沿用现有的假检索器测试。覆盖：`method` 传进每个检索请求，并写进运行配置和报告。
- 真实的硅基流动客户端不写单元测试；征得用户同意后做一次真实冒烟（嵌入一条查询，检查维度）。模块边界测试继续守住 retrieval 只依赖 knowledge 和 runtime。

## 不在范围内

- Reranker：紧接在本次评测之后做（R4b）。
- 英文词干化或词形归一、值层面的知识、外键扩展、限制每张表的 Chunk 数、切块粒度和 small-to-big。
- ANN 索引（HNSW 或 IVFFlat）、`halfvec`、截短维度。
- 调整 RRF 的 k 和权重，比较不同的查询指令写法，比较不同的 Embedding 模型。
- MRR 和 nDCG；新增对比命令。
- 修改 `method` 的默认值，以及把检索接入查询图（R5）。

## 补充说明

- 真实调用 Embedding API 会产生费用。生成向量（84 个 Chunk）和每次评测（每种方法 88 条查询）都要用户明确同意后才运行，运行时记录配置和产物位置。
- 冻结题和开发题一起计分，但失败分析只看非冻结题（ADR 0001）。任务指令和 RRF 参数都不参考任何题目确定。
- pgvector 的 `vector` 类型建 HNSW 索引时最多支持 2000 维。本次做精确扫描，4096 维没有问题；以后要建索引时，再考虑 `halfvec` 或截短维度。
- GitHub 流程：每完成一张工单，经用户确认后推送 `r4-hybrid-retrieval`。第一张工单完成后开 Draft PR，正文用 `/pr` 写。
