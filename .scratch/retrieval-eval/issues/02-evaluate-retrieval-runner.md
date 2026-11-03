# 02: 检索评测运行器外层

**What to build:** 开发者用一条命令对一份检索题集跑检索评测：逐题调用检索（英文原题、题集里的 Knowledge Scope、top_k=20、不限知识类型），得到一份带运行记录的报告。这一张做到计分入口为止，报告里先只有逐题结果、耗时和配置，还没有指标。

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] 公开入口 `evaluate_retrieval(cases, settings, *, retriever=..., run_context=None)`，形状参照 SQL 评测的 `run_direct`；检索器可以替换
- [ ] 在 `eval` 下新增运行检索评测的 CLI 命令，运行记录沿用 `task_run`
- [ ] `input_error` 记到这道题上，计分时按空的 Hit 列表处理；其他检索错误中止整批，并保留原因
- [ ] 报告配置包括题集 sha256、各库 Knowledge Scope、检索方法、检索配置版本、top_k、表级 K 列表、列级 K、Relevant Set 的来源说明
- [ ] 报告中没有问题原文、SQL 和 Hit 正文
- [ ] 留出计分入口，交给 03 实现
- [ ] 测试用假检索器，不连 PG；另有一条真实 PG 的端到端测试（原创小夹具 Corpus 加两三道小题）
- [ ] 在真实 PG 上对 toxicology 题集跑通一次

## Comments
