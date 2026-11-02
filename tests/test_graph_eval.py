"""用替身模型验证逐题单图评测、事件关联和结果汇总。"""

import json
import sqlite3
import sys
from pathlib import Path
from io import StringIO

from querypilot.runtime.api import Settings
from querypilot.databases.api import DatabaseRegistry
from querypilot.evaluation.api import run_graph
from querypilot.runtime.api import task_run
from querypilot.querying.api import query_result_row_count
from querypilot.runtime.api import graph_logging


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

    settings = Settings(data_dir=data_dir, artifacts_dir=tmp_path / "artifacts")
    result = run_graph(cases, settings, generator=fake_generator)
    batch_id = result["run_id"]
    run_dir = next(settings.artifacts_dir.glob("runs/evaluation/*/*"))
    output = run_dir / "private/result.json"
    events_path = run_dir / "events.jsonl"
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
    assert result["records"][2]["predicted_sql"] == "DELETE FROM items"
    assert json.loads(output.read_text(encoding="utf-8"))["run_id"] == batch_id

    raw = events_path.read_text(encoding="utf-8")
    events = [json.loads(line) for line in raw.splitlines() if json.loads(line)["event"] not in {"run_started", "run_finished"}]
    assert events[0]["event"] == "eval_started"
    assert events[-1]["event"] == "eval_finished"
    assert events[-1]["success_count"] == 1
    assert events[-1]["failure_count"] == 2
    for index in range(1, 4):
        child_id = f"{batch_id}:case:{index}"
        case_events = [event for event in events if event.get("graph_run_id") == child_id]
        node_events = [event for event in events if event.get("run_id") == child_id]
        assert [event["event"] for event in case_events] == [
            "eval_case_started", "eval_case_finished",
        ]
        assert node_events
        assert all(event["parent_run_id"] == batch_id for event in node_events)
        assert all(event["question_id"] == index for event in node_events)
        assert all(event["case_index"] == index for event in node_events)
    assert not any(event.get("node") == "execute_sql" and event.get("case_index") == 3 for event in events)
    mismatch_root = next(event for event in events
                         if event["event"] == "graph_span_finished"
                         and event["run_id"] == f"{batch_id}:case:2"
                         and event["parent_span_id"] is None)
    assert mismatch_root["status"] == "success"
    assert result["records"][1]["outcome"] == "mismatched"
    assert "SECRET" not in raw
    assert "DELETE FROM items" not in raw

    from querypilot.entrypoints.cli import main

    public_path = tmp_path / "graph-public.json"
    assert main(["--artifacts-dir", str(tmp_path / "exports"), "eval", "export-public", "--input", str(output), "--output", str(public_path)]) == 0
    assert str(public_path) in capsys.readouterr().out
    public = json.loads(public_path.read_text(encoding="utf-8"))
    assert public["graph_version"] == "single-v1"
    assert public["records"][0]["graph_run_id"] == f"{batch_id}:case:1"
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
    from querypilot.entrypoints import cli
    from querypilot.evaluation.sql import api as evaluation_api

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
    monkeypatch.setattr(evaluation_api, "generate_sql", lambda *_: "SELECT COUNT(*) FROM items")
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(data_dir=data_dir, model_name="test-model",
                                                            artifacts_dir=tmp_path / "artifacts"))
    monkeypatch.chdir(tmp_path)

    assert cli.main(["eval", "graph", "--cases", str(cases)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["provider"] == "graph"
    assert summary["summary"]["success"] == 1
    assert "runs" in Path(summary["output_file"]).parts
    assert "evaluation" in Path(summary["output_file"]).parts
    events_path = tmp_path / summary["event_file"]
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert sum(event["event"] == "graph_span_finished" and event.get("parent_span_id") is not None
               for event in events) == 4

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

    unavailable = run_graph(
        cases, Settings(data_dir=data_dir, artifacts_dir=tmp_path / "artifacts"), generator=unavailable_generator,
    )
    assert unavailable["status"] == "no_predictions"
    assert unavailable["execution_accuracy"] is None
    assert unavailable["valid_sql_rate"] is None
