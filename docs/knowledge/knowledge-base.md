# 知识库：从数据库结构到可检索的知识

模型要写出正确的 SQL，得先知道库里有哪些表和列、它们各是什么意思、表之间怎么连接。知识库把这些信息整理成带中文说明的**知识块（Chunk）**，存进 PostgreSQL，供检索使用。

## 1. 总览

```text
SQLite 库 ──extract──▶ Catalog（表、列、主外键）
                          │
BIRD 字段说明 CSV ──enrich──▶ 增强目录（每列绑定说明和来源行号）
                          │
已批准的修正计划 ──curate──▶ 修正后的增强目录 + 派生 CSV
                          │
Data Semantics 配置 ──corpus build──▶ Corpus 快照（Documents / Chunks / Relations + manifest）
                          │
                 corpus verify ──▶ store import ──▶ store verify ──▶ BM25 检索
                                                      └──▶ store embed（向量表）
```

| 步骤 | 命令 | 做什么 |
|---|---|---|
| 提取结构 | `querypilot knowledge catalog extract` | 只读打开 SQLite，提取表、列、主键、外键 |
| 绑定说明 | `querypilot knowledge catalog enrich` | 把 BIRD 的 `database_description/*.csv` 绑到对应的列，记录来源文件的 hash 和行号 |
| 应用修正 | `querypilot knowledge catalog curate` | 按**已批准**的计划修正说明里的错误，写出派生 CSV |
| 构建快照 | `querypilot knowledge corpus build` | 加上中文语义，渲染成知识块，生成确定性的身份和 manifest |
| 校验快照 | `querypilot knowledge corpus verify` | 重新读取每个文件，核对 hash 和引用关系 |
| 导入 | `querypilot knowledge store import` / `verify` | 写进 PostgreSQL，并把每个字段读回来核对 |
| 生成向量 | `querypilot knowledge store embed` | 按 `--corpus-id/--database-id/--schema-version` 或 `--scope-file` 指定 Scope，用当前 Embedding Profile 为全部 Chunk 算向量，一个事务写进向量表；已有就报 `exists`，不调用 API。会产生费用 |

目前有两个库的正式快照：

| 库 | Documents | Chunks（表 / 列 / 关系） | Relations |
|---|---|---|---|
| toxicology | 4 | 20（4 / 11 / 5） | 5 |
| student_club | 8 | 64（8 / 48 / 8） | 8 |

## 2. 知识从哪里来，什么不能进

**允许的来源**：

- SQLite 的结构（只读提取）；
- BIRD 自带的字段说明 CSV；
- 经过批准的说明修正；
- **Data Semantics**：每张表的中文用途、每条记录代表什么、每个外键的业务含义。由 Agent 只根据表结构和字段说明来写，标记为 `inferred`（推断），再由用户审阅后才使用。

**不能进入知识库**：评测题目、金标准 SQL、BIRD 的 evidence、模型预测结果，以及 SQL 示例。快照的构建配置里把这些开关都写死为 `false`，并且记进 manifest：

```json
{"allow_evaluation_questions": false, "allow_gold_sql": false, "allow_oracle_evidence": false,
 "allow_predictions": false, "allow_sql_examples": false}
```

否则用 BIRD 评测时，等于把答案放进了知识库。

## 3. 三类知识块

每张表一个 **Document**，表、列、关系三类 Chunk 都挂在它所属表的 Document 下（关系挂在外键的源表下）。以下是 student_club 里的真实内容：

**表块**：这张表是干什么的、一条记录代表什么、能连到哪些表。

```text
数据库: student_club; 表: "main"."member"
表用途: 记录社团会员的姓名、在社团中的职位、联系方式和 T 恤尺码，是按会员筛选、统计并关联专业、家乡和活动的入口。
记录粒度: 一名社团会员。
字段目录: "member_id", "first_name", "last_name", "email", "position", "t_shirt_size", "phone", "zip", "link_to_major"
行标识线索（主键声明、成员顺序）: "member_id"
出向关联（声明外键）: "zip" → "main"."zip_code" ("zip_code"); "link_to_major" → "main"."major" ("major_id")
```

**列块**：一列的类型、约束、含义和取值说明。

```text
数据库: student_club; 字段: "main"."member"."position"
声明类型: TEXT
not_null 声明标志: False; 主键位置: 0; hidden: 0
默认 SQL 表达式（仅文本）: 未声明
展示名: 来源未说明
字段解释: The position the member holds in the club
取值解释: 来源未说明
```

注意最后一行"取值解释：来源未说明"：知识库不知道 `position` 里有 President、Vice President 这些值。所以用户问"副会长"时，检索很难找到这张表。这是[检索评测](../evaluation/retrieval-evaluation.md)里发现的一类典型失败。

**关系块**：一条外键的方向、字段对、业务含义和候选 JOIN 条件。

```text
数据库: student_club; 声明外键 "main"."attendance" / 0
引用方向: 源表 "main"."attendance" → 目标表 "main"."member"。
同一条外键的有序字段对: "main"."attendance"."link_to_member" → "main"."member"."member_id"
关联语义: 出席记录对应的会员；可查询会员参加过哪些活动，或按会员属性分析出席情况。
候选 JOIN（source 为源表别名，target 为目标表别名；仅展示 ON 条件）: "source"."link_to_member" = "target"."member_id"
ON UPDATE NO ACTION; ON DELETE NO ACTION; MATCH NONE
```

同一对表之间有多条外键时（比如 toxicology 的 `connected` 用 `atom_id` 和 `atom_id2` 两次指向 `atom`），关系块会写明每条外键各自的角色。复合外键的多个字段对合成一个条件，不拆开。外键还会结构化地存一份到 `schema_relations` 表，供以后的外键扩展使用。

每个 Chunk 的 metadata 里记录了来源：说明来自哪个 CSV 的哪几行、语义配置的文件名和 hash、这段内容是原文还是推断。

## 4. 资料修正：批准后才生效

BIRD 的说明文件里有编码错误。比如 toxicology 的 `atom.element` 里有 15 处 U+0095 控制字符，本来应该是项目符号 `•`；student_club 的 `Budget.csv` 是 cp1252 编码，而不是 UTF-8。

修正不能由程序自行判断：

1. 先生成**候选计划**，列出每条规则、改动前后的内容和证据，交给用户审阅；
2. 用户批准后，计划状态改为 `approved`，并记下批准人、日期、原话，以及"批准范围"的 sha256；
3. 执行时重新计算批准范围的 hash。只要规则被改过，旧的批准记录就失效，计划不能执行；
4. 修正写进新的派生 CSV，原文件不动，修正报告里保留原始内容和派生内容的对应关系。

已批准的计划在 `config/catalog_corrections/`，语义配置在 `config/knowledge_semantics/`。

## 5. 快照身份：同样的输入，同样的快照

- **`corpus_id` 是确定性的**：它由构建配置的各个版本号、每个库的结构版本、每个来源文件的 hash 一起计算得出。同样的输入，再构建一次得到同一个 `corpus_id`；如果目标目录已经存在且内容一致，就只读复用。
- **任何输入变化，都会得到新的快照**。旧快照不会被修改，所以评测结果总能对应到当时用的知识版本。
- Document、Chunk、Relation 的 ID 都是由 `corpus_id` 和对象 key 生成的 UUID5，不依赖随机数。
- manifest 记录每个核心文件的 sha256；`verify` 每次都重新读取并核对，不会拿上次的结果代替。

检索请求必须带上 **Knowledge Scope**，也就是 `corpus_id`、`database_id`、`schema_version` 三元组，只在这一个快照里检索，不会把不同版本的知识混在一起。

## 6. PostgreSQL 存储

知识存放在独立的 PostgreSQL 数据库，schema 为 `querypilot_kb`，和业务用的 SQLite 分开：

| 表 | 内容 |
|---|---|
| `knowledge_corpora` | 每个快照的身份和 manifest |
| `knowledge_documents` | 每张表一个 Document |
| `knowledge_chunks` | 检索单元：正文、类型、表列定位、metadata |
| `schema_relations` | 结构化的外键边 |
| `chunk_embeddings` | 每个 (Chunk, Embedding Profile) 一条向量；是从 Corpus 派生的索引，不属于 Corpus（[ADR 0002](../adr/0002-embeddings-outside-corpus.md)） |
| `schema_migrations` | 迁移版本 |

**迁移**：结构变化写成按编号执行的迁移文件。`001` 建知识表；`002` 建向量表，并删掉 `knowledge_chunks` 上预留的三个向量列（`embedding`、`embedding_profile_id`、`embedding_dimensions`）和对应的 CHECK。`store migrate` 在一个事务里按顺序补跑缺少的迁移，已执行的迁移版本和摘要必须与文件一致，否则拒绝。`store check` 核对 PostgreSQL 与 pgvector 版本、迁移已执行到最新且摘要一致，以及三个角色的实际权限；不逐列比对表结构，迁移文件不被改动由摘要保证，数据是否完整由 `store verify` 逐字段核对。

**三个角色，权限最小化**：admin 只负责建库和迁移；importer 只能 SELECT 和 INSERT（包括向量表），没有 UPDATE 和 DELETE，所以导入和写向量都只能新增、不能改写已有知识；reader 只能 SELECT。

- **导入**：先重新校验快照，再在一个事务里写入。如果同一个快照已经导入过，就逐字段读回核对，一致就复用（`reused`），不一致就拒绝，绝不覆盖。如果没收到 COMMIT 的确认，结果记为 `unknown`，不会猜成功或失败。
- **读取**：`open_read_scope` 开一个 `REPEATABLE READ READ ONLY` 事务，先核对 Scope 存在，之后的查询都在同一个一致的快照视图里进行。会话只暴露有上限的读取方法，不给连接和游标。

## 7. 检索：BM25、向量、混合与重排

`retrieve_knowledge(request, settings, embedder=None, reranker=None)` 在一个 Scope 内做一次检索，返回按分数排好序的 Hit 列表。`request.method` 选择方法，默认 `lexical`：

| method | 做什么 | `score` |
|---|---|---|
| `lexical` | BM25，不使用 embedder | BM25 分 |
| `vector` | 先确认当前 Embedding Profile 有向量（没有报 `embedding_missing`，不调用 API），把查询嵌入一次，按余弦距离精确排序 | 余弦相似度 |
| `hybrid` | BM25 和向量各取 50 个候选，用 RRF（k=60，两路等权，同分按 `chunk_key`）融合后取前 `top_k` 个；BM25 没有命中时就是向量的排序 | RRF 分 Σ 1/(60 + 名次) |

三种方法在检索题集上的对比见[检索评测](../evaluation/retrieval-evaluation.md)第 3、4 节。不同方法的分数不能直接比较。嵌入查询失败报 `embedding_error`，不退回 BM25。没传 embedder 时按 `QUERYPILOT_EMBEDDING_*` 创建；批量调用时由调用方创建一个传入。k、权重和候选数用固定的默认值，不针对评测题调整（见 ADR 0001）。

**重排**是检索之后独立的一步，由 `request.rerank` 开启，默认关闭，可以和三种 `method` 任意组合：

1. 按 `method` 取前 20 个候选。这个数固定，与 `top_k` 无关；hybrid 仍是两路各 50 个融合后取前 20。
2. 只读会话关闭之后，把查询原文和每个候选的 `content` 原文交给 Reranker 打分。
3. 每个 Hit 的 `score` 换成相关性分数，按分数从高到低排，同分保持重排前的先后顺序，再取前 `top_k` 个。

开启重排时 `top_k` 最大为 20，超过在构造请求时就被拒绝。没有候选时不调用 Reranker，直接返回 `no_match`。重排失败报 `rerank_error`，不退回未重排的结果。结果里的 `rerank_profile_id` 是这次用的 Rerank Profile，配置版本在方法的版本后面拼上 `r4b-rerank-v1`（如 `r4-hybrid-v1+r4b-rerank-v1`）。重排只改变候选的顺序，候选里没有的 Chunk 它找不出来。目前 Reranker 需要由调用方传入（接口为 `profile_id` 加 `rerank(query, documents)`），仓库里只有测试用的假实现。

BM25 这一路的细节：

- **索引**：使用 pg_search 0.26.4。`title`、`content` 和展示名用 `jieba` 分词，支持中文；`table_name`、`column_name`、`schema_name` 用 `simple` 分词。
- **查询处理**：ASCII 字母转成小写，除字母、数字、汉字以外的字符都当作分隔符；中文片段用 jieba 分词，并过滤掉表达用的词；最后保序去重。如果没有一个有效的词，返回 `input_error`；hybrid 也一样，不会退化成只用向量（向量检索对任何输入都会给出 top_k 个结果，会把输入错误变成看似正常的 Hit）。
- **匹配和排序**：任意一个词命中任意一个字段就算候选（OR）。先按 Scope 和知识类型过滤，再按分数降序排列；分数相同时，按 `chunk_key`、`chunk_id` 排，保证结果稳定。`top_k` 默认 10，最大 50（开启重排时最大 20）。
- **只读**：使用 reader 角色和只读事务；检索结果里只带查询的 sha256，不带原文。

**已知局限**：没有做英文词干化，`elements` 匹配不到 `element`。另外，同一张表的多个列块可能一起挤占排名前列。两者都在[检索评测](../evaluation/retrieval-evaluation.md)里量化过。

## 8. 设计取舍

- **按对象切块（表 / 列 / 关系）**：能精确命中某一列，也方便归到表来计分。代价是一张表会被切成很多块，强关键词会让它挤占前排。粒度怎么切，计划在接入向量检索之后，在相同的上下文 token 预算下用评测来比较。
- **不可变快照**：评测可以复现，也不会出现"导入到一半"的状态。代价是任何修改都要重新构建。
- **先做词法检索，再加向量和融合**：每加一种机制，都能和上一步的基线对比，单独量出它的贡献。
- **关系单独成块，同时结构化存储**：既能被检索到，也为以后的外键扩展（沿外键补上桥接表）留好了数据。
