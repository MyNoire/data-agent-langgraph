# 检索评测

衡量的是"检索给下游的知识够不够写出正确的 SQL"：问题需要的表和列，有没有出现在检索结果的前几位。它和 [SQL 评测](sql-evaluation.md)分开进行，这样 SQL 写错时能分清是检索没给对知识，还是模型写错了。

指标的定义以 [GLOSSARY](../../GLOSSARY.md) 的「评测」一节为准。

## 1. 题集

- 题目取自 BIRD Mini-Dev 里两个库的全部题：toxicology 40 题，student_club 48 题，共 88 题，其中 16 题属于冻结集。student_club（社团、活动、预算、会员）业务直观，便于分析失败原因；它的结构见 [student_club-schema.png](student_club-schema.png)。
- 使用英文原题，不带 evidence。评测直接给定每道题所在库的 Knowledge Scope，不评选库。
- **Relevant Set**：用 sqlglot 按 SQLite 方言解析金标准 SQL，还原别名和省略了表名的列，收集到的表和列就是这道题需要的知识。名字统一映射回知识库里的写法。它是代理标注：等价的 SQL 可能用别的表也能写对。
- 生成题集：`querypilot eval retrieval-cases`。题集文件在本地（Git 忽略），manifest 提交在 `evals/retrieval/retrieval_cases_manifest.json`，记录题号、来源 hash 和 Knowledge Scope。

## 2. 指标

每题检索一次，`top_k=20`。每个 Hit 先归到它所属的表：表块和列块取自己的表名，关系块取外键的**源表**（目标表要靠检索之后的外键扩展才能取到，不算检索本身的功劳）。

| 指标 | 单题怎么算 | 汇总 |
|---|---|---|
| 首次命中名次 | 每张相关表、每个相关列第一次出现的 Hit rank；没出现为空 | 逐题保存，用于失败分析 |
| **Table Complete Recall@K**（主指标） | 相关表是否全部出现在前 K 个 Hit 里 | 对所有题取比例 |
| Table Recall@K | 前 K 个 Hit 里出现的相关表所占比例 | 对所有题取平均 |
| **Hits to Cover** | 各相关表首次命中名次的最大值，也就是这道题至少需要的 top_k；有表没出现就为空 | 能全覆盖的题取中位数和平均数，另报拿不全的题数 |
| Column Recall@10（诊断） | 相关列中，有自己的列块出现在前 10 个 Hit 里的比例 | 只对有相关列的题取平均 |

K 取 5 和 10。几个关键决定：

- **K 以 Hit 计，和检索参数 top_k 是同一个单位。** 下游拿到的就是前 top_k 个 Hit，所以指标能直接回答"top_k 设多少够用"。`Complete Recall@K` 等于"Hits to Cover ≤ K 的题所占的比例"。
- **主指标要求全部到齐。** 写 SQL 时少一张表基本就写不对。
- **表和列分开报告，不合成一个总分。** 一道题通常只用 2 张表，却可能涉及 5 个列，混在一起分数会被列主导。
- **0 和空不一样。** 检索报错（查询里没有有效词）的题，有该拿的表却一张都没拿到，表级指标记 0，计入分母；Hits to Cover 拿不全时没有一个可以算的数，记为空，单独报题数；没有相关列的题，Column Recall 是 0/0，记为空，不参与平均。
- **冻结题一起计分**，在逐题结果里标出来；但失败分析和修改只针对非冻结题，见 [ADR 0001](../adr/0001-frozen-set-for-component-eval.md)。
- 不报 MRR 和 nDCG：要回答的是"表有没有拿全"，名次由 Hits to Cover 衡量；MRR 只看第一个相关项，暂不加，nDCG 不用。

报告写进运行目录，记录题集 sha256、各库的 Knowledge Scope、检索方法和配置版本、top_k 和 K；不包含问题原文、SQL 和 Hit 正文。

## 3. BM25 基线（2026-11-06）

条件：`lexical` / `r3-bm25-v1`，英文原题，top_k=20，88 题。

| | 整体（88） | toxicology（40） | student_club（48） |
|---|---|---|---|
| **Table Complete Recall@5** | **64.8%** | 72.5% | 58.3% |
| **Table Complete Recall@10** | **84.1%** | 92.5% | 77.1% |
| Table Recall@5 / @10 | 83.5% / 92.8% | 88.8% / 97.5% | 79.2% / 88.9% |
| Hits to Cover 中位数 / 平均数 | 3 / 4.9 | 3 / 3.9 | 4 / 5.8 |
| 20 个 Hit 内拿不全的题 | 3 | 1 | 2 |
| Column Recall@10 | 67.5% | 75.8% | 60.5% |

检索耗时 P50 为 276 ms，P95 为 334 ms。

从 K=5 放宽到 K=10，完整召回从 65% 升到 84%，说明很多题不是"找不到"，而是"排得不够靠前"。

### 三种方法对比（R4，2026-11-10）

同一份题集（sha256 `6014f798…`）、同一份 Corpus（toxicology `d4792b3c…`、student_club `66f8085b…`）、top_k=20、同样的指标，只换检索方法。lexical 用上面的 BM25 基线运行；vector 和 hybrid 的 Embedding Profile 为 `siliconflow:Qwen/Qwen3-Embedding-8B:4096:content-v1:query-instruct-v1`，配置版本 `r4-vector-v1` / `r4-hybrid-v1`（RRF k=60、两路等权、每路 50 个候选，均为未调过的默认值）。

| 整体（88 题） | lexical | vector | hybrid |
|---|---|---|---|
| **Table Complete Recall@5** | 64.8% | 62.5% | **68.2%** |
| **Table Complete Recall@10** | 84.1% | 80.7% | **85.2%** |
| Table Recall@5 / @10 | 83.5% / 92.8% | 82.3% / 91.3% | 85.8% / 93.5% |
| Hits to Cover 中位数 / 平均数 | 3 / 4.9 | 3 / 5.6 | 3 / 4.9 |
| 20 个 Hit 内拿不全的题 | 3 | 3 | 2 |
| Column Recall@10 | 67.5% | 66.8% | **73.6%** |
| 检索耗时 P50 / P95 | 276 / 334 ms | 445 / 1744 ms | 489 / 2373 ms |

| Complete Recall@5 / @10 | lexical | vector | hybrid |
|---|---|---|---|
| toxicology（40） | 72.5% / 92.5% | **77.5% / 97.5%** | 72.5% / 92.5% |
| student_club（48） | 58.3% / 77.1% | 50.0% / 66.7% | **64.6% / 79.2%** |

- **hybrid 整体最好，但提升不大**：Complete Recall@5 多 3.4 个百分点（3 道题），@10 多 1.1 个百分点，Column Recall@10 多 6.1 个百分点。
- **vector 单独用不如 BM25**：整体略低，在 student_club 上低 8 个百分点；但在 toxicology 上是三种方法里最好的。
- **代价是延迟**：每次检索都要调用一次 Embedding 服务，P50 多约 200 ms，P95 从 0.3 s 涨到 1.7–2.4 s，主要是网络调用的波动。
- 88 题里一道题就是 1.1 个百分点，上面的差距只有几道题；每种方法只跑了一次，查询向量每次会有约 0.001 的余弦抖动，名次接近的 Hit 可能互换。这些数字说明方向，不足以下"显著更好"的结论。

运行目录（本地，Git 忽略）：vector `artifacts/runs/evaluation/2026-11-10/5b1da09ba55d47c08f33e259d68ed35c/`，hybrid `artifacts/runs/evaluation/2026-11-10/fe098b1338194d0faaa39cd5ce185cec/`；lexical 为下面第 5 节命令在 2026-11-06 的运行 `3c83f0af…`。三份报告里都没有问题原文。

## 4. 失败分析（只看非冻结题）

非冻结题共 72 道，其中 24 道在 K=5 时没拿全相关表；这 24 道里有 13 道到 K=10 就能拿全。主要有五类原因：

| 类型 | 例子 | 原因 | 能解决它的改进 |
|---|---|---|---|
| **英文词形对不上** | "What **elements** are in a double type bond?"：atom 表在前 20 个 Hit 里一次都没出现 | 索引没有英文词干化。对照查询 `element` 能命中 `atom.element`，`elements` 零命中；`event` 和 `events` 也是这样 | 英文词形归一；向量检索 |
| **问的是数据值** | "…the funds that the **Vice President** received?"：member 表排到第 17 | "Vice President" 是 `member.position` 的取值，而知识库只描述了列，没有取值（见[知识库](../knowledge/knowledge-base.md)的列块例子） | 从数据库本身抽取分类列的取值；值链接 |
| **一张表挤占前排** | "…percentage of carbon in double-bond molecules?"：bond 和 connected 两张表的块占了前 6 个 Hit，atom 到第 7 个才出现 | 强关键词让同一张表的多个列块一起排到前面 | 限制单表名额；调整知识粒度（如 small-to-big） |
| **常用词误配** | "**State** the date … paid his/her dues."：动词 State 把 `zip_code.state` 顶到了第 1 位 | 关键词检索只看字面 | 向量检索 + 融合 |
| **桥接表** | 需要 expense、budget、event 三张表的问题：budget 只用来连接另外两张表，问题里不会提到，排到第 11 | 用户只会说业务对象，不会说连接路径 | 检索之后的外键扩展 |

混合检索能解决词形和误配两类问题，对数据值类也有部分帮助；挤占和桥接表要靠检索之后的上下文组装来解决。下一步的计划见[路线图](../roadmap.md)。每加一项改进，都用同一份题集和这份基线对比，单独量出它的贡献。

### R4：向量和混合检索救回了什么、弄丢了什么

只看 72 道非冻结题。按 ADR 0001，这里只做分析，没有据此改知识、参数或代码。

| K=5 没拿全的题 | lexical | vector | hybrid |
|---|---|---|---|
| K=5 | 24 | 25 | **20** |
| K=10 | 11 | 11 | **7** |

和 BM25 逐题比：vector 在 K=5 救回 11 道、弄丢 12 道；hybrid 救回 7 道、弄丢 3 道，到 K=10 弄丢的为 0。两路几乎是互补的：

- **向量补上了语义和词形**。"What **elements** are in a double type bond?"：atom 表在 BM25 里没出现，向量排第 5（词形问题）；"Which department was the **President** of the club in?"：member 表从第 11 升到第 1，向量把 President 和"成员职位"联系起来了（数据值类问题里的一部分）；"…how many of them are **meetings**?"：event 表从第 13 升到第 1。
- **向量丢掉了字面的结构词**。问题里直接出现表名或列名，同时又带着具体的值时，BM25 靠字面命中排在前面，向量却被具体的值带偏："What is the total **cost** of the pizzas…"：expense 表从第 1 掉到第 15；"…expenses … approved for 'October Meeting' **event**"：event 表从第 1 掉到第 16；"…total cost that **member id** "rec4BLdZHS2Blfp4v" incurred?"：member 表从第 1 掉到第 8。student_club 的题大多带活动名、人名这类具体值，所以 vector 在这个库上最差；toxicology 的题多是化学词汇，语义和词形更重要，vector 在那里最好（这是推测，没有单独验证）。
- **hybrid 变差的 3 道都在边界上**：相关表从第 3 掉到第 6、从第 1 掉到第 6、从第 5 掉到第 6。另一路没找到它，而别的 Chunk 两路都找到了、分数加在一起，就把它挤出了前 5。
- **hybrid 也会冲淡单路的发现**。只有一路找到的 Chunk 只拿一份 RRF 分，会排在两路都找到的 Chunk 后面："elements"那题 atom 表在向量里排第 5，hybrid 里排第 9；"List all the names of events…"那题 event 表在向量里排第 1、BM25 没有，hybrid 里排第 8。这是等权 RRF 的特点，权重和 k 都没调过。
- **没解决的**：Vice President（member 表 17 → 10 → 14）、一张表挤占前排（carbon 那题 atom 表 7 → 6 → 10）、桥接表。这三类本来就不指望靠检索方法解决，要靠取值知识、单表名额或外键扩展。

## 5. 怎么跑

```powershell
uv run querypilot eval retrieval-cases --questions <mini_dev_sqlite.json> --frozen evals/bird_final100.json `
  --corpus-dir data/rag/toxicology/r1-4-corpus --corpus-dir data/rag/student_club/r1-4-corpus `
  --output evals/retrieval/retrieval_cases.json
uv run querypilot eval retrieval --cases evals/retrieval/retrieval_cases.json                  # 默认 lexical
uv run querypilot eval retrieval --cases evals/retrieval/retrieval_cases.json --method vector
uv run querypilot eval retrieval --cases evals/retrieval/retrieval_cases.json --method hybrid
uv run querypilot eval retrieval --cases evals/retrieval/retrieval_cases.json --method hybrid --rerank
```

需要已经导入 PostgreSQL 的知识库，以及 `.env` 里的只读 DSN。`--method` 可选 `lexical`（BM25，默认）、`vector`、`hybrid`（RRF 融合），见[知识库](../knowledge/knowledge-base.md)第 7 节。vector 和 hybrid 还需要 `QUERYPILOT_EMBEDDING_*` 配置和已生成的向量（`knowledge store embed`），每道题调用一次 Embedding 服务，88 题约 88 次，**会产生费用**。

三种方法的题集、Corpus、`top_k=20`、K=5/10 和 Column Recall@10 都相同，差异只来自检索方法。运行配置记录 `method`；报告的 `config.method` 是请求的方法，`config.retrieval` 列出结果实际用到的方法、Embedding Profile 和配置版本（`r3-bm25-v1` / `r4-vector-v1` / `r4-hybrid-v1`，含义写在 `retrieval/models.py` 的 `RETRIEVAL_CONFIG_VERSIONS` 旁）。计分规则的测试使用假检索器，不需要连数据库：`tests/test_retrieval_eval_scoring.py`。

`--rerank` 在检索之后加一步重排，默认关闭，可以和任何 `--method` 组合（见[知识库](../knowledge/knowledge-base.md)第 7 节）。它需要 `QUERYPILOT_RERANK_*` 配置，每道题至多调用一次 Reranker 服务（最多 20 个候选；没有候选或查询没有有效词的题不调用），88 题最多 88 次，**会产生费用**；和 vector、hybrid 组合时 Embedding 的调用照旧。一次运行只创建一个 Reranker，所有题共用。评测的 `top_k=20` 等于重排的候选数，所以重排只改变这 20 个 Hit 的顺序，题集、Corpus 和指标都不变。某一题重排失败（`rerank_error`）会中止整批运行，和 Embedding 失败时一样，不能从断点续跑。

不管开不开重排，运行配置都记录 `rerank`（真或假）；报告的 `config.rerank` 是请求的开关，`config.retrieval` 的每一项有 `rerank_profile_id`（没有重排时为空）。开启重排时，配置版本在方法的版本后面拼上 `r4b-rerank-v1`（如 `r4-hybrid-v1+r4b-rerank-v1`）。2026-11-12 之前生成的运行没有 `rerank`、`config.rerank` 和 `rerank_profile_id` 这三个字段，它们都没有重排。
