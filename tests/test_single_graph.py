"""用真实 SQLite 文件和替身模型检查图的节点交接。"""

import json
import sqlite3
import sys
from io import StringIO

import pytest

from querypilot.config import Settings
from querypilot.database.registry import DatabaseRegistry, RegistryError
from querypilot.graph.single import build_single_query_graph
from querypilot.logging import progress_logging
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


def test_graph_progress_events_cover_nodes_without_query_content(tmp_path):
    """确认四个节点有同一运行 ID 的开始与完成日志。

    参数:
        tmp_path: pytest 提供的隔离临时目录。
    返回:
        None；校验事件顺序、节点耗时字段与敏感内容排除规则。
    """
    data_dir, _ = _registered_database(tmp_path)
    graph = build_single_query_graph(
        Settings(data_dir=data_dir),
        generator=lambda *_: "SELECT name FROM items",
    )
    events_path = tmp_path / "graph.events.jsonl"
    with progress_logging(events_path, stream=StringIO()):
        graph.invoke({"database_id": "sample", "question": "PRIVATE QUESTION",
                      "run_id": "test-graph-run"})
    raw = events_path.read_text(encoding="utf-8")
    events = [json.loads(line) for line in raw.splitlines()]
    assert [event["event"] for event in events] == [
        item for node in ("load_schema", "generate_sql", "validate_sql", "execute_sql")
        for item in ("graph_node_started", "graph_node_finished")
    ]
    assert [event["node"] for event in events[::2]] == [
        "load_schema", "generate_sql", "validate_sql", "execute_sql",
    ]
    assert all(event["run_id"] == "test-graph-run" for event in events)
    assert events[-1]["row_count"] == 2
    assert "PRIVATE QUESTION" not in raw
    assert "SELECT name FROM items" not in raw


def test_graph_failure_logs_node_and_error_type(tmp_path):
    """确认校验失败会记录失败节点，并阻止执行节点启动。

    参数:
        tmp_path: pytest 提供的隔离临时目录。
    返回:
        None；失败事件包含 SqlRejected 类型而不包含原始 SQL。
    """
    data_dir, _ = _registered_database(tmp_path)
    graph = build_single_query_graph(
        Settings(data_dir=data_dir),
        generator=lambda *_: "DELETE FROM items",
    )
    events_path = tmp_path / "rejected.events.jsonl"
    with progress_logging(events_path, stream=StringIO()):
        with pytest.raises(SqlRejected):
            graph.invoke({"database_id": "sample", "question": "PRIVATE QUESTION"})
    raw = events_path.read_text(encoding="utf-8")
    events = [json.loads(line) for line in raw.splitlines()]
    assert events[-1]["node"] == "validate_sql"
    assert events[-1]["status"] == "error"
    assert events[-1]["error_type"] == "SqlRejected"
    assert not any(event["node"] == "execute_sql" for event in events)
    assert "DELETE FROM items" not in raw


def test_cli_stream_preserves_answer_and_progress_log(tmp_path, monkeypatch, capsys):
    """确认正式 CLI 消费流后仍返回答案并保留进度日志。

    参数:
        tmp_path: pytest 提供的隔离目录。
        monkeypatch: pytest 提供的临时依赖替换器。
        capsys: pytest 提供的标准输出与错误输出捕获器。
    返回:
        None；通过断言检查输出、日志和流调用方式。
    """
    from scripts import ask_graph

    data_dir, _ = _registered_database(tmp_path)

    def fake_generator(question: str, schema: str, evidence: str, settings: Settings) -> str:
        """返回固定 SQL，避免 CLI 测试调用真实模型。

        参数:
            question: 用户问题，本测试不使用。
            schema: 表结构，本测试不使用。
            evidence: 补充证据，本测试不使用。
            settings: 当前配置，本测试不使用。
        返回:
            按 ID 排序的只读查询。
        """
        return "SELECT name FROM items ORDER BY id"

    real_graph = build_single_query_graph(
        Settings(data_dir=data_dir),
        generator=fake_generator,
    )

    class StreamOnlyGraph:
        """只暴露 stream，确保 CLI 不会回退到 invoke。"""

        def stream(self, *args, **kwargs):
            """转发图的流调用。

            参数:
                *args: CLI 传入的位置参数。
                **kwargs: CLI 传入的流模式和版本。
            返回:
                真实测试图的流迭代器。
            """
            return real_graph.stream(*args, **kwargs)

    def fake_get_settings() -> Settings:
        """返回测试数据库配置。

        参数:
            无。
        返回:
            指向隔离数据目录的 Settings。
        """
        return Settings(data_dir=data_dir)

    def fake_build_graph(settings: Settings) -> StreamOnlyGraph:
        """向 CLI 注入仅支持流调用的测试图。

        参数:
            settings: CLI 传入的配置，本测试使用预先构建的图。
        返回:
            不提供 invoke() 的图替身。
        """
        return StreamOnlyGraph()

    monkeypatch.setattr(ask_graph, "get_settings", fake_get_settings)
    monkeypatch.setattr(ask_graph, "build_single_query_graph", fake_build_graph)
    monkeypatch.setattr(sys, "argv", [
        "ask_graph.py", "--database-id", "sample", "--question", "List item names",
    ])
    monkeypatch.chdir(tmp_path)

    ask_graph.main()
    captured = capsys.readouterr()
    answer = json.loads(captured.out)
    assert set(answer) == {"sql", "result"}
    assert answer["result"]["rows"] == [["one"], ["two"]]

    files = list((tmp_path / "results").glob("graph__*.events.jsonl"))
    assert len(files) == 1
    raw_events = files[0].read_text(encoding="utf-8")
    events = [json.loads(line) for line in raw_events.splitlines()]
    assert events[0]["event"] == "graph_started"
    assert events[-1]["event"] == "graph_finished"
    assert events[-1]["status"] == "success"
    assert sum(item["event"] == "graph_node_finished" for item in events) == 4
    assert "SELECT name FROM items" not in raw_events


def test_cli_stream_failure_records_node_and_graph_error(tmp_path, monkeypatch, capsys):
    """确认流式 CLI 在校验失败时保留节点及图级失败事件。

    参数:
        tmp_path: pytest 提供的隔离目录。
        monkeypatch: pytest 提供的临时依赖替换器。
        capsys: pytest 提供的终端输出捕获器。
    返回:
        None；断言异常继续传播、标准输出没有答案且事件不含 SQL。
    """
    from scripts import ask_graph

    data_dir, _ = _registered_database(tmp_path)
    graph = build_single_query_graph(
        Settings(data_dir=data_dir), generator=lambda *_: "DELETE FROM items",
    )
    monkeypatch.setattr(ask_graph, "get_settings", lambda: Settings(data_dir=data_dir))
    monkeypatch.setattr(ask_graph, "build_single_query_graph", lambda _settings: graph)
    monkeypatch.setattr(sys, "argv", [
        "ask_graph.py", "--database-id", "sample", "--question", "PRIVATE QUESTION",
    ])
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SqlRejected):
        ask_graph.main()
    assert capsys.readouterr().out == ""
    files = list((tmp_path / "results").glob("graph__*.events.jsonl"))
    assert len(files) == 1
    raw_events = files[0].read_text(encoding="utf-8")
    events = [json.loads(line) for line in raw_events.splitlines()]
    assert events[-2]["event"] == "graph_node_finished"
    assert events[-2]["node"] == "validate_sql"
    assert events[-2]["status"] == "error"
    assert events[-1]["event"] == "graph_finished"
    assert events[-1]["error_type"] == "SqlRejected"
    assert "DELETE FROM items" not in raw_events
    assert "PRIVATE QUESTION" not in raw_events
