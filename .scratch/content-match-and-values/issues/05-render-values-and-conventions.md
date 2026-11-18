# 05: Schema 渲染带上取值和数据约定

**What to build:** 查询图把知识渲染成 Schema 时，命中的列带上取值，表带上数据约定；没被命中的列仍然只出现在字段目录里。`knowledge` 与 `retrieval` 两种 Context Source、`columns` 与 `ddl` 两种结构写法都一样。渲染版本从 `r5-schema-v2` 升一版并写进报告。

**Blocked by:** 03, 04

**Status:** ready-for-agent

- [ ] 渲染规则认识取值和数据约定两个新标签；遇到不认识的行仍然报错
- [ ] 命中的列在说明后带上取值；表在记录粒度后带上数据约定；没被命中的列只出现在字段目录里（有测试）
- [ ] `columns` 和 `ddl` 两种写法都带上新内容（有测试）
- [ ] 旧 Corpus 的 Chunk（没有新行）渲染结果与升版前逐字相同（有测试，可用现有的真实 Chunk 夹具）
- [ ] 渲染版本升级，运行配置和报告里记录新版本
- [ ] SQL 评测文档中 Context Source 一节更新渲染版本的说明
- [ ] 模块边界测试和全部单元测试通过
