# SQL 执行的安全边界

模型生成的 SQL 是不可信输入。从生成到执行要经过三层检查，任何一层拒绝都不会执行；修复后的 SQL 也要重新走同一套检查。实现在 `src/querypilot/databases/`。

## 1. 数据库登记

- 只接受 `QUERYPILOT_DATA_DIR` 目录内已存在的 `.db` / `.sqlite` / `.sqlite3` 文件。
- 解析路径时拦截 `..` 越界和符号链接逃逸。
- 登记时用只读模式打开一次，确认它确实是 SQLite 数据库。

## 2. AST 检查（guard）

用 sqlglot 按 SQLite 方言把 SQL 解析成语法树，不用正则匹配，避免被字符串字面量或注释骗过。

- 必须恰好是**一条**语句，并且是 `SELECT`（含 `UNION` / `EXCEPT` / `INTERSECT`）。
- 语法树里任何位置出现 `INSERT`、`UPDATE`、`DELETE`、`CREATE`、`DROP`、`ALTER`、`MERGE` 或命令语句，都直接拒绝。
- 禁用 `load_extension`、`readfile`、`writefile`、`sqlite_version` 等函数。
- 引用的表必须在已登记数据库的结构里。CTE 名称按查询内的局部别名处理，不会被当成真实的表。
- **统一加 LIMIT**：没写 LIMIT，或者 LIMIT 超过上限时，在语法树上把它改成上限；LIMIT 必须是非负整数字面量。
- 带列名清单的 CTE（`WITH t(a, b) AS ...`）目前会被拒绝，因为当前版本的 sqlglot 重写时可能丢失列名。

## 3. 只读执行

- 以 `mode=ro` 打开文件，从文件层面禁止写入，再设置 `PRAGMA query_only=ON`。
- SQLite authorizer 只放行读操作、`SELECT` 和白名单内的函数。
- 用 progress handler 限制 CPU 时间，超时就中断执行。
- 结果最多返回上限行数（guard 已经加了同样的 LIMIT）；结果序列化成 JSON 后超过字节上限时直接报错，不返回部分结果。

上限由配置项 `QUERYPILOT_MAX_ROWS`、`QUERYPILOT_QUERY_TIMEOUT_MS`、`QUERYPILOT_MAX_RESPONSE_BYTES` 设置。

## 4. 日志里不出现什么

普通日志和进度事件不记录问题原文、SQL、结果行和凭据。guard 的判定和实际执行的 SQL 只写进私有的 `private/sql-audit.jsonl`，不会传到普通日志。详见[运行记录与事件](observability.md)。

## 5. 已知限制

这是本地项目的最小安全基线。它没有校验每一个列引用，也拦不住所有昂贵的查询计划；等待锁时不一定能及时取消；没有带认证的数据库登记，也没有为任意远程 PostgreSQL 连接做安全加固。不要把 HTTP 接口暴露给不可信的用户。
