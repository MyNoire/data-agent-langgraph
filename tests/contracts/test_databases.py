"""经公开入口验收完整定位、结构与安全执行。"""

import sqlite3

import pytest

from querypilot.databases.api import DatabaseRegistry, RegistryError, SqlRejected, execute_sql, get_schema
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
