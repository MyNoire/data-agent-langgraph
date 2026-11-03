# 04: student_club 的 Corpus

**What to build:** 知识库里多一个讲会员、活动、预算的业务库 student_club，可以被检索。构建流程和 Build Profile 都与 toxicology 一致。

**Blocked by:** None (can start immediately)

**Status:** done

- [x] Claude 起草 Data Semantics：每张表的中文用途、一行代表什么、每个外键关系的含义。起草时只参考表结构和本地完整 BIRD mini-dev 里的字段说明 CSV，不看题目、evidence 和金标准 SQL
- [x] **检查点：用户审阅 Data Semantics 并通过**，之后才能构建
- [x] 走现有命令完成抽取 → 丰富 → 修正 → 构建（`r1-4-v1`）→ 校验；修正计划只处理字段说明 CSV 本身的问题
- [x] 导入正式知识库，建好 BM25 索引，回读核对通过
- [x] 用一两条手写查询经 `retrieve_knowledge` 确认能检索到
- [x] 原始数据和 Corpus 产物不进 Git；记录构建配置和产物位置

## Comments
- 2026-11-03 完成：
  - 来源：`minidev_0703/.../student_club/database_description`。原始 Budget.csv 不是 UTF-8（event_status 说明里有 3 个 0x95 字节），按 Windows-1252 转码后放进本地来源副本 `data/rag/student_club/source-utf8/`（附 candidate_manifest.json）；其余 7 个文件和原始字节一致。
  - Data Semantics `config/knowledge_semantics/student_club-v1.json`、修正计划 `config/catalog_corrections/student_club-v1.json`：用户审阅通过（"没什么问题，通过"）。修正只有 1 条：zip_code.type 里的 U+FFFD 换成项目符号。
  - 产物（Git 忽略）：`data/rag/student_club/{catalog.json, enriched-v1, curated-v1, r1-4-corpus}`。
  - Corpus：corpus_id `66f8085b7bc58d153f8eb67c8001360975b82f82772bd9515ede0b0c773c3e3d`，schema_version `20441054b528d9e52458069d101e8290f2698b873886d66f2e7a9a52803fe6c1`；8 个 Document，64 个 Chunk（table 8 / column 48 / relationship 8），8 条 Relation。
  - `corpus verify` 的 5 项检查全部通过；`store import` 为 imported；`store verify` 回读核对为 verified；`check-search` 确认 BM25 索引正常。
  - 手写查询通过 `retrieve_knowledge` 检索：`budget remaining` 排第 1 的是 budget.remaining 列；`会员 专业` 排第 1 的是 major 表。
