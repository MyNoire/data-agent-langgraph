"""一次完整自然语言查询的公开行为；生成器固定，无模型调用。"""

import sqlite3

import pytest

from querypilot.databases.api import DatabaseRegistry, RegistryError
from querypilot.querying.api import ask
from querypilot.runtime.api import Settings


def test_ask_completes_operation_and_unknown_source_stops_generation(tmp_path):
    """只用公开入口完成原单图；未知库不调用生成器。"""
    path = tmp_path / "demo.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE items(id INTEGER)")
        conn.execute("INSERT INTO items VALUES (7)")
    DatabaseRegistry(tmp_path).register("demo", path)
    calls = []

    def generator(question, schema, evidence, settings):
        """记录原四参输入，返回固定 SQL。"""
        calls.append((question, schema, evidence))
        return "SELECT id FROM items"

    settings = Settings(_env_file=None, data_dir=tmp_path)
    assert ask("demo", "list", settings, generator=generator)["result"]["rows"] == [[7]]
    assert calls == [("list", "items(id)", "")]
    with pytest.raises(RegistryError):
        ask("missing", "list", settings, generator=generator)
    assert len(calls) == 1
