"""Exercise graph handoffs against a real SQLite file without a model call."""

import sqlite3

import pytest

from querypilot.config import Settings
from querypilot.database.registry import DatabaseRegistry, RegistryError
from querypilot.graph.single import build_single_query_graph
from querypilot.sql.guard import SqlRejected


def _registered_database(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    path = data_dir / "sample.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE items (id INTEGER, name TEXT)")
        connection.execute("INSERT INTO items VALUES (1, 'one'), (2, 'two')")
    DatabaseRegistry(data_dir).register("sample", path)
    return data_dir, path


def test_linear_graph_generates_validates_and_executes(tmp_path):
    data_dir, _ = _registered_database(tmp_path)
    calls = []

    def fake_generator(question, schema, evidence, settings):
        calls.append((question, schema, evidence, settings.max_rows))
        return "SELECT name FROM items ORDER BY id"

    graph = build_single_query_graph(Settings(data_dir=data_dir, max_rows=1), generator=fake_generator)
    output = graph.invoke({"database_id": "sample", "question": "List item names"})

    assert calls == [("List item names", "items(id, name)", "", 1)]
    assert output["sql"].endswith("LIMIT 1")
    assert output["result"]["rows"] == [["one"]]
    assert output["result"]["row_count"] == 1
    assert set(output) == {"sql", "result"}


def test_guard_rejects_model_write_before_execution(tmp_path):
    data_dir, path = _registered_database(tmp_path)
    graph = build_single_query_graph(
        Settings(data_dir=data_dir),
        generator=lambda *_: "DELETE FROM items",
    )

    with pytest.raises(SqlRejected):
        graph.invoke({"database_id": "sample", "question": "Delete everything"})
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2


def test_unknown_database_stops_before_model_call(tmp_path):
    data_dir, _ = _registered_database(tmp_path)

    def unexpected_generator(*_):
        raise AssertionError("model should not be called")

    graph = build_single_query_graph(Settings(data_dir=data_dir), generator=unexpected_generator)
    with pytest.raises(RegistryError):
        graph.invoke({"database_id": "missing", "question": "How many items?"})
