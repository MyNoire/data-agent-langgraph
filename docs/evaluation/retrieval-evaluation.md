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

### 重排（R4b，2026-11-12）

在 lexical 和 hybrid 之后各加一步重排（`--rerank`）：取前 20 个候选，交给 Reranker 重新排序。题集、Corpus、top_k=20 和指标都和上面相同。Rerank Profile 为 `siliconflow:Qwen/Qwen3-Reranker-8B:content-v1:task-instruct-v1`，配置版本 `r3-bm25-v1+r4b-rerank-v1` / `r4-hybrid-v1+r4b-rerank-v1`。候选数、模型和指令都是事先定好的，没有调过。lexical 和 hybrid 两列是上面的旧运行，没有重跑。

| 整体（88 题） | lexical | hybrid | lexical + 重排 | hybrid + 重排 |
|---|---|---|---|---|
| **Table Complete Recall@5** | 64.8% | 68.2% | **75.0%** | 67.0% |
| **Table Complete Recall@10** | 84.1% | 85.2% | 85.2% | **89.8%** |
| Table Recall@5 / @10 | 83.5% / 92.8% | 85.8% / 93.5% | 88.2% / 93.5% | 84.0% / 95.6% |
| Hits to Cover 中位数 / 平均数 | 3 / 4.9 | 3 / 4.9 | 3 / 4.6 | 4 / 5.0 |
| 20 个 Hit 内拿不全的题 | 3 | 2 | 3 | 2 |
| Column Recall@10 | 67.5% | 73.6% | 74.4% | **78.2%** |
| 检索耗时 P50 / P95 | 0.28 / 0.33 s | 0.49 / 2.4 s | 0.97 / 15.5 s | 10.6 / 39.9 s |

| Complete Recall@5 / @10 | lexical | hybrid | lexical + 重排 | hybrid + 重排 |
|---|---|---|---|---|
| toxicology（40） | 72.5% / 92.5% | 72.5% / 92.5% | **82.5%** / 95.0% | 72.5% / **100%** |
| student_club（48） | 58.3% / 77.1% | 64.6% / 79.2% | **68.8%** / 77.1% | 62.5% / **81.3%** |

- **重排有用，但效果取决于候选从哪来。**
  - 在 BM25 的候选上重排：Complete Recall@5 从 64.8% 升到 75.0%（多 9 道题），是目前前 5 最好的配置。BM25 是确定的，两次运行的候选完全相同，所以这 9 道题都是重排的贡献。
  - 在 hybrid 的候选上重排：@5 从 68.2% 变成 67.0%（少 1 道题，在噪声范围内），@10 从 85.2% 升到 89.8%（多 4 道题），Column Recall@10 多 4.5 个百分点。
- **重排只改变顺序，找不出候选里没有的表。** "20 个 Hit 内拿不全"的题数，重排前后一样（lexical 3 道，hybrid 2 道）。
- **两组重排的差别来自候选，不是来自 Reranker。** Reranker 给每个候选单独打分，同一个 Chunk 在两组里的分数基本相同（这是按模型的工作方式推断的，报告里不保存 Hit 列表，没有逐个核对）。
  - toxicology 整个 Corpus 只有 20 个 Chunk。hybrid 把它们全部交给 Reranker，所以这个库上的"hybrid + 重排"就是 Reranker 自己给整个 Corpus 排序，第一阶段检索不起作用。它的 @5 是 72.5%，和 BM25 一样。
  - BM25 在这个库上有 35 道题的候选不到 20 个（最少 6 个），只留下和问题有共同词的 Chunk。在这批候选上重排，@5 是 82.5%。也就是说，BM25 在这里起了过滤的作用，去掉了一些和问题没有共同词、Reranker 却打分不低的 Chunk。
  - 反过来，候选多对 @10 有利：hybrid + 重排在 toxicology 上 @10 是 100%，lexical + 重排是 95.0%，因为有的表 BM25 根本没找到。
- **有了重排，还要不要向量这一路？在这份题集上没有单一的答案。** 只看前 5，lexical + 重排更好（75.0% 对 67.0%，差 7 道题）。看前 10 和"能不能找全"，hybrid + 重排更好（89.8% 对 85.2%，拿不全的题 2 道对 3 道）。哪个更合适，取决于 R5 里放进提示词的 Hit 有多少。
- **代价是延迟，而且波动很大。** 两组运行前后相接（hybrid + 重排用了 18.9 分钟，lexical + 重排用了 4.9 分钟）。
  - 有 50 道题两组都交给 Reranker 20 个候选，这些题的耗时中位数在 lexical + 重排里是 1.0 秒，在 hybrid + 重排里是 11.3 秒。hybrid 只多一次 Embedding 调用，解释不了这个差距，所以它主要是服务本身在两个时段的波动，不能说明 lexical + 重排更快。
  - hybrid + 重排有 17 道题超过 20 秒，最慢的一道 51.5 秒；lexical + 重排有 2 道，最慢 30.9 秒。客户端的超时是 60 秒。耗时里包含 SDK 的自动重试，报告分不出是服务慢还是重试了。
- **这些数字的可靠程度**：每种配置只跑了一次；88 题里一道题就是 1.1 个百分点，1–2 道题的差别属于噪声；同一个候选的重排分数两次调用之间会差 0.02 左右，名次接近的 Hit 可能互换；hybrid + 重排和 hybrid 基线各自嵌入查询，student_club 上的候选可能略有出入。只有两个库，不能推广到别的数据。

运行目录（本地，Git 忽略；目录按 UTC 日期命名）：hybrid + 重排 `artifacts/runs/evaluation/2026-11-11/3af18ae175bf488cb150592438006648/`，lexical + 重排 `artifacts/runs/evaluation/2026-11-11/913415bbac814780b01e4662753461f4/`。两份报告里都没有问题原文。

### 并发运行（2026-11-12）

`--workers N` 用 N 个线程同时检索。它只改变评测跑多快，不改变检索本身。用 hybrid + 重排、`--workers 8` 重跑了一次，题集、Corpus、Embedding Profile、Rerank Profile 和配置版本都和上面串行的 hybrid + 重排相同。

| 整体（88 题） | 串行（workers=1，2026-11-11） | 并发（workers=8，2026-11-12） |
|---|---|---|
| **评测总时长** | **18.9 分钟** | **45 秒** |
| 各题检索耗时之和 | 18.9 分钟 | 5.9 分钟 |
| Table Complete Recall@5 / @10 | 67.0% / 89.8% | 67.0% / 89.8% |
| Table Recall@5 / @10 | 84.0% / 95.6% | 84.0% / 95.6% |
| Hits to Cover 中位数 / 平均数 | 4 / 5.0 | 4 / 4.9 |
| 20 个 Hit 内拿不全的题 | 2 | 2 |
| Column Recall@10 | 78.2% | 78.4% |

- **指标没有变。** 两个库各自的 Complete Recall@5 / @10 也都相同；72 道非冻结题里，K=5 和 K=10 的"拿全 / 没拿全"没有一道发生变化；88 题里有 83 题各相关表的首次命中名次完全相同，其余 5 题是名次接近的 Hit 互换。可能的原因有两个，没有区分：查询向量和重排分数每次调用都有微小抖动；两次运行之间跑过集成测试，BM25 分数随之变化（见本节末尾"BM25 分数受索引里残留行的影响"）。
- **总时长从 18.9 分钟降到 45 秒，其中并发贡献约 8 倍，其余来自服务这次本身更快。** 并发那次各题检索耗时加起来是 5.9 分钟，8 个线程把它压到 45 秒（约 7.8 倍）。而 5.9 分钟本身只有串行那次的三分之一：那次有 17 道题超过 20 秒，这次最慢的一道是 13.1 秒，没有超过 20 秒的。两次运行隔了一天，服务的快慢不是同一个水平，所以"快了 25 倍"不能全算在并发头上。
- **并发运行里的单题耗时不能和串行的比。** 这次的 P50 / P95 是 3.4 / 10.3 秒，比串行那次的 10.6 / 39.9 秒还低，原因是服务的波动，不是并发让检索变快。反过来，lexical 不调用模型服务，同一天用 1 个和 8 个线程各跑一次（总时长 17.8 秒和 3.7 秒），单题 P50 从 192 ms 升到 315 ms：同时进行的请求会互相拖慢。所以**检索耗时 P50 / P95 只在 `workers=1` 的运行之间比较**，上面各表里的耗时都来自 `workers=1`。
- 8 个线程共用同一个 Embedding 客户端和同一个 Reranker 客户端，各自开知识库连接，88 题没有出现错误。只跑了这一次。

运行目录（本地，Git 忽略）：`artifacts/runs/evaluation/2026-11-12/e5feb5b32e0742ababdf35d67e4d363e/`；lexical 的两次为同一日期下的 `708726076057461799633e395aee6bcb/`（workers=1）和 `df479a2a7a9847d68d4d3d39fc038eb0/`（workers=8），两份报告的逐题结果除 `elapsed_ms` 外完全相同。

上面的运行用的是一次把所有题交给线程池的写法。之后并发部分改成了主线程派发（见第 5 节），这只改变题目什么时候开始，不改变检索。改完后只用 lexical 重跑了 `--workers 1` 和 `--workers 8`（2026-11-13，总时长 23.8 秒和 3.8 秒），逐题结果除 `elapsed_ms` 外完全相同；同一时间用改之前的运行器再跑一次 `--workers 1`，结果也相同。hybrid + 重排没有重跑。运行目录：`artifacts/runs/evaluation/2026-11-13/` 下的 `61740a0a612849f0a3fd79b47e3d2130/`（workers=1）、`798d6876b632472eb8e70f8b1e790f66/`（workers=8）和 `225cd725ec624b80bd1f96ef51da6184/`（改之前的运行器）。

### BM25 分数受索引里残留行的影响（2026-11-13 发现并修复）

BM25 的分数用到整个索引的统计量（文档数、词出现在多少文档里、平均长度），不是只算当前 Knowledge Scope 的。两个后果：

- **索引里有别的 Corpus，分数就不同。** Scope 只决定返回哪些 Chunk，不决定统计量。往知识库再导入一份 Corpus，已有 Corpus 的 lexical 和 hybrid 结果会跟着变。
- **删掉的行在 `VACUUM` 之前仍然计入统计量。** PostgreSQL 的 `DELETE` 只是把行标成已删除，索引里的条目要等 `VACUUM`（或自动清理）才去掉。

之前检索的集成测试会往正式知识库导入一份测试 Corpus 再删掉，每次留下一批还没清理的行，正式 Corpus 的分数就跟着变，变多少取决于测试跑过几次、自动清理有没有发生。只跑一次 `tests/integration/test_retrieval_eval.py`（插入 8 行、删除 8 行），前后两次 lexical 评测就有 9 题不同。

修复：这些测试改到专用测试库 `querypilot_knowledge_test` 上跑，不再写正式库；正式库执行了一次 `VACUUM`，索引回到只有 84 个 Chunk 的状态。

在干净的索引上把用到 BM25 的四种配置各重跑了一次（2026-11-13，`--workers 8`；题集、Corpus、Embedding Profile、Rerank Profile 和配置版本都和原来相同）。下表写"原来 → 重跑"，只有一个数的是没变。vector 不用 BM25，没有重跑。

| 整体（88 题） | lexical | hybrid | lexical + 重排 | hybrid + 重排 |
|---|---|---|---|---|
| **Table Complete Recall@5** | 64.8% | 68.2% → **69.3%** | 75.0% → **76.1%** | 67.0% |
| **Table Complete Recall@10** | 84.1% | 85.2% | 85.2% | 89.8% |
| Table Recall@5 | 83.5% | 85.8% → 86.2% | 88.2% → 88.7% | 84.0% |
| Table Recall@10 | 92.8% | 93.5% | 93.5% | 95.6% |
| Hits to Cover 中位数 / 平均数 | 3 / 4.9 | 3 / 4.9 | 3 / 4.6 | 4 / 5.0 |
| 20 个 Hit 内拿不全的题 | 3 | 2 | 3 | 2 |
| Column Recall@10 | 67.5% → 67.9% | 73.6% | 74.4% | 78.2% → 78.4% |

- **结论都没有变。** K=10 的指标和"拿不全"的题数四种配置都没变；K=5 上 hybrid 和 lexical + 重排各有 1 道非冻结题从"没拿全"变成"拿全"（hybrid 在 student_club 上 64.6% → 66.7%，lexical + 重排在 toxicology 上 82.5% → 85.0%），没有变差的题，冻结题没有变化。各方法之间的先后顺序不变；"只看前 5，lexical + 重排比 hybrid + 重排好"现在是 76.1% 对 67.0%，差 8 道题（原来差 7 道）。
- **引用数字时以本表为准**：它的四列来自同一个索引状态。本文前面的表格没有改，仍是各自运行时的结果，和本表最多差 1 道题。
- lexical 不调用模型服务，变化只来自索引：88 题里 21 题的逐题记录不同（1 道冻结题），变的是表和列的命中名次；`--workers 1` 和 `--workers 8` 结果相同。其余三种配置的变化里还混着查询向量和重排分数的抖动，分不开。
- 2026-11-12 和 2026-11-13 上午的 lexical 运行也都带着残留行，和干净索引分别差 9 题和 21 题，表级指标同样没变。
- 这几次重跑用了 8 个线程，耗时不能和前面的表比；前面各表的 P50 / P95 仍来自 `workers=1` 的运行。评测总时长分别约 19 秒（hybrid）、15 秒（lexical + 重排）、32 秒（hybrid + 重排）。

运行目录（本地，Git 忽略）：`artifacts/runs/evaluation/2026-11-13/` 下，lexical `473ac64621f14627969564e6348cd6fa/`（workers=1）和 `641304b445734a6ebe409f525325cdb9/`（workers=8），hybrid `fac48f7d76914be695bbde256b6c92f1/`，lexical + 重排 `01e273963dc941ca9c2f37469c8a790d/`，hybrid + 重排 `334e6224518e4419bcee636afe03585e/`；只跑一次集成测试之后的那次 lexical 是 `d2e9146a7d3c472ea2b27182246d099b/`。

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

### R4b：重排救回了什么、弄丢了什么

只看 72 道非冻结题。按 ADR 0001，这里只做分析，没有据此改知识、参数或代码。下面的名次都是"相关表第一次出现在第几个 Hit"。

| 没拿全的题 | lexical | hybrid | lexical + 重排 | hybrid + 重排 |
|---|---|---|---|---|
| K=5 | 24 | 20 | **16** | 23 |
| K=10 | 11 | 7 | 9 | **5** |

K=5 时：在 BM25 的候选上重排，救回 12 道、弄丢 4 道；在 hybrid 的候选上重排，救回 8 道、弄丢 11 道。K=10 时两组都是救回 3 道、弄丢 1 道。

- **重排擅长把"意思相关、字面不突出"的第二张表提上来。**
  - "What is the percentage of **carbon** in double-bond molecules?"：atom 表在 BM25 里排第 7，重排后第 3；在 hybrid 里排第 10，重排后第 4。
  - "What **elements** are in the TR004_8_9 bond atoms?"：atom 表从第 7（BM25）、第 9（hybrid）都升到第 2。
  - "Which department was the **President** of the club in?"：member 表在 BM25 里排第 11，重排后第 2。
- **重排会把问题里直接点名的表挤出前 5。** 两组重排的方向一致，说明这是 Reranker 的偏好，和候选从哪来无关。
  - "Name the **event** with the highest amount spent on advertisement."：event 表原来排第 1，重排后是第 7（lexical）和第 8（hybrid）；budget 表从第 2 升到第 1。
  - "Calculate the amount budgeted for 'April Speaker' **event**…"：event 表从第 3、第 4 掉到第 8、第 9。
  - "What is the total cost of the pizzas for all the events?"：expense 表从第 1、第 2 掉到第 5、第 6。
  - 推测是同一张表的好几个列块一起排到了前面，把另一张表挤了出去，也就是上面"一张表挤占前排"那一类问题。报告不保存 Hit 列表，这一点没有验证。
- **同样是被挤，hybrid 的候选里掉得更深。**
  - "…molecules that have single bonds … and are not carcinogenic…"：molecule 表在 lexical + 重排里排第 4，在 hybrid + 重排里排第 8。
  - "List the name of students that have attended more than 7 events."：member 表分别是第 5 和第 9。
  - hybrid 的 20 个候选里有只靠向量找到的 Chunk，Reranker 给其中一些打了高分，相关表就多掉了几名。K=5 时 hybrid + 重排比 lexical + 重排多丢 8 道非冻结题，大多是这种情况。
- **hybrid 的候选更全，好处体现在 K=10。**
  - "List all the names of events that had an attendance of over 20 students but were not fundraisers."：BM25 的候选里没有 event 表，重排也无能为力；hybrid 里它排第 8，重排后第 2。
  - "What is the amount of the funds that the **Vice President** received?"：member 表在 hybrid 里排第 14，重排后第 6；在 lexical + 重排里仍是第 16。
  - "What **elements** are in a double type bond?"：atom 表不在 BM25 的候选里；在 hybrid 里排第 9，重排后还是第 9。
- **没解决的**：有 4 道题四种配置到 K=10 都拿不全，都是桥接表或数据值类问题。
  - "Which student has been entrusted to manage the budget for the Yearly Kickoff?"：event 和 member 表排在第 11–20，或者不在候选里。
  - "State what kind of expenses that **Sacha Harrison** incurred?"：人名是 member 表里的取值，member 表排在第 12–19。
  - 这两类要靠取值知识和外键扩展，重排解决不了。

## 5. 怎么跑

```powershell
uv run querypilot eval retrieval-cases --questions <mini_dev_sqlite.json> --frozen evals/bird_final100.json `
  --corpus-dir data/rag/toxicology/r1-4-corpus --corpus-dir data/rag/student_club/r1-4-corpus `
  --output evals/retrieval/retrieval_cases.json
uv run querypilot eval retrieval --cases evals/retrieval/retrieval_cases.json                  # 默认 lexical
uv run querypilot eval retrieval --cases evals/retrieval/retrieval_cases.json --method vector
uv run querypilot eval retrieval --cases evals/retrieval/retrieval_cases.json --method hybrid
uv run querypilot eval retrieval --cases evals/retrieval/retrieval_cases.json --method hybrid --rerank
uv run querypilot eval retrieval --cases evals/retrieval/retrieval_cases.json --method hybrid --rerank --workers 8
```

需要已经导入 PostgreSQL 的知识库，以及 `.env` 里的只读 DSN。`--method` 可选 `lexical`（BM25，默认）、`vector`、`hybrid`（RRF 融合），见[知识库](../knowledge/knowledge-base.md)第 7 节。vector 和 hybrid 还需要 `QUERYPILOT_EMBEDDING_*` 配置和已生成的向量（`knowledge store embed`），每道题调用一次 Embedding 服务，88 题约 88 次，**会产生费用**。

三种方法的题集、Corpus、`top_k=20`、K=5/10 和 Column Recall@10 都相同，差异只来自检索方法。运行配置记录 `method`；报告的 `config.method` 是请求的方法，`config.retrieval` 列出结果实际用到的方法、Embedding Profile 和配置版本（`r3-bm25-v1` / `r4-vector-v1` / `r4-hybrid-v1`，含义写在 `retrieval/models.py` 的 `RETRIEVAL_CONFIG_VERSIONS` 旁）。计分规则的测试使用假检索器，不需要连数据库：`tests/test_retrieval_eval_scoring.py`。

`--rerank` 在检索之后加一步重排，默认关闭，可以和任何 `--method` 组合（见[知识库](../knowledge/knowledge-base.md)第 7 节）。它需要 `QUERYPILOT_RERANK_*` 配置，每道题至多调用一次 Reranker 服务（最多 20 个候选；没有候选或查询没有有效词的题不调用），88 题最多 88 次，**会产生费用**；和 vector、hybrid 组合时 Embedding 的调用照旧。一次运行只创建一个 Reranker，所有题共用。评测的 `top_k=20` 等于重排的候选数，所以重排只改变这 20 个 Hit 的顺序，题集、Corpus 和指标都不变。某一题重排失败（`rerank_error`）会中止整批运行，和 Embedding 失败时一样，不能从断点续跑。

不管开不开重排，运行配置都记录 `rerank`（真或假）；报告的 `config.rerank` 是请求的开关，`config.retrieval` 的每一项有 `rerank_profile_id`（没有重排时为空）。开启重排时，配置版本在方法的版本后面拼上 `r4b-rerank-v1`（如 `r4-hybrid-v1+r4b-rerank-v1`）。2026-11-12 之前生成的运行没有 `rerank`、`config.rerank` 和 `rerank_profile_id` 这三个字段，它们都没有重排。

`--workers N` 用 N 个线程同时检索，默认 1，可以和 `--method`、`--rerank` 任意组合。运行正常完成时，调用次数和费用与串行相同，只是把请求同时发出去。中途失败时不同：已经发出的请求仍会完成并计费，而串行在出错的那一次就停了。

- **线程里只做检索**；计分、逐题记录和进度事件都在主线程。只有主线程提交题目：同时在检索中的题不超过 N 道，主线程处理完一道（计分、记录、写事件）才提交下一道。报告的 `records` 和 `config.retrieval` 始终按题集顺序，与哪题先检索完无关。
- **运行配置和报告的 `config` 都记录 `workers`**。没有这个字段的旧运行都是串行的，等同于 `workers=1`。
- **耗时只在 `workers=1` 的运行之间比较**（原因见第 3 节"并发运行"）。召回类指标不受线程数影响。
- **进度事件按完成顺序写出**：`eval_case_finished` 的 `completed_cases` 是已完成的题数，从 1 递增；`case_index` 仍是这题在题集里的位置，所以并发时它不是递增的。同一时刻完成的几道题之间顺序不定；`workers=1` 时同时只有一道题在检索，事件就是题集顺序。
- **出错时**：某一题遇到 `input_error` 以外的错误，不再有新题开始，正在检索中的题（最多 N-1 道）跑完后结果丢弃，整批失败，不写 `result.json`。
- **按 Ctrl+C 或主线程自己出错时**：同样不再有新题开始，正在检索中的题（最多 N 道）跑完后结果丢弃。但不会立刻退出：在 Windows 上要等到下一道题完成才收到中断，然后还要等正在检索中的请求结束（客户端超时 60 秒，加上 SDK 的重试）。
- 没有限流和自己写的重试。整份 88 题的 hybrid + 重排约 176 次请求，远低于服务的每分钟限额；换成更大的题集或更多线程时要重新估算。
