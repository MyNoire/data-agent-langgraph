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
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="```sql\nSELECT 1\n```"))])

    def client(**options):
        """记录客户端选项，返回受控完成方法。"""
        captured.update(options)
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=complete)))

    monkeypatch.setattr("querypilot.sql_generation._model.OpenAI", client)
    value = generate_sql("count", "items(id)", "", Settings(_env_file=None, model_temperature=temperature))
    assert value == "SELECT 1"
    assert captured["timeout"] == 60
    assert len(captured["messages"]) == 1
    assert captured["messages"][0] == {"role": "user", "content": direct_prompt("count", "items(id)")}
    assert ("temperature" in captured) == (temperature is not None)
