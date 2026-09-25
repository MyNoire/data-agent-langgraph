"""验证 SQL 策略、只读行为、结果大小与递归查询超时。"""

import sqlite3

import pytest

from querypilot.database.executor import QueryLimitError, execute_readonly
from querypilot.sql.guard import SqlRejected, guard_sql


@pytest.fixture
def sample(tmp_path):
    path = tmp_path / "sample.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
        db.executemany("INSERT INTO items (name) VALUES (?)", [("a",), ("b",)])
    return path


@pytest.mark.parametrize("sql", [
    "DELETE FROM items", "DROP TABLE items", "SELECT * FROM items; DELETE FROM items",
    "PRAGMA database_list", "ATTACH DATABASE 'x' AS evil", "SELECT * FROM secrets",
    "SELECT load_extension('x')", "SELECT * FROM items LIMIT -1",
    "WITH RECURSIVE seq(n) AS (SELECT 1) SELECT * FROM seq",
])
def test_guard_rejects(sql):
    with pytest.raises(SqlRejected):
        guard_sql(sql, {"items"}, 10)


def test_cte_limit_and_readonly(sample):
    result = execute_readonly(sample, "WITH x AS (SELECT * FROM items) SELECT name FROM x ORDER BY name", database_id="sample", max_rows=1)
    assert result.rows == [["a"]]
    assert "LIMIT 1" in result.sql
    with pytest.raises(SqlRejected):
        execute_readonly(sample, "UPDATE items SET name='x'", database_id="sample")
    with sqlite3.connect(sample) as db:
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2
    assert "LIMIT 1" in guard_sql("SELECT * FROM items LIMIT 9999", {"items"}, 1)


def test_response_byte_limit(sample):
    with pytest.raises(QueryLimitError):
        execute_readonly(sample, "SELECT name FROM items", database_id="sample", max_response_bytes=10)


def test_recursive_query_timeout(sample):
    query = "WITH RECURSIVE seq AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM seq WHERE n < 100000000) SELECT SUM(n) FROM seq"
    with pytest.raises(QueryLimitError, match="timed out"):
        execute_readonly(sample, query, database_id="sample", timeout_ms=1)
