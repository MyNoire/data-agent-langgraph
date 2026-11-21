"""用假生成器、假检索器和假知识读取验证 Small-to-Big：命中的表以完整 Document 交给模型，选表和先后不变。"""

import pytest

from querypilot.querying.api import QueryContext, build_query_graph

from test_graph_context_source import CUSTOMERS_DDL, ORDERS_DDL, _recording_generator, _shop
from test_graph_knowledge_source import CUSTOMERS_TABLE, ORDERS_FK0, ORDERS_TOTAL, SHOP_CHUNKS, SHOP_SCOPE, _hit, _recording_reader
from test_graph_retrieval_source import _recording_retriever

QUESTION = "List customer names and order totals"

# 检索先命中 customers 的表 Chunk，再命中 orders 的 total 列；orders 的关系没被命中。
CUSTOMERS_THEN_ORDERS_TOTAL = [
    _hit(1, ["table", "shop", "main", "customers"], CUSTOMERS_TABLE, "customers"),
    _hit(2, ["column", "shop", "main", "orders", "total"], ORDERS_TOTAL, "orders", "total"),
]


def _invoke(tmp_path, hits, *, structure_format="columns"):
    """编译打开 Small-to-Big 的 retrieval 图并调用一次，返回 (模型收到的 Schema 列表, 知识读取收到的 Scope 列表)。"""
    schemas, scopes = [], []
    graph = build_query_graph(_shop(tmp_path), context_source="retrieval", small_to_big=True,
                              structure_format=structure_format,
                              generator=_recording_generator(schemas, "SELECT name FROM customers"),
                              retriever=_recording_retriever([], hits),
                              knowledge_reader=_recording_reader(scopes, SHOP_CHUNKS))
    graph.invoke({"database_id": "shop", "question": QUESTION},
                 context=QueryContext(knowledge_scopes={"shop": SHOP_SCOPE}))
    return schemas, scopes


def test_small_to_big_gives_each_hit_table_its_whole_document_in_retrieval_order(tmp_path):
    """输入：small_to_big=True；检索命中 customers 的表 Chunk（第 1）和 orders 的 total 列（第 2）。

    输出：
    - 知识读取收到 shop 的 Scope，读一次。
    - 模型收到的 Schema 里两张表都完整：orders 除了命中的 total 列，还带上没被命中的关系，
      与 knowledge 来源里 orders 的渲染逐字相同。
    - 表的先后按检索名次：customers 在前。knowledge 来源按全量读取的顺序，orders 在前。
    """
    schemas, scopes = _invoke(tmp_path, CUSTOMERS_THEN_ORDERS_TOTAL)

    assert scopes == [SHOP_SCOPE]
    assert schemas == ['''表 "customers"
  表用途: 记录客户及其姓名。
  记录粒度: 一位客户。
  字段目录: "id", "name"
  主键: "id"
  外键: 未声明

表 "orders"
  表用途: 记录客户下的订单及金额。
  记录粒度: 一笔订单。
  字段目录: "id", "customer_id", "total"
  主键: "id"
  外键: "customer_id" → "customers" ("id")
  列 "total" REAL: 订单金额，单位为元
  关系 "orders"."customer_id" → "customers"."id": 订单属于该客户。''']


@pytest.mark.parametrize("context_source", ["schema", "ddl", "knowledge"])
def test_small_to_big_on_a_source_without_retrieval_is_rejected_when_the_graph_is_compiled(tmp_path, context_source):
    """输入：在 schema、ddl、knowledge 来源上打开 small_to_big。

    输出：编译时报 ValueError。否则会有标着 small-to-big、实际却是裸表名或全量知识的评测运行。
    """
    with pytest.raises(ValueError, match="small_to_big"):
        build_query_graph(_shop(tmp_path), context_source=context_source, small_to_big=True)


def test_a_table_hit_only_through_its_relationship_is_expanded_too(tmp_path):
    """输入：检索只命中 orders → customers 的关系 Hit。

    输出：关系归到源表 orders，orders 以完整 Document 出现（表 Chunk、total 列、关系）；customers 只是
    关系的目标表，没有自己的 Hit，不出现。
    """
    schemas, _ = _invoke(tmp_path, [_hit(1, ["relationship", "shop", "main", "orders", 0], ORDERS_FK0)])

    assert schemas == ['''表 "orders"
  表用途: 记录客户下的订单及金额。
  记录粒度: 一笔订单。
  字段目录: "id", "customer_id", "total"
  主键: "id"
  外键: "customer_id" → "customers" ("id")
  列 "total" REAL: 订单金额，单位为元
  关系 "orders"."customer_id" → "customers"."id": 订单属于该客户。''']


def test_small_to_big_in_ddl_format_uses_each_expanded_table_create_statement(tmp_path):
    """输入：structure_format="ddl"，检索同第一个测试。

    输出：每张展开的表用建表语句代替字段目录、主键、外键三行，其余内容和顺序与 columns 写法相同。
    """
    schemas, _ = _invoke(tmp_path, CUSTOMERS_THEN_ORDERS_TOTAL, structure_format="ddl")

    assert schemas == [f'''表 "customers"
  表用途: 记录客户及其姓名。
  记录粒度: 一位客户。
{CUSTOMERS_DDL}

表 "orders"
  表用途: 记录客户下的订单及金额。
  记录粒度: 一笔订单。
{ORDERS_DDL}
  列 "total" REAL: 订单金额，单位为元
  关系 "orders"."customer_id" → "customers"."id": 订单属于该客户。''']
