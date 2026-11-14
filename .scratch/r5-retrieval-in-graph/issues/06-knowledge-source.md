# 06: `knowledge` 来源进图

**What to build:** 图的调用方选 `knowledge` 作为 Context Source 时，图按运行时 context 里的 Knowledge Scope 读出这个库的全部知识，经组装和渲染后交给模型。评测开发者用 `eval graph --context-source knowledge --structure-format columns|ddl` 跑全量知识的两组。

**Blocked by:** 04, 05

**Status:** ready-for-human

结对分工：节点、路径连接和运行时 context 由**用户写**（乒乓 TDD）。开始前 Claude 先写一份带最终代码的 LangGraph 运行时 context 短教程。知识读取、评测接线由 Claude 写。

- [ ] 知识读取：取一个 Knowledge Scope 下的全部 Chunk，顺序固定，有数量上限，由 retrieval 模块对外提供；测试时可以换成假的读取
- [ ] 图声明运行时 context，调用时传入"`database_id` → Knowledge Scope"对照表
- [ ] `knowledge` 路径按状态里的 `database_id` 查表；查不到时请求失败，不调用模型
- [ ] Knowledge Scope 不出现在图状态和输出里
- [ ] `load_knowledge` 把全部 Chunk 当作 Hit 交给 `build_context`，后者调用 05 的组装和渲染
- [ ] `build_query_graph` 增加编译时参数 `structure_format`（`columns` 或 `ddl`），只对 `knowledge` 和 `retrieval` 有效；`ddl` 时用 `load_schema` 读到的建表语句
- [ ] 给模型的 Schema 覆盖这个库的每张表
- [ ] `schema` 和 `ddl` 两条路径不需要运行时 context，行为不变
- [ ] `eval graph --context-source knowledge --structure-format …`，从检索题集文件取各库的 Knowledge Scope；需要而没有提供时在开始前报错
- [ ] 运行配置和报告记录结构写法、渲染版本和各库的 Knowledge Scope
- [ ] 图的测试用假 SQL 生成器和假知识读取，覆盖：假生成器收到的 Schema（两种写法）、Scope 传到了知识读取、对照表里没有当前库时失败且假生成器没被调用、状态和输出里没有 Scope
- [ ] 知识读取的测试用真实 PG 加原创小 Corpus，覆盖：顺序固定、只读到当前 Scope 的内容
- [ ] 模块边界测试通过：查询图只从 retrieval 和 databases 的公开入口导入
