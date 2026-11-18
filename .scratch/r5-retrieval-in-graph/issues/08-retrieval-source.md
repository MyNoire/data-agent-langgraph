# 08: `retrieval` 来源进图

**What to build:** 图的调用方选 `retrieval` 作为 Context Source 时，图用问题原文检索一次，把命中的知识按表组织后交给模型：模型只看到被命中的表。检索默认用 hybrid + 重排、`top_k=10`。检索失败时这次请求直接失败。评测开发者用 `eval graph --context-source retrieval` 跑检索组。

**Blocked by:** 06

**Status:** ready-for-human

结对分工：`retrieve` 节点和它在图里的连接由**用户写**（乒乓 TDD）。表 Chunk 的读取、评测参数和报告由 Claude 写。

- [x] 知识读取：按表名取一个 Knowledge Scope 下的表 Chunk，有数量上限，由 retrieval 模块对外提供
- [x] `retrieve` 节点：按问题原文、当前库的 Knowledge Scope 和编译时的检索配置检索一次，再读出这些 Hit 所属表的表 Chunk，交给 `build_context`
- [x] 检索配置是编译时参数：`method` 默认 `hybrid`，`rerank` 默认开启，`top_k` 固定为 10；`method` 和 `rerank` 可以在编译时改
- [x] 给模型的 Schema 只包含被命中的表
- [x] 五种情况都让请求失败，不调用模型，不退回 `schema` 路径：没有命中、查询没有有效词、Embedding 失败、重排失败、对照表里没有当前库
- [x] 五种失败的错误类别能分清
- [x] AST 检查仍允许这个库的全部表：模型用了没被检索到的表不会被拒
- [x] 检索器可以在测试里换成假的
- [x] `eval graph --context-source retrieval --method … --rerank / --no-rerank`，默认值与查询图一致；一次运行里所有题共用一个 Embedder 和一个 Reranker
- [x] 运行配置和报告记录检索方法、Embedding Profile、Rerank Profile、检索配置版本和 `top_k`
- [x] 评测里检索失败的题记为 error，带错误类别，继续下一题
- [x] 日志和运行记录不写 Hit 正文
- [x] 图的测试用假 SQL 生成器和假检索器，覆盖：假生成器收到的 Schema、Scope 和检索配置传到了检索请求、默认配置与编译时覆盖、五种失败且假生成器没被调用、AST 检查的范围
- [x] 表 Chunk 读取的测试用真实 PG 加原创小 Corpus
- [x] 在真实知识库上用 lexical 和假 SQL 生成器跑通几道自编的问题（不产生费用），结果记在 Comments

## Comments

- 实现（2026-11-17）：用户先开了头（参数和导入），随后说"这些都由你来做吧，我想尽快看到结果"，`retrieve` 节点和连线由 Claude 写完。
  - `retrieve`：按 `database_id` 从运行时 context 取 Scope → `RetrievalRequest(问题原文, Scope, method, rerank, top_k=RETRIEVAL_TOP_K=10)` → `outcome == "no_match"` 时抛 `KnowledgeRetrievalError("no_match")` → 用 `hit_table` 按第一次出现的顺序去重得到表名 → `table_chunk_reader(scope, tables)`，写出 `hits` 和 `table_chunks`，后面接工单 06 的 `build_context`。路径在 `prefix` 里加一行 `load_schema(load_ddl) → retrieve → build_context`。
  - 测试 `tests/test_graph_retrieval_source.py`：只给命中的表、请求里的 Scope 和默认配置、编译时覆盖配置、五种失败（参数化）、AST 范围是全部表，以及默认读取函数的签名。
- 和工单不一样的地方：
  - 真实跑通时发现图默认的 `retrieve_knowledge` 必须传存储配置，节点只给请求，第一题就会失败（单元测试都用假检索器，没暴露）。`retrieve_knowledge` 的 `settings` 改为可选，没传时按 `QUERYPILOT_KNOWLEDGE_*` 创建；加了一个测试用 `inspect.signature(...).bind` 守住三个默认读取函数按图的调用方式可调用。
  - 检索评测里"整次运行共用一组 Embedder 和 Reranker"的 `_default_retriever` 搬进 `retrieval.api`，改名 `shared_retriever`，检索评测和 SQL 评测共用。
  - 实际用到的检索版本不进图状态：SQL 评测把检索函数包一层，从每题的检索结果里收集方法、两个 Profile 和检索配置版本，报告的 `retrieval` 列出不同的几组（正常是一组）。
  - 逐题 `error_type` 改为"带类别的错误记类别，否则记类名"，与命令行的规则一致；之前图评测里只有知识读取和检索的错误带类别，已有报告不受影响。
- 真实知识库跑通（2026-11-17，lexical、不重排、生成器是固定 SQL，不调用模型和付费服务；问题是自编的）：

  | 库 | 问题 | 给模型的表 | Schema 长度 | 单次请求 |
  |---|---|---|---|---|
  | student_club | Which members went to events, and what is each member's major? | member, major, income, attendance, expense | 1905 字符 | 约 1.0 秒（首次） |
  | student_club | How much money did the club spend on food? | expense, budget, attendance, income, member | 2163 字符 | 约 0.6 秒 |
  | toxicology | Which molecules contain chlorine atoms? | atom, connected, bond | 1408 字符 | 约 0.6 秒 |
  | toxicology | What kinds of bonds connect two carbon atoms? | connected, atom, bond, molecule | 1724 字符 | 约 0.5 秒 |

  第一题仍没有 event 表、第三题没有 molecule 表——这正是工单 09 要量的"表没拿全"。
- 发现：用户的 shell 环境里开着 LangSmith 追踪（`LANGCHAIN_TRACING_V2=true`，项目 ragStudy），图的每次运行都会把输入、状态和输出上传到 LangSmith，包括问题原文、Schema、Hit 正文、SQL 和结果行；本月额度用完后返回 429。不是项目代码设的，已报告用户决定怎么处理。本工单的测试和跑通都在命令前加了 `LANGCHAIN_TRACING_V2=false`。
- 测试：`LANGCHAIN_TRACING_V2=false pytest -q --ignore=tests/integration` 547 通过、1 失败（一直就有的 `test_console_and_module_share_help`）、1 跳过；`pytest tests/integration --require-postgres` 119 通过。
