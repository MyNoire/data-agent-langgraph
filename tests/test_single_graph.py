"""用真实 SQLite 文件和替身模型检查图的节点交接。"""

import sqlite3

import pytest

from querypilot.config import Settings
from querypilot.database.registry import DatabaseRegistry, RegistryError
from querypilot.graph.single import build_single_query_graph
from querypilot.sql.guard import SqlRejected


def _registered_database(tmp_path):
    """创建并登记测试数据库。

    参数:
        tmp_path: pytest 提供的隔离临时目录。
    返回:
        二元组：数据目录路径和 SQLite 文件路径。
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    path = data_dir / "sample.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE items (id INTEGER, name TEXT)")
        connection.execute("INSERT INTO items VALUES (1, 'one'), (2, 'two')")
    DatabaseRegistry(data_dir).register("sample", path)
    return data_dir, path


def test_linear_graph_generates_validates_and_executes(tmp_path):
    """确认图传递 Schema、限制行数并返回执行结果。

    参数:
        tmp_path: pytest 提供的隔离临时目录。
    返回:
        None；通过断言报告测试结果。
    """
    data_dir, _ = _registered_database(tmp_path)
    calls = []

    def fake_generator(question, schema, evidence, settings):
        """记录模型输入并返回固定的只读查询。

        参数:
            question: 用户问题。
            schema: 从 SQLite 读取的 Schema 文本。
            evidence: 额外证据；此链路预期为空字符串。
            settings: 当前运行配置。
        返回:
            固定的 SELECT 语句。
        """
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
    """确认写入 SQL 被校验节点拒绝且数据库保持原样。

    参数:
        tmp_path: pytest 提供的隔离临时目录。
    返回:
        None；通过断言报告测试结果。
    """
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
    """确认未知数据库在模型调用前被拒绝。

    参数:
        tmp_path: pytest 提供的隔离临时目录。
    返回:
        None；通过断言报告测试结果。
    """
    data_dir, _ = _registered_database(tmp_path)

    def unexpected_generator(*_):
        """在意外调用模型时使测试失败。

        参数:
            *_: 意外传入的模型参数；本测试不解析其值。
        返回:
            不返回，始终抛出 AssertionError。
        """
        raise AssertionError("model should not be called")

    graph = build_single_query_graph(Settings(data_dir=data_dir), generator=unexpected_generator)
    with pytest.raises(RegistryError):
        graph.invoke({"database_id": "missing", "question": "How many items?"})
