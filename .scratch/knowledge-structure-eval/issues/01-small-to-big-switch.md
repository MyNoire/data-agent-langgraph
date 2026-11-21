# 01: Small-to-Big 开关与 `eval graph --small-to-big`

**What to build:** 评测者可以用 `eval graph --context-source retrieval --small-to-big` 跑一组 SQL 评测：检索照常进行，进入上下文的表与不开开关时相同，但每张被命中的表以完整的 Document 交给模型，渲染与全量知识（`knowledge` 来源）里这张表逐字相同，表的先后仍按检索名次。开关写进运行配置、报告和 `export-public`；默认关闭，关闭时一切与今天逐字相同。在非检索来源上打开开关会被拒绝。见 [spec](../spec.md)「实现决定 / 查询图」「图评测」。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [x] 查询图有编译参数 `small_to_big`（默认 `False`）；打开时命中表的全部 Chunk 由可注入的全量读取函数从当前 Knowledge Scope 读出，按 `hit_table` 只保留命中的表，表内顺序同全量读取，表之间按 Hit 第一次出现的顺序
- [x] 测试：假检索命中两张表的部分列，生成器收到的 Schema 只含这两张表，每张表与 `knowledge` 来源对同一份 Chunk 渲染出的对应表逐字相同，先后按检索名次
- [x] 测试：只命中某张表的关系 Hit，这张表同样展开
- [x] 测试：`ddl` 写法下展开的表使用建表语句
- [x] 测试：关闭开关时输出与今天逐字相同（现有检索来源测试保持通过）
- [x] 测试：`schema`、`ddl`、`knowledge` 来源上打开开关，编译时报 `ValueError`
- [x] `eval graph --small-to-big / --no-small-to-big`（默认关闭）；不带 `--context-source retrieval` 时在写任何运行记录前报错（有测试）
- [x] `small_to_big` 登记进运行配置字段白名单，写进报告和 `export-public`；用假生成器跑一次，`true` / `false` 两种情况都有测试
- [x] `SCHEMA_RENDER_VERSION`、`GRAPH_VERSION` 不变；`tables_complete` 计算不变
- [x] 查询图 docstring 写明新参数；SQL 评测文档写入 `--small-to-big` 的说明
- [x] 模块边界测试和全部单元测试（`--ignore=tests/integration`）通过，记录实际命令与结果

## Comments

- 2026-11-21 实现（Claude，TDD）：
  - 查询图：`build_query_graph(small_to_big=...)`；`retrieve` 节点打开开关时用 `knowledge_reader` 全量读出当前 Scope，交给查询模块里的纯函数 `expand_to_documents(hits, chunks)`：按 Hit 第一次出现的顺序取各表的全部 Chunk、表内保持全量读取的顺序、rank 重新编号，之后的 `assemble_context` 和 `render_context` 不变。此时不调用 `table_chunk_reader`。非 retrieval 来源上打开报 `ValueError`。
  - 评测：`--small-to-big / --no-small-to-big` → `run_graph(small_to_big=)` → 查询图；检查放在 `sql_run_config`（建运行记录之前），命令行没有另写一份。`small_to_big` 进 `CONFIG_FIELDS`、报告 `TOP_LEVEL` 导出白名单和 `EvalReport`；retrieval 来源总记 true/false，其他来源不记。
  - 测试：新文件 `tests/test_graph_small_to_big.py`（展开与检索表序、只命中关系也展开、ddl 写法、三种非 retrieval 来源报错）；`tests/test_graph_eval_retrieval.py` 加 3 个测试（run_graph 记录开关、CLI 开关生效、CLI 非 retrieval 来源开始前失败且无运行记录），并把"非检索来源不记检索配置"扩到 `small_to_big`。"只命中关系"和 ddl 两个测试写下时就通过（前一个测试的实现已覆盖），作为验收守护保留。
  - 命令与结果：`.venv/Scripts/python.exe -m pytest tests -q --ignore=tests/integration -p no:cacheprovider` → 604 passed, 1 skipped, 1 failed（`tests/contracts/test_cli.py::test_console_and_module_share_help`，PATH 上没有 `querypilot`，之前就有）。项目没有配置类型检查器。没有跑真实模型或检索。
- 2026-11-21 审查后（Claude）：
  - 加了一条断言：打开开关时 `tables_complete` 仍是 True。
  - 把从一组 Chunk 里取表 Chunk 的那一行抽成查询模块里的 `table_chunks_of`，`knowledge` 来源和 small-to-big 分支都用它。
  - 不改的地方：打开开关时 `tables_complete` 按展开后的 Hit 计算。如果某张命中的表在全量读取里不存在，它会被悄悄丢掉。但 Hit 和全量读取来自同一份不可变的 Corpus，这种情况不会发生，所以不加检查。
