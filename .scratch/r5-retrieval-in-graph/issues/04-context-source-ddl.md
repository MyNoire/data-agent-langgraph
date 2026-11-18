# 04: Context Source 参数与 `ddl` 来源

**What to build:** 图的调用方在编译查询图时选择 Context Source。`schema`（默认）是今天的行为；`ddl` 把这个库全部表的建表语句给模型。评测开发者用 `eval graph --context-source ddl` 跑"全部建表语句、不带知识"这一组。

**Blocked by:** None (can start immediately)

**Status:** ready-for-human

结对分工：图的改动由**用户写**（乒乓 TDD，Claude 先讲例子、写失败的测试，再审查实现）。建表语句的读取、评测参数和报告由 Claude 写。

- [x] 数据库模块提供只读的建表语句读取：从 SQLite 的 `sqlite_master` 取每张用户表的建表语句，跳过内部表
- [x] `build_query_graph` 增加编译时参数 `context_source`，默认 `schema`；不认识的取值在编译时报错
- [x] `ddl` 时给模型的 Schema 是全部表的建表语句，按表名顺序拼接
- [x] 路径在编译时选定，图里没有条件边
- [x] 两条路径下 AST 检查都允许这个库的全部表
- [x] `schema` 路径的行为不变：现有的图测试不改断言就能通过
- [x] 图版本升为 `single-v2`，只在一处定义，评测和命令行都从那里取
- [x] `eval graph --context-source`；运行配置和报告记录 Context Source
- [x] 日志和运行记录不写建表语句
- [x] 测试用假 SQL 生成器，覆盖：两种来源下假生成器各自收到的 Schema、`ddl` 下 AST 检查的范围、不认识的取值、报告里的 Context Source

## Comments

- 实现（2026-11-15）：
  - 建表语句的读取由 Claude 写：`databases.api.get_table_ddl`，只读连接查 `sqlite_master`，按表名排序，跳过 `sqlite_` 内部表。
  - 图的改动由用户写（乒乓 TDD，三个测试在 `tests/test_graph_context_source.py`）：`build_query_graph` 里写了 `load_columns` 和 `load_ddl` 两个函数，编译时用 `loaders` 字典选一个挂到 `load_schema` 节点上，字典里没有的取值抛 `ValueError`。节点名不变，图里没有条件边。
  - 评测参数和报告由 Claude 写：`run_graph(..., context_source=...)`、`eval graph --context-source`，报告、运行配置和公开导出都记下 Context Source。
- `GRAPH_VERSION = "single-v2"` 和 `ContextSource` 定义在 `querying`，由它的 `api.py` 导出。评测、`query ask` 命令和 `ask` 都从这里取，提示词版本也改成从 `sql_generation` 取，不再写字面量。
- 命令行和 `run_graph` 原来各自拼一份运行配置。命令行那份少了模型、执行上限等字段，现在两边都调用 `sql_run_config`，有测试断言两份完全相同。
- 运行配置有字段白名单（`runtime/_privacy.py`），`context_source` 已加进去。
- 命令行的 `--context-source` 自己写了一份可选值 `("schema", "ddl")`，没有从 `querying` 导入：导入 `querying.api` 会让每条命令的解析多花约 1.6 秒。另有一个测试检查图认识的每种来源命令行都接受。
- 审查（用户的实现）：没有改逻辑。整理了 import，删掉 master 上就已经没人用的六个 import；补全两个加载函数和 `build_query_graph` 的 docstring；报错信息带上收到的值和允许的取值。
- 测试：`tests/test_graph_context_source.py` 3 个（ddl 收到的 Schema、ddl 下仍然拒绝 `sqlite_master`、拼错的取值），`tests/test_graph_eval.py` 新增 4 个（报告、运行配置和公开导出里的 Context Source，运行目录不含建表语句；命令行参数，以及命令行与 `run_graph` 写出的运行配置相同；命令行接受图认识的每种来源；拼错的取值在解析参数时退出），`tests/contracts/test_databases.py` 1 个（`get_table_ddl`），`tests/test_single_graph.py` 1 个（图状态里的 token 用量）。现有测试只改了两处图版本的断言（`single-v1` 改为 `single-v2`）和导出清单。
- 没验证：真实模型下 `ddl` 组的效果和用量，留到工单 07。
