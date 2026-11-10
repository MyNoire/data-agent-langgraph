# 04: 混合检索

**What to build:** 检索调用方用 `method=hybrid`，得到 BM25 和向量两路用 RRF 融合后的结果。一路漏掉的 Chunk 可以由另一路补上；BM25 无命中时，只用向量那一路；查询没有有效词时与 lexical 一样报 `input_error`。

**Blocked by:** 03

**Status:** ready-for-human

结对分工：RRF 纯函数由 Claude 用 TDD 写；hybrid 的组装流程由**用户写**（乒乓 TDD，Claude 先写失败的测试，审查实现）。

- [x] RRF 纯函数：输入若干路已排序的 Chunk 列表，分数为 Σ 1/(60 + rank)，rank 从 1 开始，两路权重相同，分数相同时按 `chunk_key` 排序；k 和权重固定不调
- [x] RRF 单元测试：两路都出现时分数相加；只在一路出现的 Chunk 也在结果里；同分时的排序；某一路为空
- [x] hybrid：两路各取 50 个候选，融合后取前 top_k 个；`score` 为 RRF 分
- [x] 查询没有有效词时 hybrid 与 lexical 一样报 `input_error`，不调用 Embedding 服务
- [x] 真实 PG 测试（假 embedder）覆盖：融合后的排序、BM25 无命中时的结果、没有有效词时报 `input_error`、`knowledge_types` 过滤、`embedding_missing`、`embedding_error`

## Comments

- 实现（2026-11-09 ~ 10）：79b0192 RRF 纯函数 `reciprocal_rank_fusion`（Claude，TDD，3 个单元测试，"某一路为空"用两种改坏方式验证过）；5c47928 `RetrievalMethod` 加入 `hybrid`、CLI `--method` 可选 hybrid；73bdece 常量 `CANDIDATES_PER_BRANCH = 50`。候选数与 `top_k` 分开：固定取 TopK 上限，任何 `top_k` 的前 N 个都与更大 `top_k` 的前 N 个相同，评测从 `top_k=20` 截取 K=5/10 才成立（用户问过是否由上层控制，决定先用常量，不进请求）。
- hybrid 组装由**用户写**（b08b9e4）。过程中出现过两个 bug，都被融合测试发现：`model_copy` 的返回值没接住（两路只取了 top_k 个），以及 hybrid 没算查询词（BM25 那一路安静地返回空）。
- 规格修改（2026-11-10，用户同意）：查询没有有效词时，hybrid 与 lexical 一样报 `input_error`，不调用 Embedding 服务，不再退化成只用向量。理由：向量检索对任何输入都返回 top_k 个结果，会把明确的输入错误变成看似正常的 Hit；无意义输入应由上层澄清或拒答；BM25 基线 88 题中没有一道 `input_error`。"BM25 有词但零命中时用向量的排序"保留，由 RRF 处理空的一路，不另写分支。
- 测试（ad7dbc2）：hybrid 零命中、无有效词各一个守护测试，都用"故意改坏代码"验证过；`knowledge_types`、`embedding_missing`、`embedding_error` 三个测试改为 vector 和 hybrid 各跑一遍，hybrid 的两个错误路径也做了改坏验证。
- 审查（两轴）：1eaa6d9 修了注释、docstring、测试名、文档引用；重构由 Claude 按用户要求完成：提炼 `_vector_rows`，vector 和 hybrid 共用；`uses_terms` / `uses_vectors` 取代同一判断的三种写法。讨论过策略模式：只有三个分支、各自参数不同，先提炼函数，方法多到五六种或需要按配置组合时再换成分派表。`result_version` 不再升级：v2 只在本分支，master 仍是 v1。
- 测试结果：`pytest tests/integration --require-postgres` 103 passed；单元测试 441 passed，1 failed（`test_console_and_module_share_help`，`querypilot` 不在 PATH 上，与本工单无关）。工单 04 没有真实调用；三种方法在正式 Corpus 上的对比由工单 06 完成。
