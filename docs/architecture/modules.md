# QueryPilot：从哪里调用一项完整能力

假设你要“根据问题查出统计结果”，调用 querying.ask 即可：它负责读结构、生成 SQL、检查安全性和只读执行。调用者不用自己拼步骤。要“从批准资料生成知识快照”，则调用 knowledge.build_corpus。两者互不依赖，业务查询不会因为没有 PostgreSQL 配置而无法启动。

跨模块从 api.py 导入。一个模块可以有多个清楚的操作，不需要把功能塞进一个函数，也不要求所有结果长成同一个样子。内部按需要拆文件、用合适技术；调用者只依赖公开操作和领域类型。

## 目录与分工

~~~text
src/querypilot/
  entrypoints/       HTTP、命令解析和错误映射
  querying/          组织一次自然语言查询，保留原四节点图
  sql_generation/    提示词、模型请求、SQL 文本清理
  databases/         数据库登记、结构、安全检查、只读执行
  evaluation/        对外统一出口，内部三项职责分开
    sql/             已有 direct/graph SQL 结果评测与报告
    retrieval/       检索评测：题集准备、逐题检索、计分与报告
    datasets/        既有 BIRD 数据准备工具
  knowledge/
    knowledge_catalog/    提取结构、导入说明、应用批准修正
    knowledge_corpus/     渲染、生成身份、构建和校验 Corpus（知识语料）
    knowledge_store/      PostgreSQL 导入、复验、限定只读会话、写入向量
    knowledge_embedding/  Embedder 接口、硅基流动 Embedding 客户端
  retrieval/         限定 Knowledge Scope 的 BM25 / 向量 / 混合检索和可选的重排（retrieve_knowledge）
  runtime/           配置、运行目录、事件、审计、版本信息
~~~

查询依赖生成、数据库和检索（检索得到的 Hit 在查询里组装成给模型的 Schema；查询不直接导入 knowledge）；评测调用这些正式能力。正式查询不向评测借生成器。知识快照依赖知识目录，存储依赖快照；检索从存储借用限定只读会话。runtime 不导入业务模块。

tests/architecture/test_module_boundaries.py 检查方向、跨模块私有访问、显式导出泄漏和循环，也识别 evaluation 的三个子目录；同在 evaluation 内不代表可以互读内部实现。测试为检查安全细节可以访问本能力内部；生产调用走公开入口。

## 查询、生成和数据库

| 从哪里导入 | 操作与返回 | 失败和副作用 |
| --- | --- | --- |
| querying.api | ask(database_id, question, settings, *, generator=..., run_context=None) → 原 sql/result | 创建查询记录；模型和数据库错误保留；没有检索、修复、重试 |
| querying.api | build_query_graph(settings, *, generator=..., run_context=None, context_source="schema", knowledge_reader=load_scope_chunks, structure_format="columns", retriever=retrieve_knowledge, table_chunk_reader=load_table_chunks, method="hybrid", rerank=True) → 编译图；GRAPH_VERSION 为图版本，ContextSource 为可选的 Schema 来源（schema / ddl / knowledge / retrieval），RETRIEVAL_TOP_K 为检索取的 Hit 数（10）；QueryContext 是运行时 context（"database_id → Knowledge Scope"对照表） | 编译不建运行目录；手动执行时提供任务上下文；不认识的 context_source 在编译时抛 ValueError。knowledge 路径为 load_schema → load_knowledge → build_context，retrieval 路径为 load_schema → retrieve → build_context，两者调用时都传 context=QueryContext(...)；对照表里没有当前库时抛 KeyError，检索没有命中或失败时抛 KnowledgeRetrievalError（no_match / input_error / embedding_error / rerank_error 等），都不调用模型、不退回其他来源；Scope 不进图状态和输出；retrieval 只给模型被命中的表，AST 检查仍允许这个库的全部表；编译时用 `langsmith.configure(enabled=False)` 关闭本进程的 LangSmith 追踪，环境变量打开了也不上传（运行数据里有问题原文、Schema、Hit 正文、SQL 和结果行） |
| querying.api | start_serving(path=config/active_corpus.json) → QueryContext：用户端启动时读一次 Active Corpus | 只读配置文件，不热加载、不连知识库；关闭本进程的 LangSmith 追踪；文件缺失、不可读或不符合格式时抛 KnowledgeRetrievalError（configuration_error）。目前没有入口调用它：ask 不读这份配置，评测也不读 |
| querying.api | build_retrieval_tool(*, retriever=retrieve_knowledge, table_chunk_reader=load_table_chunks) → LangChain tool `search_knowledge`：模型只给 query，拿到当前库的候选知识 | 库取自运行状态的 database_id，Knowledge Scope 取自运行时 context（QueryContext），模型多给的参数不生效；固定 hybrid + 重排、top_k=RETRIEVAL_TOP_K、三种知识类型、不展开；返回命中的表名（按名次）、"候选不保证足以回答"的固定说明和 columns 写法的 Retrieval Context，后者与 retrieval 来源逐字相同；不设分数阈值。tool 自己不关追踪，用户端先调用 start_serving 再使用它。失败还没有分类：当前库不在对照表里时抛 KeyError，检索失败时 KnowledgeRetrievalError 原样抛出。目前没有调用方：没有接进查询图，ask 也不用它 |
| querying.api | assemble_context(hits, table_chunks) → list[TableContext]；render_context(context, *, structure_format, table_ddl=None) → Schema 文本（规则版本 SCHEMA_RENDER_VERSION） | 都是纯函数：前者把 Hit 按所属的表归拢成 Retrieval Context（关系归源表），后者按行标签筛选 Chunk 正文，columns / ddl 两种结构写法；图的 build_context 节点调用它们 |
| sql_generation.api | direct_prompt → 文本；generate_sql(question, schema, evidence, settings) → GeneratedSql（SQL 和接口报告的 token 用量） | 前者纯计算，后者请求模型；direct-v2、超时与客户端参数保持 |
| databases.api | DatabaseRegistry(data_dir).list/register/get → 登记映射或路径 | 注册写索引；不接受 data_dir 以外的源 |
| databases.api | get_schema(database_id, settings) → 表名到列名；format_schema → 文本；get_table_ddl(database_id, settings) → 表名到 CREATE TABLE 原文 | 自行定位；未知 ID 抛 RegistryError；结构读取不建任务 |
| databases.api | validate_sql → 受限 SQL；execute_sql(database_id, sql, settings, *, run_context=None) → QueryResult | 自行定位、检查和只读执行；保留行数、时间、字节限制 |

QueryResult 保留 sql/columns/rows/row_count/elapsed_ms；数据库 API 中的二进制字段仍是原 bytes。私有 JSON 归档对二进制值使用可逆的 base64 标签，普通文本与数值不改；HTTP 保留原来的成功序列化。错误包括 RegistryError、SqlRejected、QueryLimitError 和 SQLite 错误，HTTP 在入口层转换状态码。

以下例子只用临时数据库和固定生成器，不调用模型：

~~~python
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from querypilot.runtime.api import Settings
from querypilot.databases.api import DatabaseRegistry, SqlRejected, execute_sql
from querypilot.querying.api import ask

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    database = root / "demo.sqlite"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE items(id INTEGER)")
        connection.execute("INSERT INTO items VALUES (7)")
        connection.commit()
    settings = Settings(_env_file=None, data_dir=root, artifacts_dir=root / "artifacts")
    DatabaseRegistry(root).register("demo", database)
    def fixed_sql(question, schema, evidence, settings):
        return "SELECT id FROM items"
    result = ask("demo", "列出编号", settings, generator=fixed_sql)
    assert result["result"]["rows"] == [[7]]
    try:
        execute_sql("demo", "DELETE FROM items", settings)
    except SqlRejected:
        pass  # 预期失败；数据没有被删除。
~~~

## 知识准备：一层总入口，三个子能力

普通调用从 querypilot.knowledge.api 导入；需要明确子能力时用对应子目录的 api。子能力走兄弟 api，不回导总入口。

| 能力 | 完整操作与结果 | 读取、写出与错误 |
| --- | --- | --- |
| knowledge_catalog | extract_catalog(database_id, path) → Catalog；enrich_catalog(catalog, description_dir) → DescriptionImportResult | SQLite/CSV 只读；保留来源和警告；CatalogError/DescriptionError，以及文件读取或 SQLite 错误 |
| knowledge_catalog | curate_catalog(base_file, source_dir, plan_file, output_dir, *, settings=None, run_context=None) → CurationSummary | 应用批准规则到新目录；冲突与防覆盖保留；CorrectionError |
| knowledge_catalog | verify_sources(enriched, source_bytes) → SourceVerification | 完整核对 hash、行位置、单元格和表列绑定；无 I/O；SourceVerificationError |
| knowledge_catalog | collect_value_samples(database_id, path, *, private_columns) → ValueSamples | 只读打开 SQLite，按规则 `value-samples-v1` 逐列取值；隐私列不查询，名单里有不存在的列时 ValueError；SQLite 错误照常抛出 |
| knowledge_corpus | render_knowledge → KnowledgeDraft | 读取批准包和语义配置；不自动发布正式快照；KnowledgeError |
| knowledge_corpus | build_corpus(bundle_dir, semantic_file, output_dir, database_id, config=None, *, settings=None, run_context=None, values_file=None) → BuildSummary | 来源核对、渲染、身份和发布；可选的取值样例文件进入快照和 `corpus_id`；返回 built/reused；CorpusError |
| knowledge_corpus | verify_corpus(corpus_dir) → VerificationSummary；load_corpus(corpus_dir) → VerifiedCorpus | 每次从目录重新读取核对；普通库调用不建任务；CorpusError |
| knowledge_store | manage_store(operation, storage_settings, *, run_context=None) → ManagementSummary | 只执行 bootstrap/migrate/check；所选操作才加载 PG 驱动 |
| knowledge_store | import_corpus(corpus_dir, storage_settings, *, run_context=None) → ImportSummary；verify_import(...) → ImportVerificationSummary | 先重读快照；原事务锁、全字段回读、imported/reused 和未知提交语义保留；KnowledgeStorageError |
| knowledge_store | open_read_scope(scope, storage_settings, limits=None) → 上下文 ReadSession | 一个只读一致事务；关闭后不可复用；KnowledgeReadError |
| knowledge_store | embed_corpus(scope, storage_settings, embedder, *, run_context=None) → EmbeddingSummary | reader 读 Chunk，事务外调用 embedder，importer 一个事务写入全部向量；已有为 exists；KnowledgeReadError/EmbeddingError/KnowledgeStorageError |
| knowledge_embedding | Embedder（profile_id、embed_documents、embed_query）；OpenAIEmbedder(EmbeddingSettings()) | 读 QUERYPILOT_EMBEDDING_*；查询前拼任务指令（`QUERY_TASK`，重排也用这一句），文档原样；条数、维度不对或请求失败为 EmbeddingError |

例如，准备好批准包 bundle 和语义文件 semantics.json 后：

~~~python
from pathlib import Path
from querypilot.knowledge.api import build_corpus, verify_corpus, CorpusError

summary = build_corpus(Path("bundle"), Path("semantics.json"), Path("new-corpus"), "demo")
assert summary.outcome in {"built", "reused"}
checked = verify_corpus(Path("new-corpus"))
try:
    verify_corpus(Path("missing-corpus"))
except CorpusError:
    pass  # 缺文件或内容不符会拒绝，不拿上次摘要代替本次核验。
~~~

这些目录必须包含真实批准资料，不能用空目录代替。相同批准输入和语义配置产生同一快照身份，运行 ID 不参与身份。已有目标完整且与候选一致时只读复用，损坏或不同则拒绝。来源变化时构建到新目标，再导入 PG；没有新增后台巡检。VerifiedCorpus 是本次核验后持有的对象，不承诺嵌套数据深不可变，不应修改它。

读会话只暴露 scope/counts/search_bytes 和有界 fetch_one/fetch_rows，返回普通映射，不给连接、游标或 commit。SQL 必须来自受控源码，问题只当绑定参数；查询构造者仍须绑定 scope。它不替代上层授权。

PG 的真实事务和权限必须在专用测试库验证；替身只能证明接线。

### 怎样判断每项知识操作成功或失败

- 提取：给 extract_catalog("demo", SQLite路径) 返回 demo 的表、列和外键；缺失路径抛 FileNotFoundError，非 SQLite 文件抛 sqlite3.DatabaseError；不支持的结构对象等限制用 CatalogError 表示，不生成空目录冒充成功。
- 整理说明：enrich_catalog(已有目录, CSV目录) 返回增强目录和警告；未匹配列、字符警告按原规则隔离，格式或来源协议错误由 DescriptionError 报告。
- 批准修正：给 curate_catalog 指定原始目录、CSV、已批准计划和新目标，返回 changes/rules/remaining_warnings；原单元格与计划不符或目标已存在时拒绝，不能自行猜改。
- 来源核对：verify_sources(增强目录, 文件名到原字节的映射) 返回 verified 和来源/记录数；真实修改其中 CSV 字节后，会在 hash、行或字段绑定处拒绝。
- 渲染/构建：给相同批准包和语义文件，render 返回草稿，build 返回 built 或 reused；缺少来源、引用错误或已有目标冲突报领域错误，不改旧目标。
- 管理：manage_store("check", 存储配置) 检查当前迁移版本；缺对应账号配置或版本冲突时 KnowledgeStorageError 明确分类。bootstrap/migrate 只在显式选择时执行。
- 导入/复验：import_corpus(合法快照, 存储配置) 首次返回 imported，再次相同输入返回 reused；verify_import 返回 verified。源被损坏时先报 source_invalid，不进入 PG；存储内容不符时拒绝，不覆盖修复。
- 限定读取：with open_read_scope(含 corpus_id/database_id/schema_version 的 KnowledgeScope, 存储配置) as session 在同一事务核对范围；错误三元组、状态或容量会拒绝，退出后 session 不能再取数据。SQL 必须由源码构造，不能把用户问题传到语句位置。

这些是所声明前置下的完整操作例子。可重复的原创资料在 tests/conftest.py 及知识契约测试中构造；调用方无需依赖它们的内部辅助函数。

## 评测与报告

querypilot.evaluation.api 提供 run_direct/run_graph，返回原 EvalReport；run_graph 可用 workers 同时跑多道题，报告仍按题集顺序；context_source 传给查询图并记进报告。knowledge 和 retrieval 来源另传 structure_format 和"db_id → Knowledge Scope"对照表（命令行用 knowledge_scopes_from_cases 从检索题集读出），三者连同渲染版本记进运行配置和报告；对照表缺了题集用到的库时在建立运行记录之前报 ValueError。retrieval 来源另传 method / rerank（默认 hybrid + 重排），记 method、rerank、top_k，报告的 retrieval 列出实际用到的检索版本（方法、两个 Profile、检索配置版本）；整次运行用 shared_retriever 共用一组 Embedder 和 Reranker。逐题的 error_type 对带类别的错误（检索、知识读取）记类别，否则记异常类名。retrieval 另传题号 → Relevant Set 的表（命令行用 relevant_tables_from_cases 从检索题集读出），逐题记 tables_complete，报告按拿全 / 没拿全汇总。因此 evaluation.sql 也可以从 retrieval 的公开入口导入。每道题按两个口径计分：strict_execution_match（严格执行匹配，记为 execution_match）和 content_match（内容匹配，ADR 0003）；两个比较函数都从 evaluation.api 导出，离线重算脚本也调用它们。sql_run_config 是两者和命令行共用的运行配置。select_database_cases 从 BIRD 题目文件生成指定数据库的 SQL 题集和 manifest。fixture 把金标准 SQL 交回执行器，只检查评测管线，分数不能当模型能力。direct 在查库或读取结构失败时中止批次；进入逐题处理后，生成服务和执行错误都会记录后继续。graph 的查库也在逐题错误处理内，因此未知库会记错后继续下一题。两者保留原来的差异。

这些完整操作只在 `evaluation/sql/api.py` 实现，根 `evaluation.api` 普通重导出同一个函数和类型，不再包一层转发。调用方原有导入不变；已有 generator 参数继续用于固定生成器注入。SQL 批次沿用 operation=evaluation，实际最外层配置标明 mode=sql；完整无预测报告仍是完成批次，不因搬迁改成命令失败。

read_report(source) 显式读取旧 JSON 或完整的新评测运行目录，不寻找“最新”。export_public(source, destination) 按原白名单排他写新文件，仍可能含 predicted_sql，分享前要检查；不会上传。

BIRD 的 fetch/import/select/freeze/prepare 逻辑归 evaluation/datasets；命令层只适配参数。搬迁没有重新下载、选题或冻结正式数据。

检索评测归 `evaluation/retrieval`，按需离线运行：`prepare_retrieval_cases` 从 BIRD 题目和 Corpus 生成检索题集（`eval retrieval-cases`），`evaluate_retrieval` 按 `method`（lexical / vector / hybrid）和 `rerank`（是否重排，默认关闭）用 `workers` 个线程（默认 1）对每道题调用一次 `retrieve_knowledge`，在主线程里按 GLOSSARY「评测」的口径计分（`eval retrieval --method ... [--rerank] [--workers N]`），vector 和 hybrid 整次运行共用一个 Embedder，开启重排时整次运行共用一个 Reranker，它们会被多个线程同时使用；报告按题集顺序写进运行记录。SQL 报告不接收检索分数，SQL 的延迟计算也不为此改公式。

检索本体的 `retrieval.api` 导出 `load_scope_chunks`（读出一个 Knowledge Scope 的全部 Chunk，按 chunk_key 排序当作 Hit 返回，给查询图的 knowledge 来源用；行数和字节数受读取层上限约束）、`load_table_chunks`（按表名读一个 Scope 下的表 Chunk，给 retrieval 来源补齐表级知识）、`shared_retriever`（一次批量运行共用一组 Embedder 和 Reranker 的检索函数，检索评测和 SQL 评测都用它）、`retrieve_knowledge`（限定 Knowledge Scope，按 `method` 做 BM25、向量或 RRF 混合检索；向量和混合检索可传入 Embedder，没传时按配置创建；请求开启 `rerank` 时用 Reranker 对候选重排，没传时按 `QUERYPILOT_RERANK_*` 创建）和它的请求、结果、Hit、错误与 ScopeFile 类型，以及 `Reranker` 接口、`RerankError`、硅基流动的 `SiliconFlowReranker` 和它的配置 `RerankSettings`；`KnowledgeScope` 也从这里导出，查询图和 SQL 评测不直接导入 knowledge。参数和 scope 文件解析位于 `entrypoints/commands/retrieval.py`，安全解析器在 `entrypoints/_arguments.py` 共用。统一检索命令还没有接进 CLI，不公开内部查询或会话辅助函数凑接口。

## 一次任务的记录归谁管

ask/execute_sql/curate_catalog/build_corpus/manage_store/import_corpus/verify_import 和评测都是任务型操作。没上下文时自行创建并结束；上层传入 RunContext 时只借用，由创建者发布最终结果并关闭。一次评测各题共享批次目录，图事件用“批次:case:序号”区分。

普通提取、渲染、校验和图编译不偷偷创建另一份任务；作为独立命令时由命令层持有上下文。runtime.api.task_run 管理配置、事件和终态；RunArtifactError 单独表达记录失败。比如数据库操作成功而结果文件发布失败，保留“操作成功、产物失败”，不宣称回滚。原业务错误与记录错误同时发生时，业务错误优先。

目录、隐私、事件字段和接口见[运行记录与事件](observability.md)。全部命令见 `querypilot --help`。
