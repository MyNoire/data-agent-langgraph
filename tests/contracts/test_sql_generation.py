"""正式 SQL 生成入口的请求约定；不连接模型服务。"""

from types import SimpleNamespace

import pytest

from querypilot.runtime.api import Settings
from querypilot.sql_generation.api import PROMPT_VERSION, direct_prompt, generate_sql


def test_prompt_keeps_original_evidence_policy():
    """验证固定提示词及显式证据，不引入额外消息。"""
    assert PROMPT_VERSION == "direct-v2"
    assert "Evidence:" not in direct_prompt("count", "items(id)")
    assert "Evidence: approved\nQuestion: count" in direct_prompt("count", "items(id)", "approved")


@pytest.mark.parametrize("temperature", [None, 0])
def test_client_request_and_original_cleanup(monkeypatch, temperature):
    """输入替代客户端；核对超时、单 user 消息和温度省略，输出清理保持。"""
    captured = {}

    def complete(**request):
        """记录请求并返回带原有围栏的固定 SQL，无服务调用。"""
        captured.update(request)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="```sql\nSELECT 1\n```"))],
                               usage=None)

    def client(**options):
        """记录客户端选项，返回受控完成方法。"""
        captured.update(options)
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=complete)))

    monkeypatch.setattr("querypilot.sql_generation._model.OpenAI", client)
    value = generate_sql("count", "items(id)", "", Settings(_env_file=None, model_temperature=temperature))
    assert value.sql == "SELECT 1"
    assert captured["timeout"] == 60
    assert len(captured["messages"]) == 1
    assert captured["messages"][0] == {"role": "user", "content": direct_prompt("count", "items(id)")}
    assert ("temperature" in captured) == (temperature is not None)


def _client_returning(response):
    """替身 OpenAI 客户端工厂：任何请求都返回同一个响应，不联网。"""
    def client(**_options):
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_request: response)))
    return client


def test_generated_sql_carries_the_token_usage_reported_by_the_service(monkeypatch):
    """接口返回用量时，结果带上 prompt_tokens 和 completion_tokens。"""
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="SELECT 1"))],
        usage=SimpleNamespace(prompt_tokens=321, completion_tokens=45, total_tokens=366),
    )
    monkeypatch.setattr("querypilot.sql_generation._model.OpenAI", _client_returning(response))

    generated = generate_sql("count", "items(id)", "", Settings(_env_file=None))

    assert generated.sql == "SELECT 1"
    assert (generated.prompt_tokens, generated.completion_tokens) == (321, 45)


def test_generated_sql_has_empty_usage_when_the_service_reports_none(monkeypatch):
    """接口没有返回用量时，两项用量为空，SQL 照常返回。"""
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="SELECT 1"))], usage=None)
    monkeypatch.setattr("querypilot.sql_generation._model.OpenAI", _client_returning(response))

    generated = generate_sql("count", "items(id)", "", Settings(_env_file=None))

    assert generated.sql == "SELECT 1"
    assert (generated.prompt_tokens, generated.completion_tokens) == (None, None)
