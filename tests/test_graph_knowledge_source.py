"""用假 SQL 生成器和假知识读取验证 knowledge 来源：图按运行时 context 里的 Scope 读出全部知识，渲染后交给模型。"""

import json

import pytest
from uuid import NAMESPACE_URL, uuid5

from querypilot.querying.api import QueryContext, build_query_graph
from querypilot.retrieval.api import KnowledgeScope, RetrievalHit

from test_graph_context_source import CUSTOMERS_DDL, ORDERS_DDL, _recording_generator, _shop

SHOP_SCOPE = KnowledgeScope(corpus_id="a" * 64, database_id="shop", schema_version="b" * 64)

CUSTOMERS_TABLE = '''数据库: shop; 表: "main"."customers"
表用途: 记录客户及其姓名。
记录粒度: 一位客户。
字段目录: "id", "name"
行标识线索（主键声明、成员顺序）: "id"
出向关联（声明外键）: 未声明'''

ORDERS_TABLE = '''数据库: shop; 表: "main"."orders"
表用途: 记录客户下的订单及金额。
记录粒度: 一笔订单。
字段目录: "id", "customer_id", "total"
行标识线索（主键声明、成员顺序）: "id"
出向关联（声明外键）: "customer_id" → "main"."customers" ("id")'''

ORDERS_TOTAL = '''数据库: shop; 字段: "main"."orders"."total"
声明类型: REAL
not_null 声明标志: False; 主键位置: 0; hidden: 0
默认 SQL 表达式（仅文本）: 未声明
展示名: 来源未说明
字段解释: 订单金额，单位为元
取值解释: 来源未说明'''

ORDERS_FK0 = '''数据库: shop; 声明外键 "main"."orders" / 0
引用方向: 源表 "main"."orders" → 目标表 "main"."customers"。
同一条外键的有序字段对: "main"."orders"."customer_id" → "main"."customers"."id"
关联语义: 订单属于该客户。
候选 JOIN（source 为源表别名，target 为目标表别名；仅展示 ON 条件）: "source"."customer_id" = "target"."id"
ON UPDATE NO ACTION; ON DELETE NO ACTION; MATCH NONE'''


def _hit(rank: int, key: list, content: str, table: str | None = None, column: str | None = None) -> RetrievalHit:
    """知识读取返回的一个 Chunk：rank 是读取顺序，score 为 0。"""
    chunk_key = json.dumps(key)
    return RetrievalHit(rank=rank, score=0.0, chunk_id=str(uuid5(NAMESPACE_URL, chunk_key)), chunk_key=chunk_key,
                        knowledge_type=key[0], title="t", content=content, content_hash="c" * 64,
                        schema_name=None if table is None else "main", table_name=table, column_name=column)


# shop 的全部知识，按 chunk_key 排序（与真实知识读取的顺序规则相同）。
SHOP_CHUNKS = [
    _hit(1, ["column", "shop", "main", "orders", "total"], ORDERS_TOTAL, "orders", "total"),
    _hit(2, ["relationship", "shop", "main", "orders", 0], ORDERS_FK0),
    _hit(3, ["table", "shop", "main", "customers"], CUSTOMERS_TABLE, "customers"),
    _hit(4, ["table", "shop", "main", "orders"], ORDERS_TABLE, "orders"),
]


def _recording_reader(scopes: list[KnowledgeScope], chunks: list[RetrievalHit]):
    """假知识读取：把每次收到的 Scope 记进 scopes，返回固定的 Chunk。"""
    def reader(scope):
        scopes.append(scope)
        return chunks
    return reader


def test_knowledge_source_gives_the_model_all_knowledge_of_the_database_rendered_as_columns(tmp_path):
    """输入：context_source="knowledge"、structure_format="columns"；调用时运行时 context 给出 shop → SHOP_SCOPE。

    输出：
    - 知识读取收到的是 shop 的 Scope，只读一次。
    - 模型收到的 Schema 是 shop 全部知识经组装和渲染（columns 写法）的结果：每张表都在，表按 Hit 的最小
      rank 排列（orders 的列排第 1，customers 的表 Chunk 排第 3），关系归到源表 orders。
    - 图照常校验并执行模型写的 SQL。
    """
    schemas, scopes = [], []
    graph = build_query_graph(_shop(tmp_path), context_source="knowledge", structure_format="columns",
                              generator=_recording_generator(schemas, "SELECT name FROM customers"),
                              knowledge_reader=_recording_reader(scopes, SHOP_CHUNKS))

    output = graph.invoke({"database_id": "shop", "question": "List customer names"},
                          context=QueryContext(knowledge_scopes={"shop": SHOP_SCOPE}))

    assert scopes == [SHOP_SCOPE]
    assert schemas == ['''表 "orders"
  表用途: 记录客户下的订单及金额。
  记录粒度: 一笔订单。
  字段目录: "id", "customer_id", "total"
  主键: "id"
  外键: "customer_id" → "customers" ("id")
  列 "total" REAL: 订单金额，单位为元
  关系 "orders"."customer_id" → "customers"."id": 订单属于该客户。

表 "customers"
  表用途: 记录客户及其姓名。
  记录粒度: 一位客户。
  字段目录: "id", "name"
  主键: "id"
  外键: 未声明''']
    assert output["result"]["rows"] == [["Ada"]]


def test_knowledge_source_in_ddl_format_puts_each_table_create_statement_in_place_of_the_structure_lines(tmp_path):
    """输入：同样的 shop 知识，structure_format="ddl"。

    输出：模型收到的 Schema 里，每张表的字段目录、主键、外键三行换成这张表在 SQLite 里的建表语句原文
    （由 load_schema 从库里现读）；表用途、记录粒度、列和关系与 columns 写法相同。
    """
    schemas = []
    graph = build_query_graph(_shop(tmp_path), context_source="knowledge", structure_format="ddl",
                              generator=_recording_generator(schemas, "SELECT name FROM customers"),
                              knowledge_reader=_recording_reader([], SHOP_CHUNKS))

    graph.invoke({"database_id": "shop", "question": "List customer names"},
                 context=QueryContext(knowledge_scopes={"shop": SHOP_SCOPE}))

    assert schemas == [f'''表 "orders"
  表用途: 记录客户下的订单及金额。
  记录粒度: 一笔订单。
{ORDERS_DDL}
  列 "total" REAL: 订单金额，单位为元
  关系 "orders"."customer_id" → "customers"."id": 订单属于该客户。

表 "customers"
  表用途: 记录客户及其姓名。
  记录粒度: 一位客户。
{CUSTOMERS_DDL}''']


def test_request_fails_before_the_model_when_the_database_has_no_knowledge_scope(tmp_path):
    """输入：运行时 context 的对照表里只有别的库，没有 shop。

    输出：请求失败（KeyError），知识读取和模型都没有被调用。不退回到不带知识的 Schema，否则评测里这一题
    其实是在别的条件下跑的。
    """
    schemas, scopes = [], []
    graph = build_query_graph(_shop(tmp_path), context_source="knowledge",
                              generator=_recording_generator(schemas, "SELECT name FROM customers"),
                              knowledge_reader=_recording_reader(scopes, SHOP_CHUNKS))

    with pytest.raises(KeyError):
        graph.invoke({"database_id": "shop", "question": "List customer names"},
                     context=QueryContext(knowledge_scopes={"other": SHOP_SCOPE}))

    assert scopes == []
    assert schemas == []


def test_knowledge_scope_never_enters_graph_state_or_output(tmp_path):
    """输入：正常的 knowledge 调用，用 stream_mode="values" 取出每一步之后的完整状态。

    输出：任何一步的状态里都没有 Scope 的 corpus_id 和 schema_version；最终输出只有 sql 和 result。
    Scope 只经运行时 context 交给 load_knowledge，不随状态进入 stream、输出或以后的 checkpoint。
    """
    graph = build_query_graph(_shop(tmp_path), context_source="knowledge",
                              generator=_recording_generator([], "SELECT name FROM customers"),
                              knowledge_reader=_recording_reader([], SHOP_CHUNKS))
    request = {"database_id": "shop", "question": "List customer names"}
    context = QueryContext(knowledge_scopes={"shop": SHOP_SCOPE})

    states = list(graph.stream(request, context=context, stream_mode="values"))
    output = graph.invoke(request, context=context)

    assert len(states) == 7   # 输入，加上 load_schema 到 execute_sql 六个节点之后各一次
    for state in [*states, output]:
        assert SHOP_SCOPE.corpus_id not in repr(state)
        assert SHOP_SCOPE.schema_version not in repr(state)
    assert set(output) == {"sql", "result"}
