"""用替身模型验证逐题单图评测、事件关联和结果汇总。"""

import json
import sqlite3
import sys
from io import StringIO

from querypilot.config import Settings
from querypilot.database.registry import DatabaseRegistry
from querypilot.evals.graph import run_graph_eval
from querypilot.evals.naming import result_path
from querypilot.logging import progress_logging


def test_graph_eval_records_match_mismatch_and_guard_failure(tmp_path, monkeypatch, capsys):
    """确认三类结果和逐节点事件与题目、批次可一一关联。

    参数:
        tmp_path: pytest 提供的隔离目录。
        monkeypatch: pytest 提供的临时命令行参数替换器。
        capsys: pytest 提供的标准输出捕获器。
    返回:
        None；通过汇总、逐题记录和 JSONL 断言验证行为。
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    database = data_dir / "sample.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE items (id INTEGER, name TEXT)")
        connection.executemany("INSERT INTO items VALUES (?, ?)", [(1, "one"), (2, "two")])
    DatabaseRegistry(data_dir).register("sample", database)
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([
        {"question_id": 1, "db_id": "sample", "question": "MATCH SECRET",
         "SQL": "SELECT name FROM items ORDER BY id"},
        {"question_id": 2, "db_id": "sample", "question": "MISMATCH SECRET",
         "SQL": "SELECT name FROM items ORDER BY id"},
        {"question_id": 3, "db_id": "sample", "question": "REJECT SECRET",
         "SQL": "SELECT name FROM items ORDER BY id"},
    ]), encoding="utf-8")

    def fake_generator(question: str, schema: str, evidence: str, settings: Settings) -> str:
        """按题目返回固定 SQL，避免测试调用真实模型。

        参数:
            question: 当前问题，用作选择不同结果路径。
            schema: 图读取的表结构，本测试不使用。
            evidence: 图提供的证据，本测试不使用。
            settings: 当前模型配置，本测试不使用。
        返回:
            对应题目的 SELECT 或应被拒绝的 DELETE。
        """
        return {
            "MATCH SECRET": "SELECT name FROM items ORDER BY id",
            "MISMATCH SECRET": "SELECT name FROM items WHERE id = 1",
            "REJECT SECRET": "DELETE FROM items",
        }[question]

    output = tmp_path / "graph-result.json"
    events_path = tmp_path / "graph-events.jsonl"
    with progress_logging(events_path, stream=StringIO()):
        result = run_graph_eval(
            cases, Settings(data_dir=data_dir), output=output,
            run_id="batch-001", generator=fake_generator,
        )
    assert result["provider"] == "graph"
    assert result["graph_version"] == "single-v1"
    assert result["summary"] == {
        "total": 3, "success": 1, "failure": 2, "mismatch": 1,
        "error": 1, "no_prediction": 0, "valid_sql": 2,
    }
    assert [record["outcome"] for record in result["records"]] == [
        "matched", "mismatched", "error",
    ]
    assert result["records"][2]["error_type"] == "SqlRejected"
    assert json.loads(output.read_text(encoding="utf-8"))["run_id"] == "batch-001"

    raw = events_path.read_text(encoding="utf-8")
    events = [json.loads(line) for line in raw.splitlines()]
    assert events[0]["event"] == "eval_started"
    assert events[-1]["event"] == "eval_finished"
    assert events[-1]["success_count"] == 1
    assert events[-1]["failure_count"] == 2
    for index in range(1, 4):
        child_id = f"batch-001:case:{index}"
        case_events = [event for event in events if event.get("graph_run_id") == child_id]
        node_events = [event for event in events if event.get("run_id") == child_id]
        assert [event["event"] for event in case_events] == [
            "eval_case_started", "eval_case_finished",
        ]
        assert all(event["parent_run_id"] == "batch-001" for event in node_events)
        assert all(event["question_id"] == index for event in node_events)
        assert all(event["case_index"] == index for event in node_events)
    assert not any(event.get("node") == "execute_sql" and event.get("case_index") == 3 for event in events)
    assert "SECRET" not in raw
    assert "DELETE FROM items" not in raw

    from scripts import export_eval_public

    public_path = tmp_path / "graph-public.json"
    monkeypatch.setattr(sys, "argv", ["export_eval_public.py", str(output), str(public_path)])
    export_eval_public.main()
    assert str(public_path) in capsys.readouterr().out
    public = json.loads(public_path.read_text(encoding="utf-8"))
    assert public["graph_version"] == "single-v1"
    assert public["records"][0]["graph_run_id"] == "batch-001:case:1"
    assert "model_base_url" not in public
    assert "question" not in public["records"][0]


def test_graph_eval_cli_uses_graph_and_keeps_model_runs_separate(tmp_path, monkeypatch, capsys):
    """确认 CLI 命令实际进入单图，并生成可辨认的结果与事件文件。

    参数:
        tmp_path: pytest 提供的隔离目录。
        monkeypatch: pytest 提供的临时依赖替换器。
        capsys: pytest 提供的标准输出捕获器。
    返回:
        None；通过生成文件、汇总和节点事件断言入口行为。
    """
    from scripts import run_graph_eval as cli
    from querypilot.evals import graph as graph_module

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    database = data_dir / "sample.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE items (id INTEGER)")
        connection.execute("INSERT INTO items VALUES (1)")
    DatabaseRegistry(data_dir).register("sample", database)
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([
        {"question_id": 7, "db_id": "sample", "question": "Count items",
         "SQL": "SELECT COUNT(*) FROM items"},
    ]), encoding="utf-8")
    real_builder = graph_module.build_single_query_graph

    def fake_builder(settings: Settings, *, generator):
        """构造仍执行真实节点、但不用模型服务的单图。

        参数:
            settings: CLI 传入的数据和执行配置。
            generator: 默认模型生成器，本测试替换。
        返回:
            注入固定 SQL 的真实编译图。
        """
        return real_builder(settings, generator=lambda *_: "SELECT COUNT(*) FROM items")

    monkeypatch.setattr(graph_module, "build_single_query_graph", fake_builder)
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(data_dir=data_dir, model_name="test-model"))
    monkeypatch.setattr(sys, "argv", ["run_graph_eval.py", "--cases", str(cases)])
    monkeypatch.chdir(tmp_path)

    cli.main()
    summary = json.loads(capsys.readouterr().out)
    assert summary["provider"] == "graph"
    assert summary["summary"]["success"] == 1
    assert "graph-single-v1" in summary["output_file"]
    events_path = tmp_path / summary["event_file"]
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert sum(event["event"] == "graph_node_finished" for event in events) == 4
    assert "graph-single-v1" in result_path(cases, "graph", "test-model", "none").name
    assert "graph-single-v1" not in result_path(cases, "model", "test-model", "none").name
    monkeypatch.setattr(graph_module, "build_single_query_graph", real_builder)

    def unavailable_generator(question: str, schema: str, evidence: str, settings: Settings) -> str:
        """模拟模型不可用，使评测没有可计分的 SQL 预测。

        参数:
            question: 用户问题，本测试不使用。
            schema: 图读取的表结构，本测试不使用。
            evidence: 图传入的证据，本测试不使用。
            settings: 运行配置，本测试不使用。
        返回:
            不返回；总是抛出 ConnectionError。
        """
        raise ConnectionError("model unavailable")

    with progress_logging(tmp_path / "unavailable.events.jsonl", stream=StringIO()):
        unavailable = run_graph_eval(
            cases, Settings(data_dir=data_dir), generator=unavailable_generator,
        )
    assert unavailable["status"] == "no_predictions"
    assert unavailable["execution_accuracy"] is None
    assert unavailable["valid_sql_rate"] is None
