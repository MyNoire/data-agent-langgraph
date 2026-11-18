# 06: `knowledge` 来源进图

**What to build:** 图的调用方选 `knowledge` 作为 Context Source 时，图按运行时 context 里的 Knowledge Scope 读出这个库的全部知识，经组装和渲染后交给模型。评测开发者用 `eval graph --context-source knowledge --structure-format columns|ddl` 跑全量知识的两组。

**Blocked by:** 04, 05

**Status:** ready-for-human

结对分工：节点、路径连接和运行时 context 由**用户写**（乒乓 TDD）。开始前 Claude 先写一份带最终代码的 LangGraph 运行时 context 短教程。知识读取、评测接线由 Claude 写。

- [x] 知识读取：取一个 Knowledge Scope 下的全部 Chunk，顺序固定，有数量上限，由 retrieval 模块对外提供；测试时可以换成假的读取
- [x] 图声明运行时 context，调用时传入"`database_id` → Knowledge Scope"对照表
- [x] `knowledge` 路径按状态里的 `database_id` 查表；查不到时请求失败，不调用模型
- [x] Knowledge Scope 不出现在图状态和输出里
- [x] `load_knowledge` 把全部 Chunk 当作 Hit 交给 `build_context`，后者调用 05 的组装和渲染
- [x] `build_query_graph` 增加编译时参数 `structure_format`（`columns` 或 `ddl`），只对 `knowledge` 和 `retrieval` 有效；`ddl` 时用 `load_schema` 读到的建表语句
- [x] 给模型的 Schema 覆盖这个库的每张表
- [x] `schema` 和 `ddl` 两条路径不需要运行时 context，行为不变
- [x] `eval graph --context-source knowledge --structure-format …`，从检索题集文件取各库的 Knowledge Scope；需要而没有提供时在开始前报错
- [x] 运行配置和报告记录结构写法、渲染版本和各库的 Knowledge Scope
- [x] 图的测试用假 SQL 生成器和假知识读取，覆盖：假生成器收到的 Schema（两种写法）、Scope 传到了知识读取、对照表里没有当前库时失败且假生成器没被调用、状态和输出里没有 Scope
- [x] 知识读取的测试用真实 PG 加原创小 Corpus，覆盖：顺序固定、只读到当前 Scope 的内容
- [x] 模块边界测试通过：查询图只从 retrieval 和 databases 的公开入口导入

## Comments

- 实现（2026-11-17）：
  - 图由用户写（乒乓 TDD，`tests/test_graph_knowledge_source.py` 4 个测试）：`QueryContext`（frozen dataclass，只有 `knowledge_scopes`）、`QueryState` 新增 `table_ddl` / `hits` / `table_chunks`、`load_knowledge` 和 `build_context` 两个节点。路径改成"每种来源一段前缀 + 公共后半段"，用 `add_sequence` 一次连好，没有条件边；不认识的来源仍在编译时报 ValueError。
  - 用户问过"要不要把三种来源拆成几条路径"：每次编译只选一条，编出来的图就是一条直线；条件边留给运行时才知道走哪条的情况（以后的 SQL 修复）。
  - 后两个测试（对照表里没有当前库、Scope 不进状态）写完就通过：查不到时字典查找直接抛 KeyError，Scope 只在 `load_knowledge` 的局部变量里。为确认测试 4 能抓到问题，临时把 Scope 写进状态跑过一次，测试失败，代码已恢复。
  - Claude 写：`retrieval.api.load_scope_chunks`（`_search.scope_chunks` 一条 SQL，按 chunk_key COLLATE "C"、chunk_id 排序；上限沿用读取层的 5000 行、1 MiB，超出报 capacity_exceeded 不截断）、`retrieval.api` 导出 `KnowledgeScope`（querying 不能导入 knowledge）、评测和命令行接线、文档。
- 和工单不一样的地方：
  - 知识读取的测试除了"顺序固定、只读当前 Scope"，还测了 Scope 不存在时报 `scope_not_found`（`tests/integration/test_scope_chunks.py`）。
  - 模块边界：`evaluation.sql` 现在也可以从 retrieval 的公开入口导入（类型和默认读取函数）。工单 10 的"表是否拿全"本来也要用 `hit_table`。
  - 缺 Scope 的检查放在 `sql_run_config` 里：命令行和 `run_graph` 都在建立运行记录之前调用它，所以缺库时不会留下运行记录。只给了 `--context-source knowledge` 而没给 `--retrieval-cases` 时，在解析参数阶段就退出。
  - 同一个库在检索题集里出现两个不同 Scope 时报 ValueError，不替调用方挑一个。
  - `structure_format`、`schema_render_version`、`knowledge_scopes` 只在 knowledge 来源时记进运行配置和报告，`schema` / `ddl` 运行里没有这三项，免得看起来用了某种写法。运行配置的白名单（`runtime/_privacy.py`）和公开导出都加了这三项。
- 真实数据的一次跑通（2026-11-17，生成器是固定 SQL，不调用模型；Scope 取自 `evals/retrieval/retrieval_cases.json`）：

  | 库 | 写法 | Schema 长度 | 表数 | 单次请求 |
  |---|---|---|---|---|
  | toxicology | columns | 2619 字符 | 4 | 约 1.0 秒（首次） |
  | toxicology | ddl | 3143 字符 | 4 | 约 0.4 秒 |
  | student_club | columns | 7371 字符 | 8 | 约 0.4 秒 |
  | student_club | ddl | 8373 字符 | 8 | 约 0.5 秒 |

  每次请求的时间主要花在打开知识库只读会话（见 known gaps 里的读取会话开销）；88 题一组多出不到一分钟。
- 测试：`pytest -q --ignore=tests/integration` 532 通过、1 失败（一直就有的 `test_console_and_module_share_help`）、1 跳过；`pytest tests/integration --require-postgres` 118 通过。
