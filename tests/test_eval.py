"""区分 fixture 管线自检与模型路径，并检查模型不可用时的计分。"""

import json
import sqlite3
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import pytest

from querypilot.config import Settings
from querypilot.database.registry import DatabaseRegistry
from querypilot.evals.baseline import direct_prompt, run_eval
from querypilot.evals.naming import result_path
from querypilot.logging import progress_logging


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
    output = tmp_path / "result.json"
    result = run_eval(cases, Settings(data_dir=data), provider="fixture", output=output)
    assert result["provider"] == "fixture"
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
            return type("Response", (), {"choices": [type("Choice", (), {"message": type("Message", (), {"content": "SELECT COUNT(*) FROM items"})()})()]})()

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

    monkeypatch.setattr("querypilot.evals.baseline.OpenAI", FakeClient)
    result = run_eval(cases, Settings(data_dir=data, model_base_url="http://localhost:1234/v1", model_name="qwen"), provider="model")
    assert result["execution_accuracy"] == 1
    assert captured["model"] == "qwen"
    assert "items(id)" in captured["messages"][0]["content"]
    assert captured["base_url"] == "http://localhost:1234/v1"
    assert "temperature" not in captured
    assert "count only active items" not in captured["messages"][0]["content"]
    assert result["evidence_mode"] == "none"
    assert result["records"][0]["evidence_used"] is False

    result = run_eval(cases, Settings(data_dir=data, model_temperature=0), provider="model", evidence_mode="oracle")
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

    monkeypatch.setattr("querypilot.evals.baseline.OpenAI", unavailable)
    result = run_eval(cases, Settings(data_dir=data), provider="model")
    assert result["status"] == "model_unavailable"
    assert result["execution_accuracy"] is None
    assert result["summary"]["failure"] == 1
    assert result["summary"]["no_prediction"] == 1


def test_eval_summary_separates_matches_mismatches_and_errors(tmp_path, monkeypatch):
    """用四种确定性结果验证汇总计数与逐题标签。

    参数:
        tmp_path: pytest 提供的隔离临时目录。
        monkeypatch: 用假生成器替换真实模型调用。
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
        return {"match": "SELECT id FROM items",
                "mismatch": "SELECT 999",
                "bad SQL": "DELETE FROM items"}[question]

    monkeypatch.setattr("querypilot.evals.baseline.generate_sql", fake_generate)
    events_path = tmp_path / "eval.events.jsonl"
    with progress_logging(events_path, stream=StringIO()):
        result = run_eval(cases, Settings(data_dir=data), provider="model")
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
    events = [json.loads(line) for line in raw_events.splitlines()]
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


def test_result_filename_and_existing_output_guard(tmp_path):
    """验证自动命名可辨认且旧评测文件不会被覆盖。

    参数:
        tmp_path: pytest 提供的隔离临时目录。
    返回:
        None；文件名包含题集、模型、证据模式、时间，重名时保留原文件。
    """
    timestamp = datetime(2026, 9, 28, 12, 30, 0, tzinfo=timezone.utc)
    named = result_path(Path("evals/bird_final100.json"), "model",
                        "Qwen/Qwen2.5-Coder", "none", timestamp=timestamp)
    assert named == Path(
        "results/bird-final100__model-qwen-qwen2-5-coder__direct-v2__"
        "evidence-none__20260928T123000000000Z.json"
    )
    assert result_path(Path("evals/bird_smoke.json"), "fixture", "unused", "none",
                       timestamp=timestamp) == Path(
        "results/bird-smoke__fixture__20260928T123000000000Z.json"
    )
    output = tmp_path / "existing.json"
    output.write_text("original", encoding="utf-8")
    with pytest.raises(FileExistsError):
        run_eval(tmp_path / "missing.json", Settings(data_dir=tmp_path),
                 provider="fixture", output=output)
    assert output.read_text(encoding="utf-8") == "original"
