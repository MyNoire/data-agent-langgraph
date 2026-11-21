# 01: Small-to-Big 开关与 `eval graph --small-to-big`

**What to build:** 评测者可以用 `eval graph --context-source retrieval --small-to-big` 跑一组 SQL 评测：检索照常进行，进入上下文的表与不开开关时相同，但每张被命中的表以完整的 Document 交给模型，渲染与全量知识（`knowledge` 来源）里这张表逐字相同，表的先后仍按检索名次。开关写进运行配置、报告和 `export-public`；默认关闭，关闭时一切与今天逐字相同。在非检索来源上打开开关会被拒绝。见 [spec](../spec.md)「实现决定 / 查询图」「图评测」。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] 查询图有编译参数 `small_to_big`（默认 `False`）；打开时命中表的全部 Chunk 由可注入的全量读取函数从当前 Knowledge Scope 读出，按 `hit_table` 只保留命中的表，表内顺序同全量读取，表之间按 Hit 第一次出现的顺序
- [ ] 测试：假检索命中两张表的部分列，生成器收到的 Schema 只含这两张表，每张表与 `knowledge` 来源对同一份 Chunk 渲染出的对应表逐字相同，先后按检索名次
- [ ] 测试：只命中某张表的关系 Hit，这张表同样展开
- [ ] 测试：`ddl` 写法下展开的表使用建表语句
- [ ] 测试：关闭开关时输出与今天逐字相同（现有检索来源测试保持通过）
- [ ] 测试：`schema`、`ddl`、`knowledge` 来源上打开开关，编译时报 `ValueError`
- [ ] `eval graph --small-to-big / --no-small-to-big`（默认关闭）；不带 `--context-source retrieval` 时在写任何运行记录前报错（有测试）
- [ ] `small_to_big` 登记进运行配置字段白名单，写进报告和 `export-public`；用假生成器跑一次，`true` / `false` 两种情况都有测试
- [ ] `SCHEMA_RENDER_VERSION`、`GRAPH_VERSION` 不变；`tables_complete` 计算不变
- [ ] 查询图 docstring 写明新参数；SQL 评测文档写入 `--small-to-big` 的说明
- [ ] 模块边界测试和全部单元测试（`--ignore=tests/integration`）通过，记录实际命令与结果
