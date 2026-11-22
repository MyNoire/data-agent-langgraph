# 服务边界与检索 tool

Status: needs-info

> 等用户确认文末「待确认」的几项后改为 ready-for-agent，再拆工单。架构决定见 [ADR 0004](../../docs/adr/0004-admin-and-serving-separation.md)。

## 问题

知识结构已经定下（列级 Chunk + hybrid + 重排、`top_k=10`、不展开），但还不能作为可靠的 tool 交给面向业务用户的 Agent：

- **没有在用语料**：`database_id → Knowledge Scope` 只能从评测题集或手写文件得到。
- **管理面没有隔开**：`retrieval` 导入 `knowledge.api` 总入口；存储配置带着三个角色的 DSN；HTTP `POST /databases` 没有鉴权就能登记数据库。CLI 目前没有登记命令，HTTP 是除 BIRD 导入以外唯一的登记入口。
- **失败没有全部归类**：缺 Embedding / Rerank 配置、查询超过 2000 字时抛 `ValidationError`；缺 Scope、缺表块时是裸 `KeyError`。
- **知识会静默过期**：查询时不比对业务库结构和 Corpus 的 Schema Version。
- **“没找到”的语义不成立**：2026-11-22 在两个库上实测，`zzqxv` 和“火星天气”在 lexical 下是 `no_match`，在 hybrid 下都返回 10 个 Hit（用固定假向量，未调用付费服务）。

## 解决方案

建立服务面：用户端只用 reader 配置，启动时读入在用语料并核对结构版本，再把确定性的检索包成一个薄 tool。模型只给查询文本，得到组装好的 Retrieval Context，或一个有类别的失败。

## 用户故事

### 知识管理员

1. 作为知识管理员，我想在仓库的一份配置里指定每个库的在用语料，这样切换知识版本有 Git 记录。
2. 作为知识管理员，我想用 CLI 登记数据库，这样业务用户接口不需要这项能力。
3. 作为知识管理员，我想让评测继续自己指定 Scope，这样切换在用语料不影响实验。

### 业务用户与查询 Agent

4. 作为业务用户，我不能经 HTTP 登记数据库，也不能触发知识准备、校验和评测。
5. 作为业务用户，我想在知识与库结构不一致时被明确告知该库不可用，而不是得到按旧知识写的答案。
6. 作为查询 Agent，我只提供查询文本就能拿到按表组织的知识，不需要也不能选择 Scope、方法和 top_k。
7. 作为查询 Agent，我想知道返回的是最相关候选、不保证足以回答，这样我能决定继续、澄清或拒答。
8. 作为查询 Agent，我想让失败带有类别和是否值得改写重试，这样不会对配置错误反复重试。

### 开发者

9. 作为开发者，我想在误导入管理操作时让架构测试失败。
10. 作为开发者，我想在只有 reader DSN 的环境里运行全部用户端检索。

## 实现决定

### 在用语料

- 新增 `config/active_corpus.json`：`database_id → corpus_id + schema_version`。初始为两份 values Corpus（toxicology `50c156f8…`、student_club `748167d2…`），完整 ID 取自 `evals/retrieval/retrieval_cases_values_manifest.json`，之后两者不再关联。
- 用户端启动时读一次，生成 `QueryContext` 的对照表；不热加载。

### 启动时的结构核对

- 对配置里的每个库，用与构建相同的算法（`extract_catalog` + `schema_version`）重算并比较。`schema_version` 需要从 `knowledge_corpus.api` 导出，再由 retrieval 提供核对入口；业务库路径由 querying 经 `DatabaseRegistry` 取得后传入，不新增 retrieval → databases 的依赖。
- 不一致、库未登记或提取失败时，只停用这个库并记下类别，其他库照常。出现视图时 `extract_catalog` 会报 `CatalogError`，同样停用。
- 已验证可行：两个正式库重算结果与在用语料一致；在临时副本上删一行版本不变，加一列版本改变。

### 只读配置与导入边界

- 新增只含 `read_dsn` 和读取超时的配置类型。`open_read_scope` 和 retrieval 只依赖它；管理端现有配置继续可用。
- 边界测试按名字限定 retrieval 从 knowledge 导入的内容：读会话与 Scope、读配置、Embedder、相关错误与类型、上一节的核对函数。querying 继续不导入 knowledge。`entrypoints/http.py` 和 `entrypoints/commands/query.py` 不得导入 knowledge 和 evaluation。

### 登记数据库

- 删除 HTTP `POST /databases`，新增管理员命令 `querypilot data register`，沿用 `DatabaseRegistry.register` 的全部检查。

### 检索 tool

- 位置在 querying，是薄适配层；retrieval 不知道自己被模型调用。
- 模型可见的输入只有 `query`。`database_id` 来自运行状态，Scope 来自运行时 context；方法、重排、top_k 和知识类型固定为已定配置，都不出现在 tool 的参数 schema 里。
- 成功时返回渲染后的 Retrieval Context、命中的表名，以及“候选不保证足以回答”的固定说明。组装和渲染复用现有函数，与 retrieval 来源逐字相同。不设分数阈值。
- 返回内容是数据，不是指令。
- 失败时返回类别、安全的固定消息和是否值得改写重试，不含查询原文、SQL、连接串：

| 情况 | 类别 | 改写重试 |
|---|---|---|
| 空查询、超长、没有有效词 | `input_error` | 是 |
| 没有候选 | `no_match` | 是 |
| 缺配置、缺依赖 | `configuration_error` | 否 |
| 库没有在用语料 | `scope_not_configured` | 否 |
| 启动核对未通过 | `knowledge_stale` / `database_unavailable` | 否 |
| Embedding、重排、读取层失败 | 沿用已有类别 | 否 |
| 命中的表缺表块 | `knowledge_state_error` | 否 |

- 归类在产生错误的层完成，tool 只做转换。没有预料到的程序错误继续抛出，不吞异常。
- 不回退、不自动重试（SDK 自带的重试除外）。
- tool 即使不经过 `build_query_graph` 使用，也不能把问题和知识正文上传到 LangSmith：关闭追踪目前只发生在编译查询图时。

### 分工

- **用户写（乒乓 TDD）**：tool 的参数与 context 注入、成功与失败到返回值的映射、它在图里的接线。
- **Claude 写**：只读配置、边界测试、在用语料读取、结构核对函数、retrieval 层的错误归类、CLI 登记、文档。

## 测试决定

只测外部行为，用假检索、假模型和临时 SQLite；不调用付费服务。

- **架构测试**：在合成源码里，retrieval 导入 `import_corpus`、用户入口导入 knowledge 或 evaluation 都会失败；现有源码通过。
- **读配置**：只有 reader DSN 时能检索；类型上没有 admin 和 importer 的 DSN。真实角色行为在专用测试库验证。
- **启动核对**：一致时可用；加列后停用该库且不影响另一个库；只改数据仍可用；未登记和提取失败各自归类。
- **tool**：参数 schema 只有 `query`；额外传入 Scope 不生效；成功文本与查询图逐字相同；表中每类失败都有一个用例；程序错误不被吞掉。
- **HTTP / CLI**：`POST /databases` 不再可用；CLI 登记沿用原有的拒绝规则。
- 每张工单提交前跑架构测试和全部单元测试；涉及读配置的工单另跑集成测试。

## 不在范围内

- 自主检索循环、调用次数上限、与固定流程的对比实验（下一项功能，另行对齐）。
- 总时限、降级、连接池、把部署检查移到启动时（主功能完成后优化）。
- Access Grant、选库、列级权限。`POST /queries` 仍接受任意只读 SQL，它能读到隐私列。
- 分数阈值与拒答。
- 知识内容修改、重建 Corpus、正文脱敏。
- 热切换、灰度、MCP、建库 Agent。

## 待确认

1. **测试接口**：是否就用上面五组 seam（架构测试、读配置、启动核对、tool、HTTP / CLI）。建议：是。
2. **`ask` 的默认来源**：本功能是否保持 `schema` 不变，等自主检索的对比做完再切换。建议：是，一次只改一种机制。
3. **异常范围**：“所有失败都归类”是否按“所有预期内失败”理解，程序错误继续抛出。建议：是，符合 AGENTS.md 的“不吞异常”。
