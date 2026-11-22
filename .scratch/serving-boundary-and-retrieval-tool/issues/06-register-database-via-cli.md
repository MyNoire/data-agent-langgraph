# 06: 登记数据库从 HTTP 移到 CLI

**What to build:** 知识管理员用 `querypilot data register` 登记数据库，登记后业务用户能经 HTTP 看到它；HTTP 不再提供登记。这是对 HTTP 调用方的破坏性改动。见 [spec](../spec.md)「实现决定 / 登记数据库」。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

**分工：** Claude 写。

**用户故事：** 11–14、16、44

- [ ] 测试：CLI 登记一个临时 SQLite 后，HTTP 能列出它并读到它的结构
- [ ] 测试：沿用登记的全部检查，标识不合法、路径在数据目录外、文件无效、标识已绑定其他文件时都被拒绝，且不改登记表
- [ ] 测试：`POST /databases` 不再可用
- [ ] 测试：列库、看结构、SQL 查询和健康检查照常工作
- [ ] CLI 帮助、README 和架构文档改成新的登记方式；面试材料里的状态同步
- [ ] PR 正文写明这是破坏性改动
- [ ] 架构测试和全部单元测试（不含集成测试）通过，记录实际命令与结果
