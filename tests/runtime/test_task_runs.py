"""通过真实公开查询入口验证运行记录，不用日志辅助函数冒充任务。"""

import json
import sqlite3

from querypilot.databases.api import DatabaseRegistry
from querypilot.querying.api import ask, build_query_graph
from querypilot.runtime.api import Settings, read_run_result, task_run


def registered_settings(tmp_path):
    """创建原创 SQLite；返回仅指向隔离资料和运行目录的配置。"""
    path = tmp_path / "sample.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE items(id INTEGER, label TEXT)")
        connection.execute("INSERT INTO items VALUES (1, 'ROW_PRIVATE')")
    DatabaseRegistry(tmp_path).register("demo", path)
    return Settings(_env_file=None, data_dir=tmp_path, artifacts_dir=tmp_path / "artifacts")


def test_public_queries_keep_one_directory_each_and_safe_records(tmp_path):
    """两次完整单图各建一份记录，执行子调用复用，安全记录不含正文。"""
    settings = registered_settings(tmp_path)
    for _ in range(2):
        result = ask("demo", "QUESTION_PRIVATE", settings,
                     generator=lambda *_: "SELECT label FROM items")
        assert result["result"]["rows"] == [["ROW_PRIVATE"]]
    runs = sorted(settings.artifacts_dir.glob("runs/query/*/*"))
    assert len(runs) == 2
    assert runs[0].name != runs[1].name
    for run in runs:
        state = json.loads((run / "run.json").read_text(encoding="utf-8"))
        assert state["status"] == "completed" and state["result_path"] == "private/result.json"
        assert read_run_result(run)["result"]["rows"] == [["ROW_PRIVATE"]]
        safe = "".join((run / name).read_text(encoding="utf-8") for name in ["run.json", "config.json", "events.jsonl"])
        assert all(value not in safe for value in ["QUESTION_PRIVATE", "ROW_PRIVATE", "SELECT label"])
        events = [json.loads(line) for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        assert any(event.get("event_version") == 2 for event in events)
        assert "SELECT label" in (run / "private/sql-audit.jsonl").read_text(encoding="utf-8")


def test_compile_is_pure_and_nested_ask_borrows_context(tmp_path):
    """编译不建作业；上层任务中的 ask 不发布/关闭上层记录。"""
    settings = registered_settings(tmp_path)
    build_query_graph(settings, generator=lambda *_: "SELECT id FROM items")
    assert not settings.artifacts_dir.exists()
    with task_run("evaluation", settings, command="fixture") as context:
        for _ in range(2):
            assert ask("demo", "list", settings, generator=lambda *_: "SELECT id FROM items",
                       run_context=context)["result"]["rows"] == [[1]]
        assert not (context.run_dir / "result.json").exists()
        context.complete({"calls": 2})
    assert len(list(settings.artifacts_dir.glob("runs/*/*/*"))) == 1
