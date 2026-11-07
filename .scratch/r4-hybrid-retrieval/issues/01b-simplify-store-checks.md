# 01b: 精简知识库的迁移与核对

**What to build:** `store migrate` / `check` 改成 Flyway 式的核对：已执行迁移的版本和摘要与文件一致、PG 与 pgvector 版本正确、三个角色的有效权限符合契约。不再把迁移 SQL 解析成 AST 推导预期表结构、再与系统表逐列比对。以后的迁移可以用任意 DDL，不必扩展解析器。精简后在正式库执行迁移 002。

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] 删除结构比对：预期结构推导、约束表达式规范化、ALTER 推导，以及对应的单元测试
- [ ] `migrate` 直接执行迁移文件原文，不再经 sqlglot 解析后重新生成
- [ ] `bootstrap` 只负责角色、扩展、Schema 和基础授权，不读迁移记录；Schema 中有对象却没有 `schema_migrations` 时仍报 `migration_conflict`
- [ ] `check` = 版本 + 迁移记录与文件完全一致 + 有效权限；`migrate` 结束时做同样的核对
- [ ] 测试：已执行迁移的摘要被篡改、或出现文件里没有的版本时，`migrate` 和 `check` 报 `migration_conflict`
- [ ] 测试：测试库停在 001 并已导入 Corpus，`migrate` 升级到 002 后 `store verify` 仍然通过
- [ ] 知识库文档写明 002 删除了 `knowledge_chunks` 的三个向量列和对应约束；"核对"一节与精简后的行为一致
- [ ] 测试库上全部知识库集成测试通过；经用户同意后在正式库执行迁移 002，`store check`、两个正式 Corpus 的 `store verify`、`check-search` 和读正式库的集成测试通过

## Comments

- 起因：工单 01 的审查发现结构 AST 比对约 100 行、依赖 PG 反解析细节（如 `0::double precision` 特判），每种新 DDL 都要扩展推导；它挡的"绕过迁移手工改表"在单人项目里基本不会发生。摘要保证迁移文件没被改，事务保证迁移整体执行，`store verify` 仍逐行核对数据摘要。
- 一并处理工单 01 审查里的小项：测试里函数内 `import psycopg`、`uuid5(NAMESPACE_URL, str(uuid4()))` 改为 `uuid4()`、迁移文件首行"R4"和测试注释"不再"的措辞。
