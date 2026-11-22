# 01: 检索 tool 的最小成功路径

**What to build:** 用户端按 Active Corpus 启动后，模型只传 `query`，就拿到与检索来源逐字相同的 Retrieval Context、命中的表名和“候选不保证足以回答”的说明。库和 Knowledge Scope 由系统注入，模型改不了。本工单不把 tool 接进查询图，也不改 `ask`。见 [spec](../spec.md)「实现决定 / Active Corpus」「检索 tool 的契约」。

**Blocked by:** None (can start immediately)

**Status:** ready-for-human

**分工：**

- Claude：Active Corpus 配置与读取、用户端启动、依赖声明；给 tool 一次写一个失败的测试并说明它验证什么，然后停下。
- 用户：tool 的参数、运行状态与 context 的注入、成功时的返回值。Claude 审查，不代写。

**用户故事：** 1–5、23–31、42、43、44

- [x] `config/active_corpus.json` 写入 spec 给出的两个库；`corpus_id` 和 `schema_version` 与 spec 一致
- [x] 用户端启动时读一次配置，得到各库的 Knowledge Scope；测试：启动后再改文件，本次启动的结果不变；评测仍自己指定 Scope，不读这份配置
- [x] 测试：配置文件缺失、不可读或不符合格式时，启动以 `configuration_error` 失败
- [ ] 测试：经 `ToolNode` 发起一次调用，模型可见的参数 schema 只有 `query`
- [ ] 测试：检索收到模型给的查询原文、运行状态里那个库的 Scope，以及固定配置（hybrid + 重排、`top_k=10`、三种知识类型、不展开）
- [ ] 测试：模型额外传入库、Scope、检索方法、top_k 或运行时，都不生效
- [ ] 测试：返回文本里的知识与检索来源对同一组 Hit 的渲染逐字相同，并带有命中的表名和“候选不保证足以回答”的固定说明
- [ ] 测试：环境变量打开追踪时，不编译查询图、单独使用 tool 也不会上传 LangSmith
- [x] `langchain-core` 写成直接依赖，已安装的版本不变
- [ ] 查询图和 `ask` 的现有测试不改、保持通过
- [ ] 文档只把本工单做完并测过的能力写成已实现，并写明 tool 还没有调用方；面试材料里的状态同步
- [ ] 架构测试和全部单元测试（不含集成测试）通过，记录实际命令与结果

## Comments

- 2026-11-23 Claude 的部分完成（TDD 四轮），tool 待用户实现：
  - `start_serving(path)` 读一次 Active Corpus，返回现有的 `QueryContext`；配置缺失、不可读或格式不对时抛 `configuration_error`；同时关闭 LangSmith 追踪，因为 tool 可以不经过查询图使用。
  - `langchain-core` 写成直接依赖，仍是 1.6.5。
  - `uv run --no-sync pytest tests --ignore=tests/integration -q -p no:cacheprovider` → 618 passed、1 skipped（含架构测试）。集成测试没有运行，项目没有配置类型检查器。
  - 追踪一项只验证到启动，等 tool 存在后再经 tool 调用验证一次，所以还没勾。
  - 没有运行 `/code-review`：工单还没做完。
