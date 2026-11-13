"""SiliconFlowReranker 的请求与结果检查；用 httpx.MockTransport 代替网络，不调用真实服务。"""

import json

import httpx
import openai
import pytest
from pydantic import SecretStr

from querypilot.knowledge.api import QUERY_TASK
from querypilot.retrieval.api import RerankError, RerankSettings, SiliconFlowReranker

SECRET = "sk-private-test-key"
QUERY = "Which label has the most items?"
DOCUMENTS = ["表 items：物品", "列 items.label：标签", "表 events：事件"]


def _settings():
    return RerankSettings(_env_file=None, base_url="https://rerank.test/v1",
                          model="Qwen/Qwen3-Reranker-8B", api_key=SecretStr(SECRET))


def _reranker(respond):
    """respond(请求体) → httpx.Response；记录每次收到的请求。"""
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return respond(json.loads(request.content))
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return SiliconFlowReranker(_settings(), http_client=client), requests


def _results(*pairs):
    """(index, 分数) → 服务返回的响应，字段与硅基流动的真实响应一致；服务按分数从高到低返回，不按输入顺序。"""
    return httpx.Response(200, json={"id": "r1", "results": [{"document": None, "index": index,
                                                             "relevance_score": score} for index, score in pairs],
                                     "meta": {"tokens": {"input_tokens": 1, "output_tokens": 0}}})


def test_profile_names_model_text_recipe_and_instruction_version():
    reranker, _ = _reranker(lambda body: _results())
    assert reranker.profile_id == "siliconflow:Qwen/Qwen3-Reranker-8B:content-v1:task-instruct-v1"


def test_request_goes_to_the_rerank_endpoint_and_scores_follow_input_order():
    """输入：3 段文本；服务按分数从高到低返回 index 2、0、1。

    输出：分数按输入顺序排好；请求发到 base_url 后的 /rerank，带模型、查询原文和文本原文，
    任务指令是 Embedding 查询用的同一句话，不要求返回文本，用配置的密钥鉴权。
    """
    reranker, requests = _reranker(lambda body: _results((2, 0.9), (0, 0.4), (1, 0.1)))
    assert reranker.rerank(QUERY, DOCUMENTS) == [0.4, 0.1, 0.9]
    request, = requests
    assert (request.method, str(request.url)) == ("POST", "https://rerank.test/v1/rerank")
    assert json.loads(request.content) == {"model": "Qwen/Qwen3-Reranker-8B", "query": QUERY,
                                           "documents": DOCUMENTS, "instruction": QUERY_TASK,
                                           "return_documents": False}
    assert request.headers["authorization"] == f"Bearer {SECRET}"


def _raw(text: str) -> httpx.Response:
    """原样返回一段 JSON 文本（NaN 这类值没法用 json= 参数构造）。"""
    return httpx.Response(200, content=text.encode(), headers={"content-type": "application/json"})


@pytest.mark.parametrize("respond", [
    lambda body: _results((0, 0.9), (1, 0.4)),                          # 少一条
    lambda body: _results((0, 0.9), (1, 0.4), (2, 0.1), (2, 0.1)),      # 多一条
    lambda body: _results((0, 0.9), (1, 0.4), (1, 0.1)),                # index 重复
    lambda body: _results((0, 0.9), (1, 0.4), (3, 0.1)),                # index 越界
    lambda body: _raw('{"results": [{"index": 0, "relevance_score": NaN}, {"index": 1, "relevance_score": 0.4},'
                      ' {"index": 2, "relevance_score": 0.1}]}'),        # 分数不是有限数
    lambda body: _raw('{"results": [{"index": 0, "relevance_score": null}, {"index": 1, "relevance_score": 0.4},'
                      ' {"index": 2, "relevance_score": 0.1}]}'),        # 分数不是数
    lambda body: _raw('{"code": 20012, "message": "Model does not exist"}'),   # 没有 results
    lambda body: _raw('{"results": [{"index": 0, "relev'),                     # 200 但 JSON 不完整
    lambda body: httpx.Response(200, text="<html>gateway</html>"),             # 200 但不是 JSON
    lambda body: httpx.Response(400, json={"code": 20015, "message": "bad request"}),   # 请求被拒绝
])
def test_bad_responses_are_rerank_errors_without_private_text(respond):
    """条数不对、index 没有恰好覆盖每段文本、分数不是有限数、响应不是预期的 JSON、请求被拒绝，都报 rerank_error；
    消息里没有查询原文、文本原文和密钥。"""
    reranker, _ = _reranker(respond)
    with pytest.raises(RerankError) as caught:
        reranker.rerank(QUERY, DOCUMENTS)
    assert caught.value.error_type == "rerank_error"
    message = str(caught.value)
    assert QUERY not in message and SECRET not in message and not any(text in message for text in DOCUMENTS)


def _server_error() -> httpx.Response:
    """服务端错误；retry-after-ms 让 SDK 只等 1 毫秒就重试，测试不用真的等退避时间。"""
    return httpx.Response(500, json={"message": "internal error"}, headers={"retry-after-ms": "1"})


def test_a_temporary_server_error_is_retried_and_then_succeeds():
    """输入：第一次请求返回 500，第二次正常；输出：自动重试，调用方拿到分数，共发了 2 次请求。"""
    responses = iter([_server_error(), _results((0, 0.4), (1, 0.1), (2, 0.9))])
    reranker, requests = _reranker(lambda body: next(responses))
    assert reranker.rerank(QUERY, DOCUMENTS) == [0.4, 0.1, 0.9]
    assert len(requests) == 2


def test_a_server_that_keeps_failing_is_a_rerank_error_after_two_retries():
    """输入：每次请求都返回 500；输出：重试 2 次后报 rerank_error，共发了 3 次请求。"""
    reranker, requests = _reranker(lambda body: _server_error())
    with pytest.raises(RerankError):
        reranker.rerank(QUERY, DOCUMENTS)
    assert len(requests) == 3


def test_a_timeout_is_a_rerank_error():
    """输入：请求超时；输出：rerank_error，底层原因保留在异常链里。重试的退避时间是真实的，约 1.5 秒。"""
    def timeout(body):
        raise httpx.ReadTimeout("timed out")
    reranker, _ = _reranker(timeout)
    with pytest.raises(RerankError) as caught:
        reranker.rerank(QUERY, DOCUMENTS)
    assert isinstance(caught.value.__cause__, openai.APITimeoutError)
