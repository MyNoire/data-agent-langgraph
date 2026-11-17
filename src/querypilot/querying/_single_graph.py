"""要求显式指定数据库的单条线性 Text2SQL 工作流。

此图复用 Phase 0 的模型适配器、AST 校验器和只读执行器。走哪条路径在编译时由 context_source 选定，
没有条件边。目前不含选库、重试、checkpoint 或长期记忆。
"""

from collections.abc import Callable
from dataclasses import asdict
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from querypilot.databases.api import execute_sql, format_schema, get_schema, get_table_ddl, validate_sql
from querypilot.retrieval.api import (
    KnowledgeRetrievalError, KnowledgeScope, RetrievalHit, RetrievalMethod, RetrievalRequest, RetrievalResult,
    hit_table, load_scope_chunks, load_table_chunks, retrieve_knowledge,
)
from querypilot.runtime.api import Settings
from querypilot.sql_generation.api import SqlGenerator, generate_sql

from ._context import assemble_context
from ._render import StructureFormat, render_context
from .models import QueryContext, QueryInput, QueryOutput, QueryState

GRAPH_VERSION = "single-v2"
"""查询图的实现版本，写进运行配置和评测报告；图的路径或节点职责变了就升版本。"""

ContextSource = Literal["schema", "ddl", "knowledge", "retrieval"]
"""模型收到的 Schema 来源，编译图时选定；取值含义见 build_query_graph。"""

RETRIEVAL_TOP_K = 10
"""retrieval 来源每次检索取的 Hit 数，固定不变，写进评测报告。"""


def query_result_row_count(output: Any) -> int | None:
    """从 QueryPilot 输出中只提取安全的行数。

    参数:
        output: 节点增量或图最终输出，也可能不是字典。
    返回:
        result.row_count 为非负整数时返回该值，否则返回 None。
        不修改 output，不返回 SQL、列名或结果行。
    """
    result = output.get("result") if isinstance(output, dict) else None
    count = result.get("row_count") if isinstance(result, dict) else None
    return count if type(count) is int and count >= 0 else None


def build_query_graph(
    settings: Settings,
    *,
    generator: SqlGenerator = generate_sql,
    run_context=None,
    context_source: ContextSource = "schema",
    knowledge_reader: Callable[[KnowledgeScope], list[RetrievalHit]] = load_scope_chunks,
    structure_format: StructureFormat = "columns",
    retriever: Callable[[RetrievalRequest], RetrievalResult] = retrieve_knowledge,
    table_chunk_reader: Callable[[KnowledgeScope, list[str]], dict[str, str]] = load_table_chunks,
    method: RetrievalMethod = "hybrid",
    rerank: bool = True,
) -> CompiledStateGraph:
    """编译 Schema → 模型 → SQL 校验 → 只读执行的线性图。

    参数:
        settings: 数据目录、模型连接信息及 SQL 执行上限。
        generator: SQL 生成函数，返回 GeneratedSql；默认调用真实模型，测试可注入固定结果。
        run_context: 传给只读执行器的运行上下文；为 None 时每次执行各自开一次运行记录。
        context_source: 模型收到的 Schema 来源，编译时选定。"schema" 为每张表的表名和列名，
            "ddl" 为每张表的 CREATE TABLE 原文，"knowledge" 为这个库的全部知识经组装和渲染的文本，
            "retrieval" 为用问题原文检索一次、只含被命中的表的文本。
        knowledge_reader: knowledge 来源读出一个 Knowledge Scope 下全部 Chunk 的函数；默认读知识库，
            测试可注入固定结果。其他来源不调用。
        structure_format: knowledge 和 retrieval 来源里表结构的写法，见 StructureFormat；其他来源忽略。
        retriever: retrieval 来源的检索函数，收一个 RetrievalRequest、返回 RetrievalResult；默认
            retrieve_knowledge（每次按配置新建 Embedder 和 Reranker），评测可传入共用客户端的版本。
        table_chunk_reader: retrieval 来源按表名读表 Chunk 的函数，参数为 (Scope, 表名列表)。
        method, rerank: retrieval 来源的检索方法和是否重排，默认 hybrid + 重排；top_k 固定为 RETRIEVAL_TOP_K。
    返回:
        可调用的已编译 LangGraph；输入为 QueryInput，输出为 QueryOutput。knowledge 和 retrieval 来源
        调用时要传 context=QueryContext(knowledge_scopes=...)，其他来源不需要。
    异常:
        ValueError: context_source 不是上面列出的取值。
    说明:
        凭据和数据库路径留在节点闭包内，不写入图状态；Knowledge Scope 只经运行时 context 传入，
        也不写入图状态。
    """
    def load_columns(state: QueryState) -> dict[str, Any]:
        """读取已登记数据库每张表的表名和列名。

        参数:
            state: 至少包含 database_id 的图状态。
        返回:
            状态增量：schema 为 format_schema 的文本，allowed_tables 为表名列表。
        异常:
            RegistryError: database_id 未登记或登记路径无效。
        """
        schema = get_schema(state["database_id"], settings)
        return {"schema": format_schema(schema), "allowed_tables": list(schema)}

    def load_ddl(state: QueryState) -> dict[str, Any]:
        """读取已登记数据库每张用户表的建表语句。

        参数:
            state: 至少包含 database_id 的图状态。
        返回:
            状态增量：schema 为按表名排序、以空行分隔的 CREATE TABLE 原文，
            allowed_tables 为表名列表（不含 sqlite_ 内部表），table_ddl 为表名 → CREATE TABLE 原文。
        异常:
            RegistryError: database_id 未登记或登记路径无效。
        """
        table_ddl = get_table_ddl(state["database_id"], settings)
        return {"schema": "\n\n".join(table_ddl.values()), "allowed_tables": list(table_ddl), "table_ddl": table_ddl}

    def load_knowledge(state: QueryState, runtime: Runtime[QueryContext]) -> dict[str, Any]:
        """按运行时 context 里当前库的 Knowledge Scope 读出全部 Chunk。

        参数:
            state: 至少包含 database_id 的图状态。
            runtime: context 为 QueryContext，提供"database_id → Knowledge Scope"对照表。
        返回:
            状态增量：hits 为全部 Chunk（当作 Hit），table_chunks 为其中表 Chunk 的表名 → 正文。
            Scope 本身不写入状态。
        异常:
            KeyError: 对照表里没有当前库；此时不读知识、不调用模型。
            KnowledgeRetrievalError: 读取失败，类别沿用读取层。
        """
        read_scope = runtime.context.knowledge_scopes[state["database_id"]]
        hits = knowledge_reader(read_scope)
        table_chunks = {hit.table_name: hit.content for hit in hits if hit.knowledge_type == "table"}
        return {"hits": hits, "table_chunks": table_chunks}

    def retrieve(state: QueryState, runtime: Runtime[QueryContext]) -> dict[str, Any]:
        """用问题原文在当前库的 Knowledge Scope 里检索一次，再读出 Hit 所属各表的表 Chunk。

        参数:
            state: 含 database_id 和 question 的图状态。
            runtime: context 为 QueryContext，提供"database_id → Knowledge Scope"对照表。
        返回:
            状态增量：hits 为检索命中的 Hit，table_chunks 为它们所属各表的表名 → 表 Chunk 正文。
            Scope 本身不写入状态。
        异常:
            KeyError: 对照表里没有当前库；此时不检索、不调用模型。
            KnowledgeRetrievalError: 没有命中（no_match），或检索失败（input_error、embedding_error、
                rerank_error 及读取层的类别）。都让请求失败，不退回其他来源。
        """
        scope = runtime.context.knowledge_scopes[state["database_id"]]
        result = retriever(RetrievalRequest(query=state["question"], scope=scope, method=method, rerank=rerank,
                                            top_k=RETRIEVAL_TOP_K))
        if result.outcome == "no_match":
            raise KnowledgeRetrievalError("no_match", "检索没有命中任何知识")
        # 按第一次出现的顺序去重：一张表的多个 Hit 只读一次表 Chunk。
        tables = list(dict.fromkeys(hit_table(hit) for hit in result.hits))
        return {"hits": result.hits, "table_chunks": table_chunk_reader(scope, tables)}

    def build_context(state: QueryState) -> dict[str, str]:
        """把 Hit 组装成 Retrieval Context，按编译时的结构写法渲染成给模型的 Schema。

        参数:
            state: 含 hits、table_chunks 和 load_schema 产出的 table_ddl 的图状态。
        返回:
            状态增量：schema 为渲染后的文本，替换 load_schema 写的那份。
        """
        table_context = assemble_context(state["hits"], state["table_chunks"])
        return {"schema": render_context(table_context, structure_format=structure_format,
                                         table_ddl=state["table_ddl"])}

    def generate(state: QueryState) -> dict[str, Any]:
        """根据问题和 Schema 生成候选 SQL，不注入 oracle evidence。

        参数:
            state: 含 question 和 load_schema 产出的 schema 的图状态。
        返回:
            状态增量：generated_sql 候选语句，以及模型接口报告的 prompt_tokens 和
            completion_tokens（接口没有返回用量时为 None）。
        异常:
            模型调用失败时向上抛出适配器异常，本阶段不重试。
        """
        generated = generator(state["question"], state["schema"], "", settings)
        return {"generated_sql": generated.sql, "prompt_tokens": generated.prompt_tokens,
                "completion_tokens": generated.completion_tokens}

    def validate(state: QueryState) -> dict[str, str]:
        """校验候选 SQL 的 AST 并施加行数上限。

        参数:
            state: 含 generated_sql 和 allowed_tables 的图状态。
        返回:
            含 sql 字段的状态增量；该字段是允许执行的受限 SQL。
        异常:
            SqlRejected: 语句不是受允许的单条只读查询。
        """
        return {"sql": validate_sql(state["generated_sql"], set(state["allowed_tables"]), settings.max_rows)}

    def execute(state: QueryState) -> dict[str, dict[str, Any]]:
        """使用 Phase 0 只读执行器运行通过校验的 SQL。

        参数:
            state: 含 database_id 和校验后 sql 的图状态。
        返回:
            含 result 的状态增量；result 包含 SQL、列、行数和耗时。
        异常:
            QueryLimitError: 查询超时或响应超限。
            sqlite3.DatabaseError: SQLite 无法执行该查询。
        """
        result = execute_sql(state["database_id"], state["sql"], settings, run_context=run_context)
        return {"result": asdict(result)}

    # 每种来源在 generate_sql 之前的节点；之后的三个节点所有来源相同。
    prefix = {
        "schema": [("load_schema", load_columns)],
        "ddl": [("load_schema", load_ddl)],
        "knowledge": [("load_schema", load_ddl), ("load_knowledge", load_knowledge), ("build_context", build_context)],
        "retrieval": [("load_schema", load_ddl), ("retrieve", retrieve), ("build_context", build_context)],
    }
    if context_source not in prefix:
        raise ValueError(f"unknown context_source {context_source!r}; expected one of {sorted(prefix)}")
    builder = StateGraph(QueryState, input_schema=QueryInput, output_schema=QueryOutput, context_schema=QueryContext)
    builder.add_sequence(prefix[context_source]
                         + [("generate_sql", generate), ("validate_sql", validate), ("execute_sql", execute)])
    builder.add_edge(START, "load_schema")
    builder.add_edge("execute_sql", END)
    return builder.compile(name="querypilot_single")
