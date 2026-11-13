# 04: Context Source 参数与 `ddl` 来源

**What to build:** 图的调用方在编译查询图时选择 Context Source。`schema`（默认）是今天的行为；`ddl` 把这个库全部表的建表语句给模型。评测开发者用 `eval graph --context-source ddl` 跑"全部建表语句、不带知识"这一组。

**Blocked by:** None (can start immediately)

**Status:** ready-for-human

结对分工：图的改动由**用户写**（乒乓 TDD，Claude 先讲例子、写失败的测试，再审查实现）。建表语句的读取、评测参数和报告由 Claude 写。

- [ ] 数据库模块提供只读的建表语句读取：从 SQLite 的 `sqlite_master` 取每张用户表的建表语句，跳过内部表
- [ ] `build_query_graph` 增加编译时参数 `context_source`，默认 `schema`；不认识的取值在编译时报错
- [ ] `ddl` 时给模型的 Schema 是全部表的建表语句，按表名顺序拼接
- [ ] 路径在编译时选定，图里没有条件边
- [ ] 两条路径下 AST 检查都允许这个库的全部表
- [ ] `schema` 路径的行为不变：现有的图测试不改断言就能通过
- [ ] 图版本升为 `single-v2`，只在一处定义，评测和命令行都从那里取
- [ ] `eval graph --context-source`；运行配置和报告记录 Context Source
- [ ] 日志和运行记录不写建表语句
- [ ] 测试用假 SQL 生成器，覆盖：两种来源下假生成器各自收到的 Schema、`ddl` 下 AST 检查的范围、不认识的取值、报告里的 Context Source
