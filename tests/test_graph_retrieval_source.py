"""用假 SQL 生成器、假检索器和假表 Chunk 读取验证 retrieval 来源：图用问题检索一次，模型只看到被命中的表。"""

from hashlib import sha256

import pytest

from querypilot.querying.api import QueryContext, build_query_graph
from querypilot.retrieval.api import (
    KnowledgeRetrievalError, KnowledgeScope, RetrievalHit, RetrievalRequest, RetrievalResult,
)

from test_graph_context_source import _recording_generator, _shop
from test_graph_knowledge_source import ORDERS_FK0, ORDERS_TABLE, ORDERS_TOTAL, SHOP_SCOPE, _hit

QUESTION = "List order totals"

# 检索只命中 orders 的一列和一条关系；customers 一个 Hit 都没有。
ORDERS_HITS = [
    _hit(1, ["column", "shop", "main", "orders", "total"], ORDERS_TOTAL, "orders", "total"),
    _hit(2, ["relationship", "shop", "main", "orders", 0], ORDERS_FK0),
]


def _result(request: RetrievalRequest, hits: list[RetrievalHit]) -> RetrievalResult:
    """假检索器的返回值：按请求填好范围和配置，hits 原样放进去。"""
    return RetrievalResult(
        request_id="r1", outcome="matched" if hits else "no_match", scope=request.scope, method=request.method,
        query_sha256=sha256(request.query.encode("utf-8")).hexdigest(), top_k=request.top_k,
        knowledge_types=request.knowledge_types, hit_count=len(hits), hits=hits, elapsed_ms=1.0)


def _recording_retriever(requests: list[RetrievalRequest], hits: list[RetrievalHit]):
    """假检索器：把每次收到的请求记进 requests，返回固定的 Hit。"""
    def retriever(request):
        requests.append(request)
        return _result(request, hits)
    return retriever


def _recording_table_reader(reads: list[tuple[KnowledgeScope, list[str]]], table_chunks: dict[str, str]):
    """假表 Chunk 读取：记下收到的 (Scope, 表名列表)，返回其中每张表的表 Chunk。"""
    def reader(scope, tables):
        reads.append((scope, tables))
        return {table: table_chunks[table] for table in tables}
    return reader


def test_retrieval_source_gives_the_model_only_the_tables_its_hits_belong_to(tmp_path):
    """输入：context_source="retrieval"，其他参数用默认值；检索命中 orders 的 total 列和 orders → customers 的关系。

    输出：
    - 检索器收到一个请求：问题原文、shop 的 Scope、hybrid、开启重排、top_k=10。
    - 表 Chunk 读取收到 shop 的 Scope 和 ["orders"]：两个 Hit 都属于 orders，只读一次；customers 没被命中，不读。
    - 模型收到的 Schema 只有 orders 一张表（columns 写法），带命中的列和关系。
    """
    schemas, requests, reads = [], [], []
    graph = build_query_graph(_shop(tmp_path), context_source="retrieval",
                              generator=_recording_generator(schemas, "SELECT total FROM orders ORDER BY id"),
                              retriever=_recording_retriever(requests, ORDERS_HITS),
                              table_chunk_reader=_recording_table_reader(reads, {"orders": ORDERS_TABLE}))

    output = graph.invoke({"database_id": "shop", "question": QUESTION},
                          context=QueryContext(knowledge_scopes={"shop": SHOP_SCOPE}))

    assert requests == [RetrievalRequest(query=QUESTION, scope=SHOP_SCOPE, method="hybrid", rerank=True, top_k=10)]
    assert reads == [(SHOP_SCOPE, ["orders"])]
    assert schemas == ['''表 "orders"
  表用途: 记录客户下的订单及金额。
  记录粒度: 一笔订单。
  字段目录: "id", "customer_id", "total"
  主键: "id"
  外键: "customer_id" → "customers" ("id")
  列 "total" REAL: 订单金额，单位为元
  关系 "orders"."customer_id" → "customers"."id": 订单属于该客户。''']
    assert output["result"]["rows"] == [[10.0], [25.5]]


def test_retrieval_config_can_be_changed_when_the_graph_is_compiled(tmp_path):
    """输入：编译时 method="lexical"、rerank=False。输出：检索请求带着这两项，top_k 仍是 10。"""
    requests = []
    graph = build_query_graph(_shop(tmp_path), context_source="retrieval", method="lexical", rerank=False,
                              generator=_recording_generator([], "SELECT total FROM orders"),
                              retriever=_recording_retriever(requests, ORDERS_HITS),
                              table_chunk_reader=_recording_table_reader([], {"orders": ORDERS_TABLE}))

    graph.invoke({"database_id": "shop", "question": QUESTION},
                 context=QueryContext(knowledge_scopes={"shop": SHOP_SCOPE}))

    assert [(r.method, r.rerank, r.top_k) for r in requests] == [("lexical", False, 10)]


def _failing_retriever(error_type: str):
    """假检索器：总是抛出给定类别的 KnowledgeRetrievalError。"""
    def retriever(request):
        raise KnowledgeRetrievalError(error_type)
    return retriever


@pytest.mark.parametrize(("retriever", "scopes", "expected"), [
    (_recording_retriever([], []), {"shop": SHOP_SCOPE}, "no_match"),
    (_failing_retriever("input_error"), {"shop": SHOP_SCOPE}, "input_error"),
    (_failing_retriever("embedding_error"), {"shop": SHOP_SCOPE}, "embedding_error"),
    (_failing_retriever("rerank_error"), {"shop": SHOP_SCOPE}, "rerank_error"),
    (_recording_retriever([], ORDERS_HITS), {"other": SHOP_SCOPE}, KeyError),
], ids=["no_match", "input_error", "embedding_error", "rerank_error", "no_scope"])
def test_every_retrieval_failure_fails_the_request_before_the_model(tmp_path, retriever, scopes, expected):
    """输入：五种情况——没有命中、查询没有有效词、Embedding 失败、重排失败、对照表里没有当前库。

    输出：请求失败，模型没被调用，不退回其他来源。前四种是 KnowledgeRetrievalError，靠 error_type 区分；
    没有 Scope 是 KeyError。
    """
    schemas = []
    graph = build_query_graph(_shop(tmp_path), context_source="retrieval", retriever=retriever,
                              generator=_recording_generator(schemas, "SELECT total FROM orders"),
                              table_chunk_reader=_recording_table_reader([], {"orders": ORDERS_TABLE}))
    request = {"database_id": "shop", "question": QUESTION}
    context = QueryContext(knowledge_scopes=scopes)

    if expected is KeyError:
        with pytest.raises(KeyError):
            graph.invoke(request, context=context)
    else:
        with pytest.raises(KnowledgeRetrievalError) as caught:
            graph.invoke(request, context=context)
        assert caught.value.error_type == expected
    assert schemas == []


def test_sql_may_use_a_table_the_retrieval_did_not_hit(tmp_path):
    """输入：检索只命中 orders，模型写的 SQL 查 customers。

    输出：SQL 通过安全检查并执行。允许范围是这个库的全部用户表，不是检索命中的表：检索漏了的表模型
    仍可以用（比如照着外键行猜出来），不能因为检索没命中就拒绝。
    """
    graph = build_query_graph(_shop(tmp_path), context_source="retrieval",
                              generator=_recording_generator([], "SELECT name FROM customers"),
                              retriever=_recording_retriever([], ORDERS_HITS),
                              table_chunk_reader=_recording_table_reader([], {"orders": ORDERS_TABLE}))

    output = graph.invoke({"database_id": "shop", "question": "List customer names"},
                          context=QueryContext(knowledge_scopes={"shop": SHOP_SCOPE}))

    assert output["result"]["rows"] == [["Ada"]]


def test_the_default_readers_can_be_called_the_way_the_graph_calls_them():
    """图的默认检索器和知识读取函数只收到图给的参数（请求，或 Scope 和表名），存储配置要能省略。

    其他测试都换成了假的，这里守住默认值本身：签名对不上时真实运行在第一题就失败。
    """
    from inspect import signature

    from querypilot.retrieval.api import load_scope_chunks, load_table_chunks, retrieve_knowledge

    request = RetrievalRequest(query=QUESTION, scope=SHOP_SCOPE)
    signature(retrieve_knowledge).bind(request)
    signature(load_scope_chunks).bind(SHOP_SCOPE)
    signature(load_table_chunks).bind(SHOP_SCOPE, ["orders"])
