"""合法SQLite BLOB保持原公开结果，归档额外编码不能令成功查询失败。"""

from base64 import b64decode
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from querypilot.databases.api import DatabaseRegistry, execute_sql
from querypilot.entrypoints.http import create_app
from querypilot.querying.api import ask
from querypilot.runtime.api import Settings, read_run_result
from querypilot.sql_generation.api import GeneratedSql


def binary_settings(tmp_path, payloads, *, api_key="local"):
    """建立原创BLOB样本；返回隔离配置，不读正式数据或调用模型。"""
    path = tmp_path / "binary.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE blobs(id INTEGER, payload BLOB)")
        connection.executemany("INSERT INTO blobs VALUES (?, ?)", enumerate(payloads, start=1))
    DatabaseRegistry(tmp_path).register("binary", path)
    return Settings(_env_file=None, data_dir=tmp_path, artifacts_dir=tmp_path / "artifacts", model_api_key=api_key)


def archived_bytes(value):
    """从局部编码标签恢复归档字节，验证表示明确且使用合法base64。"""
    assert set(value) == {"$querypilot_binary"}
    encoded = value["$querypilot_binary"]
    assert encoded["encoding"] == "base64" and set(encoded) == {"encoding", "data"}
    return b64decode(encoded["data"], validate=True)


@pytest.mark.parametrize("operation", ["execute_sql", "ask"])
def test_public_library_queries_preserve_arbitrary_blob_and_archive_reversibly(tmp_path, operation):
    """两种公开查询保持原始bytes，包括非UTF8和空值；归档仍完整可读。"""
    payloads = [b"A", b"\x00\xff\x80BLOB", b""]
    settings = binary_settings(tmp_path, payloads)
    sql = "SELECT payload FROM blobs ORDER BY id"
    if operation == "execute_sql":
        rows = execute_sql("binary", sql, settings).rows
    else:
        rows = ask("binary", "返回二进制字段", settings, generator=lambda *_: GeneratedSql(sql))["result"]["rows"]
    assert rows == [[payload] for payload in payloads]
    assert all(type(row[0]) is bytes for row in rows)
    runs = list(settings.artifacts_dir.glob("runs/query/*/*"))
    assert len(runs) == 1
    archived = read_run_result(runs[0])
    archived_result = archived["result"] if operation == "ask" else archived
    assert [archived_bytes(row[0]) for row in archived_result["rows"]] == payloads
    assert archived_result["columns"] == ["payload"] and archived_result["row_count"] == 3
    assert json.loads((runs[0] / "run.json").read_text(encoding="utf-8"))["status"] == "completed"


def test_http_utf8_blob_keeps_original_success_serialization(tmp_path):
    """HTTP原本把UTF8 BLOB序列化为文本，新增归档不能把它改成500或标签。"""
    settings = binary_settings(tmp_path, [b"A"])
    response = TestClient(create_app(settings)).post("/queries", json={
        "database_id": "binary", "sql": "SELECT payload FROM blobs",
    })
    assert response.status_code == 200
    result = response.json()
    assert set(result) == {"sql", "columns", "rows", "row_count", "elapsed_ms"}
    assert result["rows"] == [["A"]] and result["row_count"] == 1
    run = next(settings.artifacts_dir.glob("runs/query/*/*"))
    assert archived_bytes(read_run_result(run)["rows"][0][0]) == b"A"


def test_binary_archive_removes_readable_secrets_before_encoding(tmp_path):
    """原库调用保留bytes；归档须先去凭据，不能将秘密直接隐藏到base64。"""
    key = "BLOB_API_KEY_SENTINEL"
    payload = (b"\xff binary-prefix " + key.encode() +
               b" postgresql://actor:BLOB_PASSWORD_SENTINEL@database/private "
               b"https://actor:WEB_PASSWORD_SENTINEL@example.test/v1?token=TOKEN_SENTINEL tail\x80")
    settings = binary_settings(tmp_path, [payload], api_key=key)
    assert execute_sql("binary", "SELECT payload FROM blobs", settings).rows == [[payload]]
    run = next(settings.artifacts_dir.glob("runs/query/*/*"))
    archived = archived_bytes(read_run_result(run)["rows"][0][0])
    assert archived.startswith(b"\xff binary-prefix ") and archived.endswith(b" tail\x80")
    assert b"[REDACTED]" in archived
    for secret in (key, "BLOB_PASSWORD_SENTINEL", "WEB_PASSWORD_SENTINEL", "TOKEN_SENTINEL"):
        assert secret.encode() not in archived
        assert secret not in "".join(path.read_text(encoding="utf-8") for path in run.rglob("*.json"))
