# 02: 检索 tool 的失败契约

**What to build:** tool 失败时，模型拿到类别、安全的固定原因和是否值得改写重试，据此决定改查询还是停止。模型传参的结构错误沿用 LangGraph 默认提示；程序错误照常抛出，不交给模型。见 [spec](../spec.md)「实现决定 / 检索 tool 的契约」。

**Blocked by:** 01（检索 tool 的最小成功路径）

**Status:** done

**分工：**

- Claude：在检索层给不合格的查询归类；给 tool 一次写一个失败的测试并说明它验证什么，然后停下。
- 用户：失败到返回值的映射，以及组装上下文这一侧“缺表块”的归类。表块读取按现有契约忽略不存在的表，所以这个错误不能在检索层报。

**用户故事：** 32–34、36、38–41、43、44

- [x] 失败时返回给模型的文本里有类别、原因和是否值得改写重试，不依赖框架的 status 字段
- [x] 测试：空查询、超长、没有有效词返回 `input_error`、可以改写重试；走真实检索代码，不联网
- [x] 测试：没有候选返回 `no_match`、可以改写重试
- [x] 测试：库没有 Active Corpus 返回 `scope_not_configured`、不重试；不检索，也不换到别的库
- [x] 测试：命中的表缺表块返回 `knowledge_state_error`、不重试
- [x] 测试：Embedding、重排和读取层的失败沿用已有类别、不重试
- [x] 测试：以上消息都不含查询原文、SQL 和连接串
- [x] 测试：失败时不换检索方法或知识来源，tool 自己不重试
- [x] 测试：缺 `query` 或类型错误时仍由框架返回错误、tool 不执行；不断言提示文案，不自定义这条提示
- [x] 测试：未预料的错误抛到调用方，包括 tool 内部意外的 `ValidationError`
- [x] 直接构造检索请求时的校验行为、检索来源查询图的现有测试不变
- [x] 文档只把本工单做完并测过的能力写成已实现；面试材料里的状态同步
- [x] 架构测试和全部单元测试（不含集成测试）通过，记录实际命令与结果

## 完成说明（2026-11-28）

失败到返回值的映射、缺表块的判断、意外 `ValidationError` 的转换由用户编写；`_failure` 的抽取、Scope 查找、读表块失败的接住由 Claude 在用户同意下完成。查询不合格的归类在检索层：新增 `build_retrieval_request`，只有查询字段不合格时报 `input_error`，其余字段仍抛 `ValidationError`。

- 失败文本是三行：`类别: …`、`原因: …`、`可以改写查询后重试` 或 `不要重试`；只有 `input_error`、`no_match` 可以重试。
- 缺表块在 tool 里判断（读完表块、组装之前）：读取层按契约略过不存在的表，`assemble_context` 与查询图共用，本功能不改 `ask`，所以没有改它。
- 读取层失败的测试走真实读取代码：连接串带密码、指向本机不监听的端口，返回 `connection_failed`，文本不含连接串、账号、主机和查询原文。
- 推迟：缺 Embedding / Reranker 配置现在以 `RuntimeError`（原因是 `ValidationError`）抛给调用方，归为 `configuration_error` 属于工单 03；嵌套 `try` 的可读性、`build_retrieval_request(**fields)` 的显式参数留到接进图时再看。
- 审查（Standards / Spec 两路）后修正：读表块时的读取层失败原本会抛穿 tool；补了重排失败、失败时只检索一次且方法不变的断言；docstring 补全；查询长度上限提成 `MAX_QUERY_CHARS`。

测试：`LANGCHAIN_TRACING_V2=false LANGSMITH_TRACING=false uv run --no-sync pytest tests --ignore=tests/integration -q -p no:cacheprovider` → 637 passed、1 skipped（含架构测试；`tests/test_retrieval_tool.py` 共 19 个测试）。集成测试没有运行；没有调用真实模型或检索服务。
