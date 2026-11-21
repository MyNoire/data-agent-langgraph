# 05: Schema 渲染带上取值和数据约定

**What to build:** 查询图把知识渲染成 Schema 时，命中的列带上取值，表带上数据约定；没被命中的列仍然只出现在字段目录里。`knowledge` 与 `retrieval` 两种 Context Source、`columns` 与 `ddl` 两种结构写法都一样。渲染版本从 `r5-schema-v2` 升一版并写进报告。

**Blocked by:** 03, 04

**Status:** ready-for-agent

- [x] 渲染规则认识取值和数据约定两个新标签；遇到不认识的行仍然报错
- [x] 命中的列在说明后带上取值；表在记录粒度后带上数据约定；没被命中的列只出现在字段目录里（有测试）
- [x] `columns` 和 `ddl` 两种写法都带上新内容（有测试）
- [x] 旧 Corpus 的 Chunk（没有新行）渲染结果与升版前逐字相同（有测试，可用现有的真实 Chunk 夹具）
- [x] 渲染版本升级，运行配置和报告里记录新版本
- [x] SQL 评测文档中 Context Source 一节更新渲染版本的说明
- [x] 模块边界测试和全部单元测试通过

## Comments

**2026-11-20 完成**

- `querying/_render.py`：表的标签加上「数据约定」，列的标签加上「取值」；表有数据约定时接在记录粒度后，命中的列有取值时接在说明（含取值解释的续行）后。没被命中的列不变，仍然只在字段目录或建表语句里。不认识的行照旧报错。`SCHEMA_RENDER_VERSION` 升为 `r5-schema-v3`，运行配置和报告通过现有字段记录。
- 测试：`tests/test_schema_render.py` 新增一个按 columns / ddl 参数化的测试，用真实 Chunk 加上新的两行，期望结果是旧渲染多出三行。先红（新行被当成不认识的行报 ValueError）后绿。旧 Chunk 的几个逐字比对测试没改动，仍然通过，说明旧 Corpus 的渲染不变。两个图评测测试里的版本号改成 v3。
- 真实数据核对（scratchpad，非正式构建）：生成两个库的取值文件，用 v2 配置构建 Corpus，再把每张表连同全部列渲染一遍，每一行都能解析。student_club 5 条约定、44 列带取值（48 列减 4 个隐私列），toxicology 3 条、11 列。
- 全部单元测试 591 通过，失败的只有之前就有的 CLI PATH 那一个。
