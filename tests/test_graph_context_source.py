"""用假 SQL 生成器验证查询图的 Context Source：模型在每种来源下收到的 Schema 是什么。"""

import sqlite3

from querypilot.databases.api import DatabaseRegistry
from querypilot.querying.api import build_query_graph
from querypilot.runtime.api import Settings
from querypilot.sql_generation.api import GeneratedSql

CUSTOMERS_DDL = "CREATE TABLE customers (\n    id INTEGER PRIMARY KEY,\n    name TEXT NOT NULL\n)"
ORDERS_DDL = "CREATE TABLE orders (\n    id INTEGER PRIMARY KEY,\n    customer_id INTEGER REFERENCES customers(id),\n    total REAL\n)"


def _shop(tmp_path) -> Settings:
    """建一个登记为 shop 的库：customers（1 行）和 orders（2 行）两张表，返回指向它的运行配置。

    建表顺序故意是 orders 在前，这样"按表名排序"和"按建表顺序"的结果不同。
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    path = data_dir / "shop.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(ORDERS_DDL)
        connection.execute(CUSTOMERS_DDL)
        connection.execute("INSERT INTO customers VALUES (1, 'Ada')")
        connection.executemany("INSERT INTO orders VALUES (?, 1, ?)", [(1, 10.0), (2, 25.5)])
    DatabaseRegistry(data_dir).register("shop", path)
    return Settings(_env_file=None, data_dir=data_dir)


def _recording_generator(schemas: list[str], sql: str):
    """假 SQL 生成器：把每次收到的 Schema 记进 schemas，总是返回同一条 SQL。"""
    def generator(question, schema, evidence, settings):
        schemas.append(schema)
        return GeneratedSql(sql)
    return generator


def test_ddl_source_gives_the_model_every_create_table_statement(tmp_path):
    """输入：context_source="ddl"，库里有 customers 和 orders 两张表。

    输出：模型收到的 Schema 是两张表的建表语句原文，按表名顺序，中间空一行，没有别的内容；
    图照常返回执行结果。
    """
    schemas = []
    graph = build_query_graph(_shop(tmp_path), context_source="ddl",
                              generator=_recording_generator(schemas, "SELECT name FROM customers"))

    output = graph.invoke({"database_id": "shop", "question": "List customer names"})

    assert schemas == [CUSTOMERS_DDL + "\n\n" + ORDERS_DDL]
    assert output["result"]["rows"] == [["Ada"]]
