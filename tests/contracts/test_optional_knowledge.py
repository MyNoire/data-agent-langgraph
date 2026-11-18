"""在禁止psycopg导入的独立进程验证普通入口；不改用户.env。"""

import os
import subprocess
import sys


def test_help_http_sqlite_single_graph_and_fixture_need_no_knowledge_driver(tmp_path):
    """清空子进程知识变量并阻止驱动导入；普通能力成功，知识操作安全失败。"""
    program = r'''
import importlib.abc
import json
import sqlite3
import sys
from pathlib import Path

class BlockKnowledgeDriver(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        """禁止子进程加载可选驱动；不干预其他包。"""
        if fullname == "psycopg" or fullname.startswith("psycopg."):
            raise ImportError("optional driver intentionally unavailable")
        return None

sys.meta_path.insert(0, BlockKnowledgeDriver())
from querypilot.entrypoints.cli import main
try:
    main(["--help"])
except SystemExit as exc:
    assert exc.code == 0
from querypilot.runtime.api import Settings
from querypilot.databases.api import DatabaseRegistry, execute_sql
from querypilot.querying.api import ask
from querypilot.sql_generation.api import GeneratedSql
from querypilot.evaluation.api import run_direct
from querypilot.entrypoints.http import create_app
from fastapi.testclient import TestClient
root = Path.cwd()
data = root / "db"
data.mkdir()
path = data / "tiny.sqlite"
with sqlite3.connect(path) as connection:
    connection.execute("CREATE TABLE items(id INTEGER)")
    connection.execute("INSERT INTO items VALUES (1)")
DatabaseRegistry(data).register("tiny", path)
settings = Settings(_env_file=None, data_dir=data, artifacts_dir=root / "artifacts")
assert execute_sql("tiny", "SELECT id FROM items", settings).row_count == 1
assert ask("tiny", "count", settings, generator=lambda *_: GeneratedSql("SELECT id FROM items"))["result"]["row_count"] == 1
cases = root / "cases.json"
cases.write_text(json.dumps([{"db_id": "tiny", "question": "count", "SQL": "SELECT id FROM items"}]), encoding="utf-8")
assert run_direct(cases, settings, provider="fixture")["summary"]["success"] == 1
client = TestClient(create_app(settings))
assert client.get("/health").status_code == 200
assert client.post("/queries", json={"database_id": "tiny", "sql": "SELECT id FROM items"}).status_code == 200
assert "psycopg" not in sys.modules
assert main(["--artifacts-dir", str(root / "knowledge-artifacts"), "knowledge", "store", "check"]) == 1
assert "psycopg" not in sys.modules
print("OPTIONAL_ISOLATION_PASSED")
'''
    environment = {key: value for key, value in os.environ.items() if not key.startswith("QUERYPILOT_KNOWLEDGE_")}
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run([sys.executable, "-X", "utf8", "-c", program], cwd=tmp_path,
                               env=environment, capture_output=True, text=True, encoding="utf-8")
    assert completed.returncode == 0, completed.stderr
    assert "OPTIONAL_ISOLATION_PASSED" in completed.stdout
    assert '"error_type": "configuration_error"' in completed.stdout
