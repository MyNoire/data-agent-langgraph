"""用真实 SQLite 文件和替身模型检查图的节点交接。"""

import json
import sqlite3
import sys
from io import StringIO

import pytest

from querypilot.runtime.api import Settings
from querypilot.databases.api import DatabaseRegistry, RegistryError
from querypilot.querying.api import build_query_graph, query_result_row_count
from querypilot.runtime.api import graph_logging
from querypilot.databases.api import SqlRejected
from querypilot.sql_generation.api import GeneratedSql


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
        return GeneratedSql("SELECT name FROM items ORDER BY id")

    graph = build_query_graph(Settings(data_dir=data_dir, max_rows=1), generator=fake_generator)
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
    graph = build_query_graph(
        Settings(data_dir=data_dir),
        generator=lambda *_: GeneratedSql("DELETE FROM items"),
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

    graph = build_query_graph(Settings(data_dir=data_dir), generator=unexpected_generator)
    with pytest.raises(RegistryError):
        graph.invoke({"database_id": "missing", "question": "How many items?"})


def test_graph_progress_events_cover_nodes_without_query_content(tmp_path):
    """验证图和四节点日志的配对、行数与内容隔离。

    参数：tmp_path 为隔离临时目录。
    返回：None；断言失败时由 pytest 报告。
    """
    data_dir, _ = _registered_database(tmp_path)
    graph = build_query_graph(
        Settings(data_dir=data_dir), generator=lambda *_: GeneratedSql("SELECT name FROM items"),
    )
    path = tmp_path / "graph.events.jsonl"
    with graph_logging(path, stream=StringIO(), row_count=query_result_row_count) as logs:
        graph.invoke({"database_id": "sample", "question": "PRIVATE QUESTION"},
                     config=logs.config(run_id="test-graph-run", database_id="sample"))
    raw = path.read_text(encoding="utf-8")
    events = [json.loads(line) for line in raw.splitlines()]
    starts = [e for e in events if e["event"] == "graph_span_started"]
    ends = [e for e in events if e["event"] == "graph_span_finished"]
    assert [e["span_name"] for e in starts] == [
        "querypilot_single", "load_schema", "generate_sql", "validate_sql", "execute_sql",
    ]
    assert {e["span_id"] for e in starts} == {e["span_id"] for e in ends}
    assert len(starts) == len(ends) == 5
    root = next(e for e in starts if e["parent_span_id"] is None)
    assert all(e["parent_span_id"] == root["span_id"] for e in starts if e is not root)
    assert all(e["run_id"] == "test-graph-run" and e["event_version"] == 2 for e in events)
    assert all(e["elapsed_ms"] >= 0 and e["status"] == "success" for e in ends)
    execute = next(e for e in ends if e["span_name"] == "execute_sql")
    assert execute["row_count"] == 2
    assert "PRIVATE QUESTION" not in raw and "SELECT name FROM items" not in raw


def test_graph_failure_logs_node_and_error_type(tmp_path):
    """验证失败位置、异常传播和后续节点未执行。

    参数：tmp_path 为隔离临时目录。
    返回：None；检查节点与根调用各自的错误事件。
    """
    data_dir, _ = _registered_database(tmp_path)
    graph = build_query_graph(Settings(data_dir=data_dir), generator=lambda *_: GeneratedSql("DELETE FROM items"))
    path = tmp_path / "rejected.events.jsonl"
    with graph_logging(path, stream=StringIO()) as logs:
        with pytest.raises(SqlRejected):
            graph.invoke({"database_id": "sample", "question": "PRIVATE QUESTION"},
                         config=logs.config(run_id="rejected-run", database_id="sample"))
    raw = path.read_text(encoding="utf-8")
    events = [json.loads(line) for line in raw.splitlines()]
    errors = [e for e in events if e.get("status") == "error"]
    assert {e["span_name"] for e in errors} == {"validate_sql", "querypilot_single"}
    assert all(e["error_type"] == "SqlRejected" for e in errors)
    assert not any(e["span_name"] == "execute_sql" for e in events)
    assert "DELETE FROM items" not in raw and "PRIVATE QUESTION" not in raw


def test_cli_invoke_preserves_answer_and_progress_log(tmp_path, monkeypatch, capsys):
    """验证 CLI 用 invoke 获得完整答案，同时自动记录进度。

    参数：tmp_path 为隔离目录；monkeypatch 替换模型与参数；capsys 捕获终端输出。
    返回：None；检查 stdout 答案和 JSONL 事件。
    """
    from querypilot.entrypoints import cli
    from querypilot.querying import api as querying_api

    data_dir, _ = _registered_database(tmp_path)
    graph = build_query_graph(
        Settings(data_dir=data_dir), generator=lambda *_: GeneratedSql("SELECT name FROM items ORDER BY id"),
    )
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(data_dir=data_dir, artifacts_dir=tmp_path / "artifacts"))
    monkeypatch.setattr(querying_api, "build_query_graph", lambda _settings, **kwargs: build_query_graph(
        _settings, generator=lambda *_: GeneratedSql("SELECT name FROM items ORDER BY id"), run_context=kwargs.get("run_context")))
    monkeypatch.setattr(sys, "argv", ["ask_graph.py", "--database-id", "sample",
                                    "--question", "PRIVATE QUESTION"])
    monkeypatch.chdir(tmp_path)
    assert cli.main(["query", "ask", "--database-id", "sample", "--question", "PRIVATE QUESTION"]) == 0
    captured = capsys.readouterr()
    answer = json.loads(captured.out)
    assert set(answer) == {"sql", "result"}
    assert answer["result"]["rows"] == [["one"], ["two"]]
    assert "graph_span_finished" in captured.err
    files = list((tmp_path / "artifacts").glob("runs/query/*/*/events.jsonl"))
    assert len(files) == 1
    raw = files[0].read_text(encoding="utf-8")
    events = [json.loads(line) for line in raw.splitlines()]
    root_ends = [e for e in events if e["event"] == "graph_span_finished"
                 and e["parent_span_id"] is None]
    assert len(root_ends) == 1 and root_ends[0]["status"] == "success"
    assert sum(e["event"] == "graph_span_finished" for e in events) == 5
    assert "PRIVATE QUESTION" not in raw and "SELECT name FROM items" not in raw


def test_cli_invoke_failure_records_node_and_graph_error(tmp_path, monkeypatch, capsys):
    """验证 CLI 出错后仍保留失败日志，不打印半成品答案。

    参数：tmp_path 为隔离目录；monkeypatch 替换依赖；capsys 捕获输出。
    返回：None；检查异常、stdout 和节点/图失败事件。
    """
    from querypilot.entrypoints import cli
    from querypilot.querying import api as querying_api

    data_dir, _ = _registered_database(tmp_path)
    graph = build_query_graph(Settings(data_dir=data_dir), generator=lambda *_: GeneratedSql("DELETE FROM items"))
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(data_dir=data_dir, artifacts_dir=tmp_path / "artifacts"))
    monkeypatch.setattr(querying_api, "build_query_graph", lambda _settings, **kwargs: build_query_graph(
        _settings, generator=lambda *_: GeneratedSql("DELETE FROM items"), run_context=kwargs.get("run_context")))
    monkeypatch.setattr(sys, "argv", ["ask_graph.py", "--database-id", "sample",
                                    "--question", "PRIVATE QUESTION"])
    monkeypatch.chdir(tmp_path)
    assert cli.main(["query", "ask", "--database-id", "sample", "--question", "PRIVATE QUESTION"]) == 1
    assert capsys.readouterr().out == ""
    files = list((tmp_path / "artifacts").glob("runs/query/*/*/events.jsonl"))
    assert len(files) == 1
    raw = files[0].read_text(encoding="utf-8")
    events = [json.loads(line) for line in raw.splitlines()]
    errors = [e for e in events if e.get("status") == "error"]
    assert {e["span_name"] for e in errors} == {"validate_sql", "querypilot_single"}
    assert all(e["error_type"] == "SqlRejected" for e in errors)
    assert "DELETE FROM items" not in raw and "PRIVATE QUESTION" not in raw
