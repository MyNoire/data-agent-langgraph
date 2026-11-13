"""用替身模型验证逐题单图评测、事件关联和结果汇总。"""

import json
import logging
import sqlite3
import threading
import sys
from pathlib import Path
from io import StringIO

import pytest

from querypilot.runtime.api import Settings
from querypilot.databases.api import DatabaseRegistry
from querypilot.evaluation.api import run_graph
from querypilot.runtime.api import task_run
from querypilot.querying.api import query_result_row_count
from querypilot.runtime.api import graph_logging
from querypilot.sql_generation.api import GeneratedSql


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
        return GeneratedSql({
            "MATCH SECRET": "SELECT name FROM items ORDER BY id",
            "MISMATCH SECRET": "SELECT name FROM items WHERE id = 1",
            "REJECT SECRET": "DELETE FROM items",
        }[question])

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
    monkeypatch.setattr(evaluation_api, "generate_sql", lambda *_: GeneratedSql("SELECT COUNT(*) FROM items"))
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


def _sample_run(tmp_path, cases: list[dict]) -> tuple[Path, Settings]:
    """建一个只有 items 表（id 为 1、2）的库并登记为 sample，写出题集，返回题集路径和运行配置。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    database = data_dir / "sample.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE items (id INTEGER, name TEXT)")
        connection.executemany("INSERT INTO items VALUES (?, ?)", [(1, "one"), (2, "two")])
    DatabaseRegistry(data_dir).register("sample", database)
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps(cases), encoding="utf-8")
    return cases_path, Settings(data_dir=data_dir, artifacts_dir=tmp_path / "artifacts")


def test_graph_eval_record_carries_the_frozen_flag_of_its_case(tmp_path):
    """题目带 frozen 标记时逐题记录原样带上；题目没有这个标记时记录里也没有。"""
    cases, settings = _sample_run(tmp_path, [
        {"question_id": 1, "db_id": "sample", "question": "q1", "SQL": "SELECT id FROM items", "frozen": True},
        {"question_id": 2, "db_id": "sample", "question": "q2", "SQL": "SELECT id FROM items", "frozen": False},
        {"question_id": 3, "db_id": "sample", "question": "q3", "SQL": "SELECT id FROM items"},
    ])

    result = run_graph(cases, settings, generator=lambda *_: GeneratedSql("SELECT id FROM items"))

    assert result["records"][0]["frozen"] is True
    assert result["records"][1]["frozen"] is False
    assert "frozen" not in result["records"][2]


MATCHING_SQL = "SELECT id FROM items"


def _cases(count: int) -> list[dict]:
    """count 道题，问题依次为 q1、q2…，金标准 SQL 都是 MATCHING_SQL。"""
    return [{"question_id": number, "db_id": "sample", "question": f"q{number}", "SQL": MATCHING_SQL}
            for number in range(1, count + 1)]


def test_workers_cases_are_in_the_graph_at_the_same_time(tmp_path):
    """输入：3 道题，workers=3；假生成器里有一道关卡，要 3 个线程都到了才一起放行。

    输出：3 道题都匹配。一题一题串行跑时，第 1 题等不到另外两题，会在关卡上超时，记成 error。
    """
    barrier = threading.Barrier(3, timeout=2)

    def generator(*_):
        barrier.wait()
        return GeneratedSql(MATCHING_SQL)

    cases, settings = _sample_run(tmp_path, _cases(3))

    result = run_graph(cases, settings, generator=generator, workers=3)

    assert [record["outcome"] for record in result["records"]] == ["matched", "matched", "matched"]


class _EventWatcher:
    """接在运行上的进度流：记下写出的每条事件；看到指定题目的完成事件时点亮一个信号。"""

    def __init__(self, question_id: int, reported: threading.Event):
        self.events, self._question_id, self._reported = [], question_id, reported

    def write(self, text: str) -> None:
        event = json.loads(text)
        self.events.append(event)
        if event["event"] == "eval_case_finished" and event["question_id"] == self._question_id:
            self._reported.set()

    def flush(self) -> None:
        pass


def test_report_keeps_case_order_while_events_follow_completion_order(tmp_path):
    """输入：两道题，workers=2；第 1 题要等主线程写出第 2 题的完成事件之后才返回，所以第 2 题一定先完成。

    输出：报告的 records 仍按题集顺序；eval_case_finished 按完成顺序写出，completed_cases 从 1 递增，
    case_index 是这题在题集里的位置。
    """
    second_reported = threading.Event()
    watcher = _EventWatcher(2, second_reported)

    def generator(question, *_):
        if question == "q1":
            assert second_reported.wait(5)
        return GeneratedSql(MATCHING_SQL)

    cases, settings = _sample_run(tmp_path, _cases(2))
    with task_run("evaluation", settings, command="graph", private=True) as context:
        context.attach_stream(watcher)
        result = run_graph(cases, settings, generator=generator, workers=2, run_context=context)
        context.complete(result)

    assert [(record["index"], record["question_id"]) for record in result["records"]] == [(0, 1), (1, 2)]
    assert [record["graph_run_id"] for record in result["records"]] == [
        f"{result['run_id']}:case:1", f"{result['run_id']}:case:2"]
    finished = [event for event in watcher.events if event["event"] == "eval_case_finished"]
    assert [(e["question_id"], e["case_index"], e["completed_cases"]) for e in finished] == [(2, 2, 1), (1, 1, 2)]


def test_case_error_only_affects_its_own_case_when_cases_run_concurrently(tmp_path):
    """输入：3 道题同时在图里（关卡保证），第 2 题的 SQL 会被安全检查拒绝。

    输出：第 2 题记为 error，另外两题照常匹配，整批不中止。
    """
    barrier = threading.Barrier(3, timeout=5)

    def generator(question, *_):
        barrier.wait()
        return GeneratedSql("DELETE FROM items" if question == "q2" else MATCHING_SQL)

    cases, settings = _sample_run(tmp_path, _cases(3))

    result = run_graph(cases, settings, generator=generator, workers=3)

    assert [(record["question_id"], record["outcome"]) for record in result["records"]] == [
        (1, "matched"), (2, "error"), (3, "matched")]
    assert result["records"][1]["error_type"] == "SqlRejected"


def test_main_thread_failure_starts_no_new_case(tmp_path):
    """输入：workers=2，5 道题；第 1 题立刻完成，第 2 题一直在图里。主线程在写第 1 题的完成事件时出错，
    并在出错的那一刻放行第 2 题。Ctrl+C 走的是同一条路径：主线程带着异常离开。

    输出：抛出主线程的错误；只有前两题被调用过，第 3–5 题没有开始。
    """
    release_second = threading.Event()
    calls = []

    def generator(question, *_):
        calls.append(question)
        if question == "q2":
            assert release_second.wait(5)
        return GeneratedSql(MATCHING_SQL)

    class BreakOnFirstFinishedCase(logging.Filter):
        def filter(self, record):
            if record.getMessage() == "eval_case_finished":
                release_second.set()
                raise RuntimeError("main thread failed")
            return True

    cases, settings = _sample_run(tmp_path, _cases(5))
    with pytest.raises(RuntimeError, match="main thread failed"):
        with task_run("evaluation", settings, command="graph", private=True) as context:
            context.logger.addFilter(BreakOnFirstFinishedCase())
            run_graph(cases, settings, generator=generator, workers=2, run_context=context)

    assert sorted(calls) == ["q1", "q2"]


def test_report_is_the_same_with_one_worker_and_with_four(tmp_path):
    """输入：同一份 8 题题集（匹配、不匹配、被拒三种都有）跑两次，workers 分别为 1 和 4。

    输出：除了耗时和运行编号，两份报告的逐题记录和汇总完全相同。
    """
    def generator(question, *_):
        number = int(question[1:])
        return GeneratedSql({0: MATCHING_SQL, 1: "SELECT id FROM items WHERE id = 1", 2: "DELETE FROM items"}[number % 3])

    def comparable(result):
        records = [{key: value for key, value in record.items() if key not in {"latency_ms", "graph_run_id"}}
                   for record in result["records"]]
        return records, result["summary"], result["execution_accuracy"], result["valid_sql_rate"]

    cases, settings = _sample_run(tmp_path, _cases(8))

    one = run_graph(cases, settings, generator=generator, workers=1)
    four = run_graph(cases, settings, generator=generator, workers=4)

    assert comparable(one) == comparable(four)
    assert one["summary"]["success"] == 2 and one["summary"]["mismatch"] == 3 and one["summary"]["error"] == 3


def _run_config(settings: Settings, run_id: str) -> dict:
    """读出一次评测运行保存在 config.json 里的参数。"""
    run_dir = next(path for path in settings.artifacts_dir.glob("runs/evaluation/*/*") if path.name == run_id)
    return json.loads((run_dir / "config.json").read_text(encoding="utf-8"))["parameters"]


def test_workers_is_recorded_in_the_run_config_and_the_report_and_defaults_to_one(tmp_path):
    """输入：同一份题集跑两次，一次不传 workers，一次 workers=3；输出：运行配置和报告分别记下 1 和 3。"""
    cases, settings = _sample_run(tmp_path, _cases(2))

    default = run_graph(cases, settings, generator=lambda *_: GeneratedSql(MATCHING_SQL))
    three = run_graph(cases, settings, generator=lambda *_: GeneratedSql(MATCHING_SQL), workers=3)

    assert default["workers"] == 1 and three["workers"] == 3
    assert _run_config(settings, default["run_id"])["workers"] == 1
    assert _run_config(settings, three["run_id"])["workers"] == 3


def test_cli_workers_flag_reaches_the_runner_and_the_run_config(tmp_path, monkeypatch, capsys):
    """输入：eval graph --workers 2，两道题在假生成器的关卡上要同时到齐才放行。

    输出：两道题都匹配（串行会在关卡上超时），打印的摘要和运行配置里的线程数都是 2。
    """
    from querypilot.entrypoints import cli
    from querypilot.evaluation.sql import api as evaluation_api

    barrier = threading.Barrier(2, timeout=2)

    def generator(*_):
        barrier.wait()
        return GeneratedSql(MATCHING_SQL)

    cases, settings = _sample_run(tmp_path, _cases(2))
    monkeypatch.setattr(evaluation_api, "generate_sql", generator)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    assert cli.main(["eval", "graph", "--cases", str(cases), "--workers", "2"]) == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["summary"]["success"] == 2
    assert summary["workers"] == 2
    assert _run_config(settings, summary["run_id"])["workers"] == 2


@pytest.mark.parametrize("workers", ["0", "-1", "two"])
def test_cli_rejects_workers_below_one_before_any_run_is_recorded(tmp_path, monkeypatch, workers):
    """输入：--workers 不是正整数；输出：参数解析阶段就退出，没有建立任何运行记录。"""
    from querypilot.entrypoints import cli

    cases, settings = _sample_run(tmp_path, _cases(1))
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    with pytest.raises(SystemExit):
        cli.main(["eval", "graph", "--cases", str(cases), "--workers", workers])

    assert not (tmp_path / "artifacts").exists()


def test_graph_eval_records_token_usage_per_case_and_in_total(tmp_path):
    """输入：3 道题，假生成器返回的用量依次是 (100, 10)、(200, 30)、没有用量。

    输出：逐题记录带上各自的两项用量（没有用量的为空）；报告的 token_usage 只对有值的题统计
    题数、总数、平均数和中位数。
    """
    usage = {"q1": (100, 10), "q2": (200, 30), "q3": (None, None)}

    def generator(question, *_):
        return GeneratedSql(MATCHING_SQL, *usage[question])

    cases, settings = _sample_run(tmp_path, _cases(3))

    result = run_graph(cases, settings, generator=generator)

    assert [(record["prompt_tokens"], record["completion_tokens"]) for record in result["records"]] == [
        (100, 10), (200, 30), (None, None)]
    assert result["token_usage"] == {
        "prompt_tokens": {"count": 2, "total": 300, "mean": 150.0, "median": 150.0},
        "completion_tokens": {"count": 2, "total": 40, "mean": 20.0, "median": 20.0},
    }
