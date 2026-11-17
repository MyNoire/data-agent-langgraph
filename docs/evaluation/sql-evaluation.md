# SQL 评测

衡量的是"给一个问题，最后执行出来的结果对不对"。实现在 `src/querypilot/evaluation/sql/`，入口是 `querypilot eval direct` 和 `querypilot eval graph`。

## 题集

题目来自 [BIRD Mini-Dev](https://github.com/bird-bench/mini_dev)（SQLite 版）：

- **12 题开发集**：日常调试用，`evals/bird_smoke_manifest.json`；
- **冻结的 100 题**：最终验证用，`evals/bird_final100_manifest.json`。
- **两库 88 题**：toxicology（40 题）和 student_club（48 题）在 Mini-Dev 里的全部题，`evals/bird_toxicology_student_club_manifest.json`。知识库目前只有这两个库，所以检索接入查询图之后的对比（R5）都在这份题集上做；题号与[检索评测](retrieval-evaluation.md)的 88 题相同。其中 16 道属于冻结集，每题带 `frozen` 标记，评测的逐题记录里原样带上：冻结题一起计分，失败分析只看其余 72 道。

仓库里只提交 manifest，记录题号、来源 URL 和各文件的 sha256；原题和数据库放在 Git 忽略的 `data/` 下，用 `querypilot data bird ...` 命令准备。两库题集用 `querypilot data bird select-databases --database-id toxicology --database-id student_club --output evals/bird_toxicology_student_club.json` 生成：来源文件的 sha256 必须和冻结 manifest 记录的一致，题集文件留在本地，旁边的 manifest 提交。冻结题的使用规则见 [ADR 0001](../adr/0001-frozen-set-for-component-eval.md)。

## 两种跑法

| 命令 | 链路 | 说明 |
|---|---|---|
| `eval direct --provider model` | 问题加完整 Schema，直接让模型生成 SQL | 模型基线 |
| `eval direct --provider fixture` | 把金标准 SQL 当作预测 | **只用来验证评测管线**，分数不能当成模型能力 |
| `eval graph` | 走 LangGraph 四节点查询图：读结构 → 生成 → 安全检查 → 只读执行 | 和线上查询同一条链路 |

`eval graph --workers N` 同时跑 N 道题（默认 1）：工作线程里只跑查询图和金标准 SQL，结果比对、逐题记录和进度事件都在主线程，报告仍按题集顺序。单题出错照常记为 error 并继续；主线程出错或被 Ctrl+C 中断后不再开始新题。图评测每题的耗时从这道题开始跑图量到金标准 SQL 执行完，不含排队和结果比对；`workers` 大于 1 时会被同时进行的请求拉高，**耗时只在线程数相同的运行之间比较**；线程数记在运行配置和报告里。

`eval graph --context-source` 选模型收到的 Schema 从哪里来（Context Source，编译图时选定，见 [GLOSSARY](../../GLOSSARY.md)）：`schema`（默认）是每张表的表名和列名；`ddl` 是这个库全部用户表的 CREATE TABLE 原文，按表名排序、空一行分隔，从 SQLite 的 `sqlite_master` 现场读取。`knowledge` 是这个库在知识库里的全部知识，经组装和渲染（规则版本 `r5-schema-v1`）后交给模型，`--structure-format columns`（默认）把表结构写成字段目录、主键、外键三行，`ddl` 换成建表语句原文；它需要 `--retrieval-cases <检索题集>`，从中取各库的 Knowledge Scope，题集用到的库缺 Scope 时在开始前报错。`retrieval` 用问题原文在这个库的 Scope 里检索一次（`--method` 默认 `hybrid`，`--rerank / --no-rerank` 默认重排，`top_k` 固定为 10），只把命中的表（加上它们的表 Chunk）渲染给模型，结构写法和 `--retrieval-cases` 与 `knowledge` 相同；没有命中或检索失败的题记为 error，`error_type` 是检索错误的类别（如 `no_match`、`embedding_error`），然后继续下一题。`retrieval` 的每道题另记 `tables_complete`：这道题 Relevant Set 的表（取自 `--retrieval-cases`，按题号对应，缺题号时在开始前报错）是否都出现在 Hit 所属的表里，检索失败的题记为没拿全；报告的 `tables_complete` 按"拿全 / 没拿全"分别给出题数、执行匹配数和比例，整体和分库各一份。用来分清 SQL 写错是检索没给全，还是给全了模型仍然写错。vector、hybrid 和重排会调用付费服务。所有来源下安全检查允许的都是这个库的全部用户表。Context Source 记在运行配置和报告里，`knowledge` 和 `retrieval` 另记结构写法、渲染版本和各库的 Scope，`retrieval` 再记 `method`、`rerank`、`top_k` 和实际用到的检索版本；建表语句、知识正文和 Hit 本身不写进日志和运行记录。

`--evidence-mode oracle` 会把 BIRD 的人工提示（evidence）交给模型。这是隔离实验，不代表系统真实的能力，默认为 `none`。

## 计分

每道题只会落进一类：

- **matched**：预测 SQL 的执行结果和金标准一致。比较时忽略行的顺序，但保留重复行。
- **mismatched**：能执行，但结果不一致。
- **error**：没有生成预测、被安全检查拒绝，或者执行报错。

`failure = mismatched + error`。另外单独统计 `valid_sql`（能通过检查并执行的 SQL 数）和 `no_prediction`，它们是诊断用的计数，不能和上面三类直接相加。如果所有题都没有预测，执行匹配率记为空（`null`），不记成 0%。

**这是项目自己的"受行数上限影响的执行匹配"，不等于官方 BIRD EX。** 结果受执行上限影响，比较不同实验时必须固定行数上限、超时和字节上限。

## 报告与可追溯

- 报告记录题集 sha256、模型、提示词版本（`direct-v2`）、图版本（`single-v2`，`schema` 来源的行为与 `single-v1` 相同）、Context Source、evidence 模式、执行上限、逐题结果，以及 P50/P95 耗时。
- **token 用量**取自模型接口的返回，不另外用 tokenizer 估算：逐题记录里的 `prompt_tokens`（提示词）和 `completion_tokens`（模型输出，推理模型的推理 token 也算在内），报告的 `token_usage` 给出两项各自的题数、总数、平均数和中位数。只对有值的题统计：fixture 不调用模型，没有用量；接口没返回用量的题记为空。
- 详细结果包含问题和预测 SQL，放在运行目录的 `private/` 下。`eval export-public` 按字段白名单导出一份可以分享的副本（包括线程数、Context Source、token 用量和每题的冻结标记），但里面仍然有 `predicted_sql`，分享前要检查。
- 对比实验要固定题目、数据库快照、模型、提示词、检索配置和评测器，并保存逐题结果。只靠 12 题，不能支撑准确率结论。

## 已有结果

| 日期 | 设置 | 结果 |
|---|---|---|
| 2026-09 | direct，gpt-5.5，`direct-v2`，12 题开发集，无 evidence | 执行匹配 4/12（33%），12/12 都是有效 SQL |
| 2026-10-02 | graph，gpt-5.5，`single-v1`，冻结 100 题，无 evidence，行数上限 10000 / 超时 20s | **30 matched / 66 mismatched / 4 error，执行匹配 30%**，有效 SQL 96%，逐题 P50 14.1 秒、P95 54.4 秒 |

这两次都还没有接入检索：模型看到的是这个库的完整 Schema。检索接入查询图之后，会在同一组题上重新对比。

### R5 前四组：无知识与全量知识（2026-11-17）

两库 88 题（题集 sha256 `673c8a45…`），graph，`gpt-6.1-sol`，`direct-v2`，`single-v2`，渲染规则 `r5-schema-v1`，无 evidence，行数上限 10000 / 超时 20 秒，4 个线程。每组跑一次，没有出错的题。

| 组 | Context Source | 执行匹配 | toxicology | student_club | 非冻结 72 题 | 有效 SQL | prompt_tokens 中位数 / 平均 | 整组耗时 |
|---|---|---|---|---|---|---|---|---|
| ① | `schema`（表名、列名） | 42/88 | 15/40 | 27/48 | 36/72 | 88/88 | 218 / 177 | 3.6 分 |
| ② | `ddl`（建表语句） | 43/88 | 15/40 | 28/48 | 35/72 | 88/88 | 564 / 473 | 3.6 分 |
| ③ | `knowledge`，`columns` 写法 | 44/88 | 17/40 | 27/48 | 35/72 | 88/88 | 2505 / 1910 | 3.9 分 |
| ④ | `knowledge`，`ddl` 写法 | 46/88 | 19/40 | 27/48 | 37/72 | 88/88 | 2562 / 1979 | 2.9 分 |

读法（±2 道题以内当作噪声，逐题只看 72 道非冻结题）：

- 四组之间的差距都很小。只有 ④ 比 ① 多 4 道，但非冻结题只多 1 道（36 → 37），多出的主要在冻结题上。所以这一轮**不能说全量知识让模型写得更准**。
- 逐题的变动比净变化大得多：相对 ①，②③④ 在非冻结题上各自新对 5–6 道、新错 5–6 道。同一组配置重跑一次会变多少还没有测过，这些翻转里有多少是模型本身的随机性，目前分不清。
- 72 道非冻结题里有 29 道四组都错，给模型更多结构和知识都没有帮上这些题。
- 分库看，toxicology 从 15 升到 17、19，student_club 一直在 27–28。
- ① 和 ③ 不对称：① 只有表名和列名，没有主键和外键；③ 的 `columns` 写法带着字段目录、主键、外键三行。③ 比 ① 多的不只是业务知识。
- 全量知识让每题输入约为 ① 的 11 倍（平均 1910 对 177 token），输出反而变少。按用户给的价格四组合计约 0.44 元。

这一轮没有根据结果调整渲染规则、提示词或题集。

### R5 结果：六组 Context Source（2026-11-17）

在上面四组之后补跑检索组：配置与前四组相同（同一份题集、模型、提示词、执行上限和线程数），另加检索题集 `evals/retrieval/retrieval_cases.json` 取 Scope 和 Relevant Set；检索 `top_k=10`，正式结果用 hybrid + 重排（Embedding `Qwen/Qwen3-Embedding-8B`，重排 `Qwen/Qwen3-Reranker-8B`，检索配置版本 `r4-hybrid-v1+r4b-rerank-v1`），lexical（`r3-bm25-v1`）各跑一次作为额外的行。每组只跑一次。

| 组 | Context Source | 执行匹配 | toxicology | student_club | 非冻结 72 题 | 有效 SQL | prompt_tokens 中位数 / 平均 | 每题 p50 | 整组耗时 |
|---|---|---|---|---|---|---|---|---|---|
| ① | `schema`（表名、列名） | 42/88 | 15/40 | 27/48 | 36/72 | 88 | 218 / 177 | 7.7 秒 | 3.6 分 |
| ② | `ddl`（建表语句） | 43/88 | 15/40 | 28/48 | 35/72 | 88 | 564 / 473 | 7.7 秒 | 3.6 分 |
| ③ | `knowledge`，`columns` | 44/88 | 17/40 | 27/48 | 35/72 | 88 | 2505 / 1910 | 6.6 秒 | 3.9 分 |
| ④ | `knowledge`，`ddl` | 46/88 | 19/40 | 27/48 | 37/72 | 88 | 2562 / 1979 | 6.3 秒 | 2.9 分 |
| ⑤ | `retrieval`，`columns`，hybrid + 重排 | 44/88 | 18/40 | 26/48 | 36/72 | 88 | 720 / 712 | 11.0 秒 | 5.4 分 |
| ⑥ | `retrieval`，`ddl`，hybrid + 重排 | 43/88 | 18/40 | 25/48 | 35/72 | 88 | 776 / 760 | 10.1 秒 | 4.6 分 |
| ⑤a | `retrieval`，`columns`，lexical | 41/88 | 17/40 | 24/48 | 34/72 | 88 | 782 / 788 | 5.5 秒 | 2.4 分 |
| ⑥a | `retrieval`，`ddl`，lexical | 44/88 | 20/40 | 24/48 | 36/72 | 87 | 850 / 838 | 5.6 秒 | 2.6 分 |

检索组按"Relevant Set 的表是否都在 Hit 所属的表里"拆开：

| 组 | 拿全：题数 / 执行匹配 | 没拿全：题数 / 执行匹配 |
|---|---|---|
| ⑤ hybrid + 重排，columns | 79 / 43（54%） | 9 / 1（11%） |
| ⑥ hybrid + 重排，ddl | 79 / 42（53%） | 9 / 1（11%） |
| ⑤a lexical，columns | 74 / 38（51%） | 14 / 3（21%） |
| ⑥a lexical，ddl | 74 / 41（55%） | 14 / 3（21%） |

结论（±2 道题以内当作噪声，逐题只看 72 道非冻结题）：

- **检索到的知识能不能帮模型写对 SQL？** 和不给知识（①）相比没有量得出的提升：六组在非冻结题上都在 35–37 道之间。但检索也没有变差：⑤⑥ 和全量知识（③④）持平，输入只有全量知识的约 37%（平均 712 对 1910 token）。逐题看，全量知识答对、检索答错的非冻结题只有 2–3 道（⑤ 2 道、⑥ 3 道，其中 1411 是表没拿全），反方向也有 1–3 道。
- **知识内容有没有用？** 这一轮量不出来：③④ 与 ①② 的差在噪声内。72 道非冻结题里有 29 道六组都错，给模型更多结构和知识帮不上这些题。
- **建表语句是不是关键？** 不是：四对"ddl 写法 − columns 写法"的差是 +1、+2、−1、+3（非冻结 −1、+2、−1、+2），都在噪声内。
- **表没拿全的题大多做错**：hybrid + 重排有 9 道题缺表，只对了 1 道；拿全的 79 道对了 54%。这 9 道都在 student_club。拿全率与检索评测的 Complete Recall@10 一致（hybrid + 重排 89.8%，lexical 84.1%）。缺表的题也可能本身更难，这里只说明相关，不说明因果。
- **代价**：hybrid + 重排让每题 p50 从全量知识的约 6.5 秒升到 10–11 秒（多了 Embedding 和重排两次服务调用）；lexical 的 p50 约 5.5 秒，比全量知识还快。
- **前提**：toxicology 只有 20 个 Chunk，`top_k=10` 已经是一半，所以 toxicology 的表在 hybrid + 重排下全部拿全；每组只跑一次，同一配置重跑的波动没有测过。

八次运行的模型费用合计约 0.77 元（按用户给的价格，检索组每次约 0.08 元），另有硅基流动的 Embedding 和重排各 176 次调用。这一轮没有根据结果调整 `top_k`、渲染规则、检索配置或提示词。
