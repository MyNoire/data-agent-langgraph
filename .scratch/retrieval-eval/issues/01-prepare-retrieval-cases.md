# 01: 检索题集准备（toxicology 先跑通）

**What to build:** 开发者用一条命令，从 BIRD mini-dev 题目、冻结 100 题和若干 Corpus 生成检索题集。每题带 Relevant Set（从金标准 SQL 解析出的表和列）、冻结标记和 Knowledge Scope；同时生成一份可以提交的 manifest。先用 toxicology 的 40 题跑通。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] 公开入口 `prepare_retrieval_cases`，并在 `eval` 下新增对应的 CLI 命令
- [ ] Relevant Set 用 sqlglot 按 SQLite 方言解析，能正确处理别名、不带表名的列、JOIN、子查询；`*` 不算列；名称统一成 Catalog 中的写法
- [ ] 任一题解析失败，或引用了 Catalog 中不存在的对象，题集生成整体失败，并报出题号
- [ ] 题集里没有金标准 SQL 和 evidence；题集文件被 Git 忽略；manifest 包含题号清单、来源 sha256、各库 Knowledge Scope 和题集 sha256
- [ ] 模块边界允许 `evaluation.retrieval` 依赖 `retrieval`、`knowledge` 和 `runtime` 的公开 api，并补一个反例
- [ ] 测试只用原创的小型夹具，不连 PG
- [ ] 真实生成 toxicology 的题集，抽查若干题的 Relevant Set 给用户看

## Comments
