# 05: 第一次基线评测

**What to build:** 得到一组可以追溯的 BM25 检索基线数字：toxicology 和 student_club 共 88 题，英文原题，不带 evidence。

**Blocked by:** 01, 03, 04

**Status:** done

- [x] 生成两个库的完整题集；manifest 提交进 Git
- [x] 在正式知识库上运行检索评测，记录配置、命令和产物位置
- [x] 用中文总结两个库的主指标、Hits to Cover 和 Column Recall@10，以及非冻结题里的典型失败类型
- [x] 按 ADR 0001，这一张只分析，不修改知识、参数或代码；改进作为下一项工作

## Comments

### 运行记录（2026-11-06）

- 题目来源：`D:/AgentStudy/minidev_0703/minidev/MINIDEV/mini_dev_sqlite.json`（sha256 `def4b2b4…`），冻结清单 `evals/bird_final100.json`。
- 生成题集：`querypilot eval retrieval-cases --questions <上面的文件> --frozen evals/bird_final100.json --corpus-dir data/rag/toxicology/r1-4-corpus --corpus-dir data/rag/student_club/r1-4-corpus --output evals/retrieval/retrieval_cases.json`。88 题（toxicology 40、student_club 48），冻结 16 题；题集 sha256 `6014f798…`（与 2026-11-04 抽查一致），manifest 已提交。
- 运行评测：`querypilot eval retrieval --cases evals/retrieval/retrieval_cases.json`。正式知识库，BM25（`lexical` / `r3-bm25-v1`），top_k=20，K=5/10，Column K=10。
- 产物：`artifacts/runs/evaluation/2026-11-06/3c83f0afc2de4befb16035186fbd7ed9/`（`result.json`、`events.jsonl`，本地，Git 忽略）。

### 基线数字（含冻结题，按 ADR 0001 一起计分）

| | 整体（88） | toxicology（40） | student_club（48） |
|---|---|---|---|
| Table Complete Recall@5 | 64.8% | 72.5% | 58.3% |
| Table Complete Recall@10 | 84.1% | 92.5% | 77.1% |
| Table Recall@5 | 83.5% | 88.8% | 79.2% |
| Table Recall@10 | 92.8% | 97.5% | 88.9% |
| Hits to Cover 中位数 / 平均数 | 3 / 4.9 | 3 / 3.9 | 4 / 5.8 |
| 拿不全的题（20 个 Hit 内） | 3 | 1 | 2 |
| Column Recall@10 | 67.5% | 75.8% | 60.5% |
| 检索耗时 P50 / P95 | 276 / 334 ms | 273 / 328 ms | 280 / 339 ms |

input_error 0 题；没有相关列的题 0 道。

### 失败分析（只看非冻结题）

非冻结 72 题（toxicology 32、student_club 40）中，Complete Recall@5 没达标的 24 题（toxicology 8、student_club 16），其中 13 题在 K=10 时达标。为看清排在前面的是什么，对这 24 题重新做了只读检索，结果只在本地查看、没有保存。典型类型：

1. **英文词形对不上（已验证）**：BM25 索引用 `jieba` / `simple` 分词，没有英文词干化。探测查询 `element` 命中 `atom.element`，`elements` 零命中；`event` 和 `events` 同样如此。问题里的复数词因此匹配不到表名和列名（如 #1322、#1323、#207）。
2. **问题提到的是数据值，不是结构词**：职位（President、Vice President → `member.position`）、人名（→ `member` 的姓名列）、活动名（→ `event.event_name`）。知识库里只有表和列的说明，没有值层面的知识，探测查询 `president` 零命中（如 #1331、#1356、#1357、#1411、#1338、#1359、#1387）。
3. **一张表的列 Chunk 挤占前排**：toxicology 里 `bond` / `connected` 的多个 Chunk 排在前面，`atom` 第一次出现在第 6～7 个 Hit，K=10 时都能拿全（#201、#206、#215、#219、#247、#268）。student_club 也有类似情况（#1359 前 10 个 Hit 里有 7 个属于 `budget`）。
4. **英文常用词误配结构词**：句首动词 State 匹配到 `zip_code.state` / `short_state`，把它们顶到前两位（#1357、#1411）。
5. **只用于 JOIN 的桥接表**：问题里根本不会提到它（如 #1338、#1457 中的 `budget`），名次靠后。这正是"检索后的外键扩展"要解决的问题。

### 改进方向（下一项工作，本工单不改）

英文词干化或词形归一；值层面的知识（只能从数据库本身取，不能从题目取）；外键扩展补桥接表；限制同一张表在前 K 个 Hit 里的 Chunk 数，或者按 Document 聚合；向量检索补语义匹配。每一项都要用非冻结题评估，再用这份基线对比。
