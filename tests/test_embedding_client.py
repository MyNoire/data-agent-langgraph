"""OpenAIEmbedder 的请求与结果检查；用 httpx.MockTransport 代替网络，不调用真实服务。"""

import json

import httpx
import pytest
from pydantic import SecretStr

from querypilot.knowledge.api import EmbeddingError, EmbeddingSettings, OpenAIEmbedder

SECRET = "sk-private-test-key"


def _settings():
    return EmbeddingSettings(_env_file=None, base_url="https://embedding.test/v1",
                             model="Qwen/Qwen3-Embedding-8B", api_key=SecretStr(SECRET))


def _vector(seed: int, dims: int = 4096) -> list[float]:
    vector = [0.0] * dims
    vector[seed % dims] = 1.0
    return vector


def _embedder(respond):
    """respond(inputs) → (状态码, 返回的 data 列表)；记录每次请求的 input。"""
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        inputs = json.loads(request.content)["input"]
        requests.append(inputs)
        status, data = respond(inputs)
        return httpx.Response(status, json={"object": "list", "model": "m", "data": data,
                                            "usage": {"prompt_tokens": 1, "total_tokens": 1}})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAIEmbedder(_settings(), http_client=client), requests


def _ok(inputs, dims=4096):
    return 200, [{"object": "embedding", "index": i, "embedding": _vector(i, dims)} for i in range(len(inputs))]


def test_profile_names_model_dimensions_and_text_recipes():
    embedder, _ = _embedder(_ok)
    assert embedder.profile_id == "siliconflow:Qwen/Qwen3-Embedding-8B:4096:content-v1:query-instruct-v1"


def test_documents_are_sent_verbatim_and_query_gets_the_instruction():
    """文档原样发送；查询前拼上固定的任务指令；返回 4096 维向量。"""
    embedder, requests = _embedder(_ok)
    documents = embedder.embed_documents(["表 atom：原子", "列 atom.element：元素"])
    query = embedder.embed_query("How many elements are there?")
    assert requests[0] == ["表 atom：原子", "列 atom.element：元素"]
    assert requests[1][0].startswith("Instruct: ") and requests[1][0].endswith("\nQuery:How many elements are there?")
    assert [len(vector) for vector in documents] == [4096, 4096] and len(query) == 4096


def test_documents_are_sent_in_batches_and_keep_their_order():
    embedder, requests = _embedder(_ok)
    texts = [f"chunk {i}" for i in range(70)]
    vectors = embedder.embed_documents(texts)
    assert [len(batch) for batch in requests] == [32, 32, 6]
    assert [vector.index(1.0) for vector in vectors] == [i % 32 for i in range(70)]


@pytest.mark.parametrize("respond", [
    lambda inputs: (200, _ok(inputs)[1][:-1]),          # 少一条
    lambda inputs: _ok(inputs, dims=1024),               # 维度不对
    lambda inputs: (400, []),                            # 请求失败
])
def test_bad_responses_are_embedding_errors_without_private_text(respond):
    """条数、维度不对或请求失败都报 embedding_error；消息里没有查询原文和密钥。"""
    embedder, _ = _embedder(respond)
    with pytest.raises(EmbeddingError) as caught:
        embedder.embed_query("secret question text")
    assert caught.value.error_type == "embedding_error"
    assert "secret question text" not in str(caught.value) and SECRET not in str(caught.value)
