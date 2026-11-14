"""经公开入口验收完整定位、结构与安全执行。"""

import sqlite3

import pytest

from querypilot.databases.api import (
    DatabaseRegistry, RegistryError, SqlRejected, execute_sql, get_schema, get_table_ddl,
)
from querypilot.runtime.api import Settings


def test_complete_database_operation(tmp_path):
    """注册临时源后只传 ID 即可读结构和执行；写 SQL 仍拒绝。"""
    path = tmp_path / "demo.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE items(id INTEGER)")
        conn.executemany("INSERT INTO items VALUES (?)", [(1,), (2,)])
    DatabaseRegistry(tmp_path).register("demo", path)
    settings = Settings(_env_file=None, data_dir=tmp_path, max_rows=1)
    assert get_schema("demo", settings) == {"items": ["id"]}
    assert execute_sql("demo", "SELECT id FROM items ORDER BY id", settings).rows == [[1]]
    with pytest.raises(SqlRejected):
        execute_sql("demo", "DELETE FROM items", settings)
    with pytest.raises(RegistryError):
        get_schema("missing", settings)


def test_table_ddl_returns_each_user_table_statement_in_name_order(tmp_path):
    """输入：登记的库里有 orders、customers 两张表，其中一张用了 AUTOINCREMENT（SQLite 因此自建内部表
    sqlite_sequence），另有一个索引。

    输出：表名到建表语句原文的映射，按表名排序；没有内部表，也没有索引的语句。
    """
    orders = "CREATE TABLE orders (\n    id INTEGER PRIMARY KEY AUTOINCREMENT,\n    customer_id INTEGER REFERENCES customers(id)\n)"
    customers = 'CREATE TABLE "customers" (id INTEGER PRIMARY KEY, name TEXT NOT NULL)'
    path = tmp_path / "shop.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(orders)
        connection.execute(customers)
        connection.execute("CREATE INDEX orders_customer ON orders(customer_id)")
        connection.execute("INSERT INTO orders (customer_id) VALUES (1)")
    DatabaseRegistry(tmp_path).register("shop", path)

    ddl = get_table_ddl("shop", Settings(_env_file=None, data_dir=tmp_path))

    assert list(ddl) == ["customers", "orders"]
    assert ddl == {"customers": customers, "orders": orders}
