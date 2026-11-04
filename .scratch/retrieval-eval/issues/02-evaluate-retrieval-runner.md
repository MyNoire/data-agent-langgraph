# 02: 检索评测运行器外层

**What to build:** 开发者用一条命令对一份检索题集跑检索评测：逐题调用检索（英文原题、题集里的 Knowledge Scope、top_k=20、不限知识类型），得到一份带运行记录的报告。这一张做到计分入口为止，报告里先只有逐题结果、耗时和配置，还没有指标。

**Blocked by:** 01

**Status:** done

- [x] 公开入口 `evaluate_retrieval(cases, settings, *, retriever=..., run_context=None)`，形状参照 SQL 评测的 `run_direct`；检索器可以替换
- [x] 在 `eval` 下新增运行检索评测的 CLI 命令，运行记录沿用 `task_run`
- [x] `input_error` 记到这道题上，计分时按空的 Hit 列表处理；其他检索错误中止整批，并保留原因
- [x] 报告配置包括题集 sha256、各库 Knowledge Scope、检索方法、检索配置版本、top_k、表级 K 列表、列级 K、Relevant Set 的来源说明
- [x] 报告中没有问题原文、SQL 和 Hit 正文
- [x] 留出计分入口，交给 03 实现
- [x] 测试用假检索器，不连 PG；另有一条真实 PG 的端到端测试（原创小夹具 Corpus 加两三道小题）
- [x] 在真实 PG 上对 toxicology 题集跑通一次

## Comments
- 2026-11-04 完成（70c7ef5，以及审查后的修正提交）。
  - 和规格的两处差别已同步进 spec：`retriever` 只接收一个 `RetrievalRequest`，默认从 `.env` 读知识库只读配置；耗时取检索结果自带的 `elapsed_ms`，`input_error` 题记为空，P50/P95 不统计这些题。
  - 计分入口：`_scoring.score_case(relevant_tables, relevant_columns, hits)` 和 `summarize(scores)`，结果放在逐题结果和汇总各自的 `scores` 字段下。目前两个函数都返回空，工单 03 来填。
  - 审查后的修正：计分结果单独放进 `scores` 子字段，避免和运行器字段重名后被覆盖；`score_case` 不再接收原题；运行配置只在 `retrieval_run_config` 定义一次；题集只读一次；测试补上中止时 `run.json` 里 `error_type` 的断言。`TOP_K` 和 `COLUMN_K` 有意保持分开：前者是检索预算，后者是列召回的截断点（COLUMN_K ≤ TOP_K）。
  - 真实运行：在正式知识库上跑了 88 题，运行 ID `artifacts/runs/evaluation/2026-11-04/6fd5201441934708b1d800d23d765acd`。0 道 input_error，检索耗时 P50 约 270ms、P95 约 337ms；result.json 和 events.jsonl 中都没有原题。
  - 测试：单元测试 419 passed、1 failed。失败的是 `test_console_and_module_share_help`，原因是 PATH，改动前就会失败。集成测试（`--require-postgres`）61 passed。
