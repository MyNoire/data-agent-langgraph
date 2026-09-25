"""区分 fixture 管线自检与模型路径，并检查模型不可用时的计分。"""

import json
import sqlite3

from querypilot.config import Settings
from querypilot.database.registry import DatabaseRegistry
from querypilot.evals.baseline import direct_prompt, run_eval


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
