# 05: 评测支持 `--method`

**What to build:** 评测开发者用 `querypilot eval retrieval --method <lexical|vector|hybrid>` 选择检索方法，方法和 Embedding Profile 写进运行配置和报告，指标和 K 都不变。

**Blocked by:** 03

**Status:** ready-for-agent

- [x] `evaluate_retrieval` 和 CLI 增加 `--method`，默认 `lexical`；每个检索请求都带上这个方法
- [x] 运行配置和报告记录方法和 Profile（lexical 时为空）
- [x] 沿用假检索器的运行器测试：方法传进每个请求，并写进运行配置和报告
- [x] 检索评测文档写上 `--method` 的用法

## Comments

- 实现（2026-11-10，Claude，TDD）：7839025 `retrieval_config_version` 按方法给出（用户选择）：lexical 仍为 `r3-bm25-v1`，与 BM25 基线可比；vector 为 `r4-vector-v1`，hybrid 为 `r4-hybrid-v1`（RRF k=60、等权、每路 50 个候选）。含义写在 `RETRIEVAL_CONFIG_VERSIONS` 旁，改 RRF 参数就升 hybrid 的版本号。它是由 `method` 推出的 computed_field，不能和 method 对不上。86aaf54 `evaluate_retrieval(method=...)` 和 `eval retrieval --method`，默认 lexical。运行配置白名单加入 `method`。默认检索器在 vector 和 hybrid 时整次运行只建一个 Embedder，lexical 不建。321effb 补了文档（用法、费用、来源字段）。
- 与原文的一处差别：运行配置（`config.json`）只记 `method`。Profile 记在报告的 `config.retrieval` 里，取自每道题结果实际用的 Profile 和配置版本。原因：运行配置在开始时写，那时注入的检索器（测试的假检索器）拿不到 Profile。失败的运行没有分数需要追溯，成功的报告都带着 Profile。
- 审查（两轴）：6e7aefd 运行配置只在一处构造。记下不改的几点：
  - CLI 的 `choices` 和 `RetrievalMethod` 各写了一份方法列表。`cli.py` 顶层不导入业务模块，不为此引入导入。
  - "哪些方法需要 Embedder"在检索和评测里各判断一次。
  - 评测 CLI 没把 Embedding key 放进 `secrets`，现有路径不会写出它：失败只记 `error_type`，配置走白名单，`EmbeddingSettings` 隐藏输入。只读 DSN 也是同样处理。
  - 结果 dump 后带着 `retrieval_config_version`，在 `extra=forbid` 下不能再读回 `RetrievalResult`。目前没有地方读回。
- 测试：`pytest tests/integration --require-postgres` 103 passed；单元测试 442 passed，1 failed（`test_console_and_module_share_help`，`querypilot` 不在 PATH 上，与本工单无关）。没有调用 API；三种方法的真实运行在工单 06 做。
