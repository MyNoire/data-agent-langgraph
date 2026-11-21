# 06: 构建并导入两份新 Corpus

**What to build:** toxicology 和 student_club 各有一份带取值样例和数据约定的新 Corpus，导入现有知识库的现有表，与旧 Corpus 并存（靠 Knowledge Scope 区分，旧行不动），并有当前 Embedding Profile 的向量。用新 Corpus 生成一份新的检索题集，题号和 Relevant Set 不变，只有 Scope 不同。这是操作性工单，不写新功能代码。

**Blocked by:** 03, 04

**Status:** ready-for-agent

- [x] 两个库各生成取值样例文件，构建新 Corpus 并通过 `corpus verify`；`corpus_id` 与旧 Corpus 不同
- [x] 抽查几个列 Chunk 和表 Chunk 的正文：取值、数据约定和隐私列的处理符合预期（Comments 只记结论，不贴隐私值）
- [x] **征得用户同意后**导入正式知识库并 `store verify` 读回核对；旧 Corpus 的行数和内容不变
- [x] 核对 BM25 索引段的 `num_docs` 之和等于表的行数（见 pg_search 维护文档）
- [x] **征得用户同意后**计算向量（付费），Comments 记录调用量
- [x] 用 `eval retrieval-cases --corpus-dir`（指向两份新 Corpus）生成新的检索题集，留在本地，manifest 提交；旧题集不动
- [x] Comments 记录两份新 Corpus 的 `corpus_id`、Scope、产物位置和使用的命令
- [x] 知识库文档的快照表补上新 Corpus 的 Chunk 数

## Comments

**2026-11-20 完成**（用户 2026-11-20 同意导入和付费向量：「开始做工单06即可」）

| 库 | `corpus_id` | `schema_version` | 产物 |
|---|---|---|---|
| toxicology | `50c156f8700b7312ea4391384c2a4f7cc64e750e6457edd7815463e08560a768` | `45b2e99ae191cbcde4fa377e702dcd498c98abaf0f7193565861601d653ba799` | `data/rag/toxicology/values-v1/`、`data/rag/toxicology/values-corpus-v1/` |
| student_club | `748167d2fce64e4575d9de96495a4ed0ac1b8c2bbdd1b3dd07f873751b3092b7` | `20441054b528d9e52458069d101e8290f2698b873886d66f2e7a9a52803fe6c1` | `data/rag/student_club/values-v1/`、`data/rag/student_club/values-corpus-v1/` |

命令（每个库各一遍；curated 目录 toxicology 为 `data/rag/toxicology/r1-2-curated-v1`，student_club 为 `data/rag/student_club/curated-v1`）：

```text
querypilot knowledge catalog values --database-id <db> --semantic-file config/knowledge_semantics/<db>-v2.json --output data/rag/<db>/values-v1/<db>-values.json
querypilot knowledge corpus build --database-id <db> --curated-dir <curated> --semantic-file config/knowledge_semantics/<db>-v2.json --values-file data/rag/<db>/values-v1/<db>-values.json --output-dir data/rag/<db>/values-corpus-v1
querypilot knowledge corpus verify --corpus-dir data/rag/<db>/values-corpus-v1
querypilot knowledge store import --corpus-dir data/rag/<db>/values-corpus-v1
querypilot knowledge store verify --corpus-dir data/rag/<db>/values-corpus-v1
querypilot knowledge store embed --corpus-id <corpus_id> --database-id <db> --schema-version <schema_version>
querypilot eval retrieval-cases --questions D:/AgentStudy/minidev_0703/minidev/MINIDEV/mini_dev_sqlite.json --frozen evals/bird_final100.json --corpus-dir data/rag/toxicology/values-corpus-v1 --corpus-dir data/rag/student_club/values-corpus-v1 --output evals/retrieval/retrieval_cases_values.json
```

- 构建与校验：两份都 `verified`，五项检查全部通过；Chunk 数与旧 Corpus 相同（20、64）。
- 抽查：toxicology 3 张表带数据约定、11 列全部带取值；student_club 5 张表带数据约定、44 / 48 列带取值，没带的 4 列正好是隐私名单上的列（metadata 里 mode 为 private）；约定的 metadata 来源都是 observed。
- 隐私核对：把 4 个隐私列的全部真实取值（长度 ≥ 4 的 130 个）拿到新 Corpus 和取值文件里逐个查找。命中的只有两类，都不是取值泄露：一个会员的姓恰好和 event.location 里的街道名相同；BIRD 原始字段说明里有一个举例用了一位会员的姓名，旧 Corpus 里就有，不是取值带进来的（规格不改 BIRD 原有说明，这里只记下）。
- 导入：两份都 `imported`，`store verify` 读回一致。两份旧 Corpus 在导入前后各 `store verify` 一次，`projection_digest` 和 `timestamps_digest` 都没变（toxicology `049e9d41…`/`09ebec64…`，student_club `06272ac5…`/`8d3716b1…`）。
- BM25 索引：3 个段的 `num_docs` 之和 168，等于 `knowledge_chunks` 的行数，`num_deleted` 为 0。四份 Corpus 各 20 / 64 / 64 / 20 行。
- 向量（付费）：Embedding Profile `siliconflow:Qwen/Qwen3-Embedding-8B:4096:content-v1:query-instruct-v1`，toxicology 20 个、student_club 64 个 Chunk，按每批 32 个共 3 次调用。运行记录 `artifacts/runs/knowledge_embed/2026-11-20/52a13cf0…`、`072eff39…`。
- 新题集：`evals/retrieval/retrieval_cases_values.json`（本地），manifest `evals/retrieval/retrieval_cases_values_manifest.json`（提交）。问题文件用的是生成旧题集时的同一份（hash `def4b2b4…`，项目内 `data/bird/` 那份字节不同）。与旧题集相比，去掉 Scope 后完全相同；manifest 只有题集文件名、题集 hash 和 Scope 不同。88 题（toxicology 40、student_club 48），其中冻结 16 题。
