"""从 HTTP 层验证健康检查、数据库注册、Schema 与查询错误映射。"""

import sqlite3

from fastapi.testclient import TestClient

from querypilot.api import create_app
from querypilot.config import Settings


def test_health_register_schema_and_query(tmp_path):
    """覆盖健康检查、数据库登记、Schema 查询和 SQL 错误映射。

    参数:
        tmp_path: pytest 提供的隔离临时目录。
    返回:
        None；通过断言报告测试结果。
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    path = data_dir / "sample.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE items (id INTEGER, name TEXT)")
        connection.execute("INSERT INTO items VALUES (1, 'a'), (2, 'b')")
    client = TestClient(create_app(Settings(data_dir=data_dir, max_rows=1)))
    assert client.get("/health").json()["status"] == "ok"
    assert client.post("/databases", json={"database_id": "sample", "path": str(path)}).status_code == 201
    assert client.get("/databases/sample/schema").json()["tables"] == {"items": ["id", "name"]}
    result = client.post("/queries", json={"database_id": "sample", "sql": "SELECT * FROM items"})
    assert result.status_code == 200
    assert result.json()["row_count"] == 1
    assert "LIMIT 1" in result.json()["sql"]
    assert client.post("/queries", json={"database_id": "sample", "sql": "DELETE FROM items"}).status_code == 400
    assert client.post("/databases", json={"database_id": "escape", "path": str(tmp_path / "outside.sqlite")}).status_code == 400
