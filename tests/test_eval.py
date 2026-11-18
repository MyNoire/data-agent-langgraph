"""区分 fixture 管线自检与模型路径，并检查模型不可用时的计分。"""

import json
import sqlite3
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import pytest

from querypilot.runtime.api import Settings
from querypilot.databases.api import DatabaseRegistry
from querypilot.evaluation.api import run_direct
from querypilot.sql_generation.api import GeneratedSql, direct_prompt
from querypilot.runtime.api import task_run


def test_fixture_eval_plumbing(tmp_path):
    """确认 fixture 仅通过已知 SQL 验证评测管线。

    参数:
        tmp_path: pytest 提供的隔离临时目录。
    返回:
        None；通过断言报告测试结果。
    """
    data = tmp_path / "data"
    data.mkdir()
    path = data / "demo.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE items (id INTEGER)")
        db.execute("INSERT INTO items VALUES (1)")
    DatabaseRegistry(data).register("demo", path)
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([{"db_id": "demo", "question": "How many?", "SQL": "SELECT COUNT(*) FROM items"}]), encoding="utf-8")
    settings = Settings(data_dir=data, artifacts_dir=tmp_path / "artifacts")
    result = run_direct(cases, settings, provider="fixture")
    output = next(settings.artifacts_dir.glob("runs/evaluation/*/*/private/result.json"))
    assert result["provider"] == "fixture"
    assert "graph_version" not in result
    assert result["execution_accuracy"] == 1
    assert result["summary"] == {
        "total": 1, "success": 1, "failure": 0, "mismatch": 0,
        "error": 0, "no_prediction": 0, "valid_sql": 1,
    }
    assert result["records"][0]["outcome"] == "matched"
    assert result["max_response_bytes"] == Settings(data_dir=data).max_response_bytes
    assert json.loads(output.read_text(encoding="utf-8"))["count"] == 1


def test_direct_prompt_contains_schema_question_and_evidence():
    """检查 Prompt 始终含问题和 Schema，证据仅在传入时出现。

    参数:
        无。
    返回:
        None；通过断言报告测试结果。
    """
    prompt = direct_prompt("How many?", "items(id)", "only active")
    assert "items(id)" in prompt and "How many?" in prompt and "only active" in prompt
    assert "Evidence:" not in direct_prompt("How many?", "items(id)")


def test_model_provider_uses_openai_compatible_client(tmp_path, monkeypatch):
    """用替身客户端检查模型请求与 oracle evidence 开关。

    参数:
        tmp_path: pytest 提供的隔离临时目录。
        monkeypatch: pytest 提供的对象替换工具。
    返回:
        None；通过断言报告测试结果。
    """
    data = tmp_path / "data"
    data.mkdir()
    path = data / "demo.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE items (id INTEGER)")
        db.execute("INSERT INTO items VALUES (1)")
    DatabaseRegistry(data).register("demo", path)
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([{"db_id": "demo", "question": "How many?", "evidence": "count only active items",
                                  "SQL": "SELECT COUNT(*) FROM items"}]), encoding="utf-8")
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            """记录 Chat Completions 请求并返回固定 SQL。

            参数:
                kwargs: 模型名称、消息及可选温度参数。
            返回:
                含一条固定 SQL 响应的简易替身对象。
            """
            captured.update(kwargs)
            return type("Response", (), {"usage": None, "choices": [type("Choice", (), {"message": type("Message", (), {"content": "SELECT COUNT(*) FROM items"})()})()]})()

    class FakeClient:
        def __init__(self, **kwargs):
            """构造只记录连接配置的替身模型客户端。

            参数:
                kwargs: OpenAI 客户端初始化配置。
            返回:
                None；初始化 chat.completions 替身属性。
            """
            captured.update(kwargs)
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr("querypilot.sql_generation._model.OpenAI", FakeClient)
    result = run_direct(cases, Settings(data_dir=data, model_base_url="http://localhost:1234/v1", model_name="qwen"), provider="model")
    assert result["execution_accuracy"] == 1
    assert captured["model"] == "qwen"
    assert "items(id)" in captured["messages"][0]["content"]
    assert captured["base_url"] == "http://localhost:1234/v1"
    assert "temperature" not in captured
    assert "count only active items" not in captured["messages"][0]["content"]
    assert result["evidence_mode"] == "none"
    assert result["records"][0]["evidence_used"] is False

    result = run_direct(cases, Settings(data_dir=data, model_temperature=0), provider="model", evidence_mode="oracle")
    assert captured["temperature"] == 0
    assert "count only active items" in captured["messages"][0]["content"]
    assert result["evidence_mode"] == "oracle"
    assert result["records"][0]["evidence_used"] is True


def test_unavailable_model_does_not_report_zero_accuracy(tmp_path, monkeypatch):
    """确认模型不可用时结果标记为不可用而非零准确率。

    参数:
        tmp_path: pytest 提供的隔离临时目录。
        monkeypatch: pytest 提供的对象替换工具。
    返回:
        None；通过断言报告测试结果。
    """
    data = tmp_path / "data"
    data.mkdir()
    path = data / "demo.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE items (id INTEGER)")
    DatabaseRegistry(data).register("demo", path)
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([{"db_id": "demo", "question": "How many?", "SQL": "SELECT COUNT(*) FROM items"}]), encoding="utf-8")

    def unavailable(**_kwargs):
        """模拟无法创建的模型客户端。

        参数:
            _kwargs: 被替换客户端收到的初始化参数。
        返回:
            不返回，始终抛出 ConnectionError。
        """
        raise ConnectionError("model offline")

    monkeypatch.setattr("querypilot.sql_generation._model.OpenAI", unavailable)
    result = run_direct(cases, Settings(data_dir=data), provider="model")
    assert result["status"] == "model_unavailable"
    assert result["execution_accuracy"] is None
    assert result["summary"]["failure"] == 1
    assert result["summary"]["no_prediction"] == 1


def test_eval_summary_separates_matches_mismatches_and_errors(tmp_path):
    """用四种确定性结果验证汇总计数与逐题标签。

    参数:
        tmp_path: pytest 提供的隔离临时目录。
    返回:
        None；成功、结果不匹配、SQL 错误和生成错误必须分别计数。
    """
    data = tmp_path / "data"
    data.mkdir()
    path = data / "items.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE items (id INTEGER)")
        db.execute("INSERT INTO items VALUES (1)")
    DatabaseRegistry(data).register("items", path)
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([
        {"question_id": index, "db_id": "items", "question": question,
         "SQL": "SELECT id FROM items"}
        for index, question in enumerate(("match", "mismatch", "bad SQL", "offline"), 1)
    ]), encoding="utf-8")

    def fake_generate(question, _schema, _evidence, _settings):
        """按题目返回测试 SQL 或模拟生成失败。

        参数:
            question: 用作测试分支选择的题目文本。
            _schema: 未使用的表结构文本。
            _evidence: 未使用的证据文本。
            _settings: 未使用的模型配置。
        返回:
            固定的预测 SQL；offline 分支抛出 RuntimeError。
        """
        if question == "offline":
            raise RuntimeError("model offline")
        return GeneratedSql({"match": "SELECT id FROM items",
                             "mismatch": "SELECT 999",
                             "bad SQL": "DELETE FROM items"}[question])

    events_path = tmp_path / "eval.events.jsonl"
    settings = Settings(data_dir=data, artifacts_dir=tmp_path / "artifacts")
    with task_run("evaluation", settings, command="direct", private=True) as context:
        result = run_direct(cases, settings, provider="model", generator=fake_generate,
                            run_context=context)
        context.complete(result)
    events_path = context.run_dir / "events.jsonl"
    assert result["summary"] == {
        "total": 4, "success": 1, "failure": 3, "mismatch": 1,
        "error": 2, "no_prediction": 1, "valid_sql": 2,
    }
    assert [record["outcome"] for record in result["records"]] == [
        "matched", "mismatched", "error", "error",
    ]
    assert [record["question_id"] for record in result["records"]] == [1, 2, 3, 4]
    assert result["execution_accuracy"] == .25
    raw_events = events_path.read_text(encoding="utf-8")
    events = [json.loads(line) for line in raw_events.splitlines() if json.loads(line)["event"].startswith("eval_")]
    assert [event["event"] for event in events] == [
        "eval_started", "eval_case_started", "eval_case_finished",
        "eval_case_started", "eval_case_finished",
        "eval_case_started", "eval_case_finished",
        "eval_case_started", "eval_case_finished", "eval_finished",
    ]
    assert all(event["run_id"] == result["run_id"] for event in events)
    assert events[-1]["success_count"] == 1
    assert events[-1]["failure_count"] == 3
    assert events[-2]["eta_seconds"] == 0
    assert "SELECT id FROM items" not in raw_events
    assert "bad SQL" not in raw_events


def test_result_runs_are_unique_and_existing_report_is_preserved(tmp_path):
    """相同题集运行两次各有目录，排他导出不能覆盖历史报告。"""
    from querypilot.evaluation.api import export_public
    source = tmp_path / "empty.json"
    source.write_text("[]", encoding="utf-8")
    settings = Settings(data_dir=tmp_path, artifacts_dir=tmp_path / "artifacts")
    first = run_direct(source, settings, provider="fixture")
    second = run_direct(source, settings, provider="fixture")
    assert first["run_id"] != second["run_id"]
    runs = list(settings.artifacts_dir.glob("runs/evaluation/*/*"))
    assert len(runs) == 2
    output = tmp_path / "existing.json"
    output.write_text("original", encoding="utf-8")
    with pytest.raises(FileExistsError):
        export_public(runs[0], output)
    assert output.read_text(encoding="utf-8") == "original"


def test_direct_eval_records_token_usage_for_model_cases_only(tmp_path):
    """model 模式下逐题记录带上生成器报告的用量并汇总；fixture 模式不调用模型，记录里没有用量。"""
    data = tmp_path / "data"
    data.mkdir()
    path = data / "demo.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE items (id INTEGER)")
        connection.execute("INSERT INTO items VALUES (1)")
    DatabaseRegistry(data).register("demo", path)
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([
        {"db_id": "demo", "question": "first", "SQL": "SELECT id FROM items"},
        {"db_id": "demo", "question": "second", "SQL": "SELECT id FROM items"},
    ]), encoding="utf-8")
    settings = Settings(data_dir=data, artifacts_dir=tmp_path / "artifacts")
    usage = {"first": (120, 8), "second": (80, 12)}

    model = run_direct(cases, settings, provider="model",
                       generator=lambda question, *_: GeneratedSql("SELECT id FROM items", *usage[question]))
    fixture = run_direct(cases, settings, provider="fixture")

    assert [(record["prompt_tokens"], record["completion_tokens"]) for record in model["records"]] == [(120, 8), (80, 12)]
    assert model["token_usage"]["prompt_tokens"] == {"count": 2, "total": 200, "mean": 100.0, "median": 100.0}
    assert model["token_usage"]["completion_tokens"] == {"count": 2, "total": 20, "mean": 10.0, "median": 10.0}
    assert all("prompt_tokens" not in record for record in fixture["records"])
    assert fixture["token_usage"]["prompt_tokens"] == {"count": 0, "total": None, "mean": None, "median": None}
