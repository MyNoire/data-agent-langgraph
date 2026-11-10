# 03: 向量检索

**What to build:** 检索调用方用 `method=vector` 在一个 Knowledge Scope 里做语义检索：对查询嵌入一次，在当前 Embedding Profile 的向量里按余弦距离做精确全表扫描。结果写明方法和 Profile。`lexical` 的行为不变，也不调用 embedder。

**Blocked by:** 02

**Status:** ready-for-human

结对分工：按 `method` 分流的流程由**用户写**（乒乓 TDD，Claude 先写失败的测试，审查实现）；向量检索 SQL、请求和结果模型的改动由 Claude 写。

- [x] `RetrievalRequest.method`：`lexical` / `vector` / `hybrid`，默认 `lexical`
- [x] `retrieve_knowledge(request, settings, embedder=None)`：非 lexical 且没传 embedder 时按配置创建；lexical 不创建，也不调用
- [x] vector：在同一个只读 Scope 会话里先检查当前 Profile 的向量是否存在，不存在报 `embedding_missing`；再嵌入查询，按 Scope、Profile 和 `knowledge_types` 过滤，按余弦距离排序；`score` 为余弦相似度；相同时按 `chunk_key`、`chunk_id` 排序
- [x] embedder 抛错时报 `embedding_error`，不退回 BM25
- [x] `RetrievalResult.method` 是实际用的方法，增加 Embedding Profile 字段（lexical 时为空），结果版本号升级
- [x] 真实 PG 测试（假 embedder）覆盖：相似度排序、`knowledge_types` 过滤、`embedding_missing`、`embedding_error`、lexical 不调用 embedder，原有 lexical 测试全部通过
- [x] 单条检索的 CLI 增加 `--method`
- [x] 征得用户同意后，在正式 Corpus 上用 `--method vector` 跑几条非冻结题，看看效果

## Comments

- 范围说明：`RetrievalMethod` 目前只有 `lexical` / `vector`，`hybrid` 随工单 04 的实现一起加入。单条检索 CLI 原本只有参数解析、没有接到 `retrieve_knowledge`，本工单只加了 `--method` 参数；真实试跑改用一小段 Python 直接调用 API。
- 实现（2026-11-08 ~ 09，乒乓 TDD）：ac9e6fd 请求的 `method`、结果的 `method` / `embedding_profile_id`（结果版本 v2，`score` 去掉 `ge=0`，余弦可为负）、向量 SQL（Claude）；按 `method` 分流由**用户写**：b13c1bb 余弦排序与 `embedding_missing`，678aab0 `embedding_error`（只接 `EmbeddingError`，不退回 BM25），1bea20e 没传 embedder 时按配置创建（lexical 不创建）；28977c0 守护测试（`knowledge_types` 过滤、全标点的 vector 查询、lexical 不调用也不创建 embedder），三个都用"故意改坏代码"确认会失败。
- 审查（两轴）：已改 `KnowledgeRetrievalError` docstring、`docs/architecture/modules.md`、测试假 Embedder 的导入与类型标注。记下不改的：
  - `RetrievalResult.retrieval_config_version` / `normalization_version` 是 BM25 的配置版本，vector 结果里也带着，评测运行器会把它记成来源信息。留到工单 05 处理评测输出时一起决定。
  - `embed_query` 在只读事务里调用（规格要求先查向量再嵌入），事务最长会多开约 60 s 超时。
  - embedder 返回全零查询向量时余弦为 NaN，报的是 pydantic 错误而不是 `KnowledgeRetrievalError`。真实服务返回单位向量，暂不处理。
  - 缺少 `QUERYPILOT_EMBEDDING_*` 配置时抛 pydantic `ValidationError`，暂不转换，等真遇到再处理。
- 测试：`pytest tests/integration --require-postgres` 97 passed；单元测试 438 passed，1 failed（`test_console_and_module_share_help`，`querypilot` 不在 PATH 上，与本工单无关）。
- 真实试跑（2026-11-09，用户同意，共 6 次 Embedding 调用，其中 2 次因试跑脚本打印出错重跑）：4 道**非冻结**题，取自检索评测失败分析，lexical 与 vector 各取前 5，结果只在终端查看，没有写文件。

  | 题 | 相关表 | lexical 前 5 | vector 前 5 |
  |---|---|---|---|
  | Q207 "What elements are in a double type bond?" | atom, bond, connected | 只有 bond、connected，**没有 atom**（词形问题） | bond、connected、**atom（第 5）**，三张都有 |
  | Q201 "percentage of carbon in double-bond molecules" | atom, bond | bond 的列占满前排，没有 atom | bond、connected、molecule，**也没有 atom** |
  | Q1331 "…funds that the Vice President received?" | income, member | income、budget，没有 member | income 表第 1，有 `link_to_member` 列，没有 member 表 |
  | Q1357 "State the date … paid his/her dues." | income, member | **zip_code.state 第 1**（误配） | income 表第 1，没有 zip_code；有 income→member 关系块，没有 member 表 |

  观察：vector 解决了词形（Q207）和常用词误配（Q1357）两类问题；数据值类（Vice President）仍没找到 member 表。vector 的分数集中在 0.46 ~ 0.68，和 BM25 分数不在一个量纲，工单 04 用 RRF 按名次融合正合适。同一查询三次嵌入，余弦有约 0.001 的抖动，名次靠近的 Hit 可能互换，工单 05/06 比较时需要注意。vector 每次 1.4 ~ 4.2 s，大部分是 API 调用；lexical 0.17 ~ 0.47 s。4 道题只是举例，不是评测结论。
