# 01: 检索题集准备（toxicology 先跑通）

**What to build:** 开发者用一条命令，从 BIRD mini-dev 题目、冻结 100 题和若干 Corpus 生成检索题集。每题带 Relevant Set（从金标准 SQL 解析出的表和列）、冻结标记和 Knowledge Scope；同时生成一份可以提交的 manifest。先用 toxicology 的 40 题跑通。

**Blocked by:** None (can start immediately)

**Status:** done

- [x] 公开入口 `prepare_retrieval_cases`，并在 `eval` 下新增对应的 CLI 命令
- [x] Relevant Set 用 sqlglot 按 SQLite 方言解析，能正确处理别名、不带表名的列、JOIN、子查询；`*` 不算列；名称统一成 Catalog 中的写法
- [x] 任一题解析失败，或引用了 Catalog 中不存在的对象，题集生成整体失败，并报出题号
- [x] 题集里没有金标准 SQL 和 evidence；题集文件被 Git 忽略；manifest 包含题号清单、来源 sha256、各库 Knowledge Scope 和题集 sha256
- [x] 模块边界允许 `evaluation.retrieval` 依赖 `retrieval`、`knowledge` 和 `runtime` 的公开 api，并补一个反例
- [x] 测试只用原创的小型夹具，不连 PG
- [x] 真实生成 toxicology 的题集，抽查若干题的 Relevant Set 给用户看

## Comments
- 2026-11-04 完成（fc8032f，以及审查后的修正提交）。
  - 真实运行：toxicology 40 题、student_club 48 题，其中冻结题 16 道；88 道金标准 SQL 全部解析成功，每题至少有 1 列。Scope 与导入的知识库一致。抽查了 1317、1323、1344、197 四道非冻结题，结果给用户看过。
  - 产物在 `artifacts/validation/2026-11-04/retrieval-cases-spotcheck/`（被 Git 忽略）。正式题集和 manifest 的提交放到工单 05。
  - 审查后的修正：表名和列名统一为 Corpus 中的写法（原先 Corpus 名称含大写时会误报"不存在的表"）；用 `_CorpusSchema` 代替二元组；Corpus 外的对象改抛专用异常，收窄了 except；`PreparedCases` 直接返回 manifest 路径；补全 CLI 的 docstring。
  - 决定不加：题集准备不建 `task_run` 运行记录（输入 hash 和 Scope 已记在 manifest 里，用户同意）。
  - 测试：`pytest -q --ignore=tests/integration` 结果为 413 passed、1 skipped、1 failed。失败的是 `test_console_and_module_share_help`：当前 shell 的 PATH 里没有 `querypilot` 命令，这个测试改动前就失败。集成测试没有跑，因为 Docker 没有运行。
