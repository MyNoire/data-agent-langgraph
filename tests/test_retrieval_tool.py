"""经 ToolNode 发起一次 tool 调用，验证模型从检索 tool 拿到什么。只替换检索和表 Chunk 读取，不联网。

ToolNode 在这里只充当调用方：tool 没有接进查询图。
"""

import json
import re
from typing import Annotated, TypedDict

import langsmith
import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langsmith.run_helpers import get_current_run_tree
from langsmith.utils import get_env_var, tracing_is_enabled

from querypilot.querying.api import QueryContext, start_serving
from querypilot.retrieval.api import RetrievalRequest

from test_graph_knowledge_source import CUSTOMERS_TABLE, ORDERS_TABLE, SHOP_SCOPE, _hit
from test_graph_retrieval_source import ORDERS_HITS, _recording_retriever, _recording_table_reader

ORDERS_CONTEXT = '''表 "orders"
  表用途: 记录客户下的订单及金额。
  记录粒度: 一笔订单。
  字段目录: "id", "customer_id", "total"
  主键: "id"
  外键: "customer_id" → "customers" ("id")
  列 "total" REAL: 订单金额，单位为元
  关系 "orders"."customer_id" → "customers"."id": 订单属于该客户。'''

# 只有表 Hit 的表：只有表级几行（与知识来源测试里 customers 的渲染相同）。
CUSTOMERS_CONTEXT = '''表 "customers"
  表用途: 记录客户及其姓名。
  记录粒度: 一位客户。
  字段目录: "id", "name"
  主键: "id"
  外键: 未声明'''


class _AgentState(TypedDict):
    """调用方的运行状态：当前库和对话消息。"""

    database_id: str
    messages: Annotated[list, add_messages]


def _serving(tmp_path) -> QueryContext:
    """按用户端的方式启动：配置里 club 在前、shop 在后，所以"取第一个库"的写法拿不到 shop。"""
    path = tmp_path / "active_corpus.json"
    path.write_text(json.dumps({
        "club": {"corpus_id": "c" * 64, "schema_version": "d" * 64},
        "shop": {"corpus_id": SHOP_SCOPE.corpus_id, "schema_version": SHOP_SCOPE.schema_version},
    }), encoding="utf-8")
    return start_serving(path)


def _call_tool(tool, context: QueryContext, args: dict, database_id: str = "shop") -> ToolMessage:
    """模拟模型发起一次 tool 调用：args 是模型给的参数，database_id 在运行状态里。返回模型会收到的消息。"""
    builder = StateGraph(_AgentState, context_schema=QueryContext)
    builder.add_node("tools", ToolNode([tool]))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    call = AIMessage(content="", tool_calls=[{"name": tool.name, "args": args, "id": "call-1"}])
    output = builder.compile().invoke({"database_id": database_id, "messages": [call]}, context=context)
    return output["messages"][-1]


def test_model_gives_only_a_query_and_gets_the_knowledge_of_the_current_database(tmp_path):
    """输入：运行状态里是 shop；模型只给 {"query": "order totals"}；检索命中 orders 的一列和一条关系。

    输出：
    - 模型收到的文本里有 orders 的 Retrieval Context，与检索来源逐字相同。
    - 检索收到模型写的查询原文，和运行时 context 里 shop 的 Scope（不是配置里排第一的 club）。
    - 表 Chunk 也按 shop 的 Scope 读，两个 Hit 同属 orders，只读一次。
    """
    from querypilot.querying.api import build_retrieval_tool

    requests, reads = [], []
    tool = build_retrieval_tool(retriever=_recording_retriever(requests, ORDERS_HITS),
                                table_chunk_reader=_recording_table_reader(reads, {"orders": ORDERS_TABLE}))

    message = _call_tool(tool, _serving(tmp_path), {"query": "order totals"})

    assert ORDERS_CONTEXT in message.content
    assert [(request.query, request.scope) for request in requests] == [("order totals", SHOP_SCOPE)]
    assert reads == [(SHOP_SCOPE, ["orders"])]


def test_the_model_sees_query_as_its_only_parameter():
    """输入：构建出的 tool，按 bind_tools 发给模型的格式转换。

    输出：参数只有 query，而且必填；运行时不在里面，库、Scope 和检索配置也都不是参数。
    """
    from querypilot.querying.api import build_retrieval_tool

    parameters = convert_to_openai_tool(build_retrieval_tool())["function"]["parameters"]

    assert list(parameters["properties"]) == ["query"]
    assert parameters["required"] == ["query"]


def test_extra_arguments_from_the_model_change_neither_the_database_nor_the_retrieval_settings(tmp_path):
    """输入：运行状态里是 shop；模型除了 query，还伪造了库、Scope、检索方法、重排、top_k、知识类型和运行时。

    输出：检索收到的请求与只给 query 时完全相同——shop 的 Scope，hybrid + 重排，top_k=10，三种知识类型；
    表 Chunk 也按 shop 读。
    """
    from querypilot.querying.api import build_retrieval_tool

    requests, reads = [], []
    tool = build_retrieval_tool(retriever=_recording_retriever(requests, ORDERS_HITS),
                                table_chunk_reader=_recording_table_reader(reads, {"orders": ORDERS_TABLE}))
    forged = {"query": "order totals", "database_id": "club",
              "scope": {"corpus_id": "c" * 64, "database_id": "club", "schema_version": "d" * 64},
              "method": "lexical", "rerank": False, "top_k": 99, "knowledge_types": ["table"], "runtime": "x"}

    _call_tool(tool, _serving(tmp_path), forged)

    assert requests == [RetrievalRequest(query="order totals", scope=SHOP_SCOPE, method="hybrid", rerank=True,
                                         top_k=10, knowledge_types=["table", "column", "relationship"])]
    assert reads == [(SHOP_SCOPE, ["orders"])]


def test_the_model_also_gets_the_hit_tables_and_a_note_that_the_candidates_may_not_be_enough(tmp_path):
    """输入：检索命中 orders 的一列、一条关系（名次 1、2），以及 customers 的表 Chunk（名次 3）。

    输出：模型收到的文本依次是：命中的表名（按名次先后）、一句固定说明、空行、两张表的 Retrieval Context
    （与检索来源逐字相同，表之间空一行）。没有分数、Chunk ID 或 Scope。
    """
    from querypilot.querying.api import build_retrieval_tool

    hits = [*ORDERS_HITS, _hit(3, ["table", "shop", "main", "customers"], CUSTOMERS_TABLE, "customers")]
    tool = build_retrieval_tool(retriever=_recording_retriever([], hits),
                                table_chunk_reader=_recording_table_reader(
                                    [], {"orders": ORDERS_TABLE, "customers": CUSTOMERS_TABLE}))

    message = _call_tool(tool, _serving(tmp_path), {"query": "order totals"})

    assert message.content == (
        '命中的表: "orders", "customers"\n'
        "以下是与查询最相关的候选知识，不保证足以回答问题。\n"
        "\n"
        f"{ORDERS_CONTEXT}\n"
        "\n"
        f"{CUSTOMERS_CONTEXT}"
    )


def test_the_tool_is_not_traced_even_when_the_environment_turns_tracing_on(tmp_path, monkeypatch):
    """输入：环境变量打开 LangSmith 追踪；不编译查询图，只做用户端启动，再经 ToolNode 调用一次 tool。

    输出：检索执行时（ToolNode 的工作线程里）追踪是关的，也没有正在记录的追踪。端点指向本机一个不监听的
    端口：即使追踪没关，也不会把内容传到真实的 LangSmith。
    """
    from querypilot.querying.api import build_retrieval_tool

    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.setenv("LANGSMITH_API_KEY", "not-a-real-key")
    get_env_var.cache_clear()            # langsmith 缓存了环境变量的读取结果
    langsmith.configure(enabled=None)    # 回到"按环境变量决定"
    seen = []

    def retriever(request):
        seen.append((tracing_is_enabled(), get_current_run_tree()))
        return _recording_retriever([], ORDERS_HITS)(request)

    try:
        assert tracing_is_enabled() is True
        tool = build_retrieval_tool(retriever=retriever,
                                    table_chunk_reader=_recording_table_reader([], {"orders": ORDERS_TABLE}))

        _call_tool(tool, _serving(tmp_path), {"query": "order totals"})

        assert seen == [(False, None)]
    finally:
        langsmith.configure(enabled=False)
        get_env_var.cache_clear()


def test_a_query_with_no_searchable_words_tells_the_model_to_rewrite_it(tmp_path):
    """输入：模型给的查询只有标点，没有可检索的词。用真实的检索函数，它在连接知识库、调用 Embedding 之前就报错。

    输出：tool 不抛错，返回给模型的文本里有类别 input_error、一句原因，以及"可以改写查询后重试"；
    文本不含查询原文；没有读取表 Chunk。
    """
    from querypilot.querying.api import build_retrieval_tool

    reads = []
    tool = build_retrieval_tool(table_chunk_reader=_recording_table_reader(reads, {}))

    message = _call_tool(tool, _serving(tmp_path), {"query": "¿¡？！"})

    assert "类别: input_error" in message.content
    assert re.search(r"原因: \S", message.content)
    assert "可以改写查询后重试" in message.content
    assert "¿¡？！" not in message.content
    assert reads == []


def test_a_failed_embedding_call_keeps_its_category_and_tells_the_model_not_to_retry(tmp_path):
    """输入：检索层报 embedding_error（Embedding 服务失败，检索层已归类，消息是固定的安全文本）。

    输出：tool 不抛错，返回给模型的文本里类别沿用 embedding_error、原因是检索层的消息，
    并明说"不要重试"，不说"可以改写查询后重试"；文本不含查询原文；没有读取表 Chunk。
    """
    from querypilot.querying.api import build_retrieval_tool
    from querypilot.retrieval.api import KnowledgeRetrievalError

    def failing_retriever(request):
        raise KnowledgeRetrievalError("embedding_error", "Embedding 服务调用失败或返回结果不符合预期")

    reads = []
    tool = build_retrieval_tool(retriever=failing_retriever, table_chunk_reader=_recording_table_reader(reads, {}))

    message = _call_tool(tool, _serving(tmp_path), {"query": "order totals"})

    assert "类别: embedding_error" in message.content
    assert "原因: Embedding 服务调用失败或返回结果不符合预期" in message.content
    assert "不要重试" in message.content
    assert "可以改写查询后重试" not in message.content
    assert "order totals" not in message.content
    assert reads == []


def test_no_candidates_tells_the_model_to_rewrite_the_query(tmp_path):
    """输入：检索正常返回，但没有任何候选（outcome 为 no_match，hits 为空）；检索层不抛错。

    输出：返回给模型的文本里有类别 no_match、一句原因和"可以改写查询后重试"，没有"命中的表"表头；
    文本不含查询原文；只检索一次（不换方法再查，也不自己重试）；没有读取表 Chunk。
    """
    from querypilot.querying.api import build_retrieval_tool

    requests, reads = [], []
    tool = build_retrieval_tool(retriever=_recording_retriever(requests, []),
                                table_chunk_reader=_recording_table_reader(reads, {}))

    message = _call_tool(tool, _serving(tmp_path), {"query": "mars weather"})

    assert "类别: no_match" in message.content
    assert re.search(r"原因: \S", message.content)
    assert "可以改写查询后重试" in message.content
    assert "命中的表: " not in message.content
    assert "mars weather" not in message.content
    assert len(requests) == 1
    assert reads == []


def test_a_database_without_an_active_corpus_is_reported_without_retrieving(tmp_path):
    """输入：运行状态里的库是 california_schools，Active Corpus 配置里只有 club 和 shop。

    输出：tool 不抛错，返回给模型的文本里有类别 scope_not_configured、一句原因和"不要重试"，
    不说"可以改写查询后重试"；没有检索（也就没有改用 club 或 shop 的知识），没有读取表 Chunk。
    """
    from querypilot.querying.api import build_retrieval_tool

    requests, reads = [], []
    tool = build_retrieval_tool(retriever=_recording_retriever(requests, ORDERS_HITS),
                                table_chunk_reader=_recording_table_reader(reads, {"orders": ORDERS_TABLE}))

    message = _call_tool(tool, _serving(tmp_path), {"query": "order totals"}, database_id="california_schools")

    assert "类别: scope_not_configured" in message.content
    assert re.search(r"原因: \S", message.content)
    assert "不要重试" in message.content
    assert "可以改写查询后重试" not in message.content
    assert requests == []
    assert reads == []


@pytest.mark.parametrize("query", ["", "   ", "x" * 2001], ids=["empty", "blank", "over-2000-chars"])
def test_an_empty_or_overlong_query_tells_the_model_to_rewrite_it(tmp_path, query):
    """输入：模型给的查询为空、只有空白，或超过 2000 字。用真实的检索函数，它在连接知识库、调用 Embedding 之前就报错。

    输出：tool 不抛错，也不是框架的传参错误：返回给模型的文本里有类别 input_error、一句原因，以及
    "可以改写查询后重试"；文本不含查询原文；没有读取表 Chunk。
    """
    from querypilot.querying.api import build_retrieval_tool

    reads = []
    tool = build_retrieval_tool(table_chunk_reader=_recording_table_reader(reads, {}))

    message = _call_tool(tool, _serving(tmp_path), {"query": query})

    assert "类别: input_error" in message.content
    assert re.search(r"原因: \S", message.content)
    assert "可以改写查询后重试" in message.content
    if query.strip():
        assert query not in message.content
    assert reads == []


def test_a_hit_table_without_its_table_chunk_is_reported_as_a_knowledge_state_error(tmp_path):
    """输入：检索命中 orders 的一列和一条关系，但按 Scope 读表 Chunk 时读不到 orders（读取层按契约略过不存在的表）。

    输出：tool 不抛错，返回给模型的文本里有类别 knowledge_state_error、一句原因和"不要重试"，
    没有"命中的表"表头，也没有只剩列和关系、缺了表级说明的 Context。
    """
    from querypilot.querying.api import build_retrieval_tool

    reads = []

    def reader_without_orders(scope, tables):
        """与 load_table_chunks 的契约一致：Scope 里没有的表不出现在结果里，不报错。"""
        reads.append((scope, tables))
        return {}

    tool = build_retrieval_tool(retriever=_recording_retriever([], ORDERS_HITS), table_chunk_reader=reader_without_orders)

    message = _call_tool(tool, _serving(tmp_path), {"query": "order totals"})

    assert reads == [(SHOP_SCOPE, ["orders"])]
    assert "类别: knowledge_state_error" in message.content
    assert re.search(r"原因: \S", message.content)
    assert "不要重试" in message.content
    assert "命中的表: " not in message.content
    assert '列 "total"' not in message.content


@pytest.mark.parametrize("args", [{}, {"query": 123}], ids=["missing-query", "query-not-a-string"])
def test_malformed_arguments_get_the_framework_error_and_the_tool_does_not_run(tmp_path, args):
    """输入：模型没给 query，或 query 不是字符串。

    输出：由框架按传参错误回给模型（status 为 error），不是 tool 的失败文本；没有检索，也没有读取表 Chunk。
    不断言框架提示的措辞。
    """
    from querypilot.querying.api import build_retrieval_tool

    requests, reads = [], []
    tool = build_retrieval_tool(retriever=_recording_retriever(requests, ORDERS_HITS),
                                table_chunk_reader=_recording_table_reader(reads, {"orders": ORDERS_TABLE}))

    message = _call_tool(tool, _serving(tmp_path), args)

    assert message.status == "error"
    assert "类别: " not in message.content
    assert requests == []
    assert reads == []


def test_an_unexpected_validation_error_inside_the_tool_reaches_the_caller(tmp_path):
    """输入：检索函数内部意外抛出 pydantic 的 ValidationError（例如返回的结果对象不合格），这是程序错误。

    输出：异常抛到调用方，ToolNode 不把它当成模型传参错误、改成消息回给模型；原来的 ValidationError
    在异常链里（抛出的可以是它本身，也可以是由它引起的其他异常）；也没有读取表 Chunk。
    """
    from pydantic import ValidationError

    from querypilot.querying.api import build_retrieval_tool
    from querypilot.retrieval.api import RetrievalResult

    def broken_retriever(request):
        return RetrievalResult.model_validate({"outcome": "matched"})

    reads = []
    tool = build_retrieval_tool(retriever=broken_retriever, table_chunk_reader=_recording_table_reader(reads, {}))

    with pytest.raises(Exception) as caught:
        _call_tool(tool, _serving(tmp_path), {"query": "order totals"})

    chain, error = [], caught.value
    while error is not None:
        chain.append(error)
        error = error.__cause__
    assert any(isinstance(error, ValidationError) for error in chain)   # 原来的错误留在异常链里，便于排查
    assert reads == []
