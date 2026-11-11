"""走硅基流动 /rerank 接口的 Reranker（Qwen3-Reranker）。"""

from math import isfinite
from typing import TYPE_CHECKING

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from querypilot.knowledge.api import QUERY_TASK

from ._rerank import RerankError

if TYPE_CHECKING:
    import httpx


class RerankSettings(BaseSettings):
    """Reranker 服务配置，与 Embedding 和对话模型分开：QUERYPILOT_RERANK_BASE_URL / MODEL / API_KEY。

    base_url 停在 /v1，/rerank 由客户端拼上。
    """

    model_config = SettingsConfigDict(env_prefix="QUERYPILOT_RERANK_", env_file=".env", extra="ignore",
                                      hide_input_in_errors=True)
    base_url: str
    model: str
    api_key: SecretStr


class SiliconFlowReranker:
    """Profile 为 siliconflow:<模型>:content-v1:task-instruct-v1。

    content-v1：查询和候选文本都原样发送；task-instruct-v1：请求的 instruction 是 Embedding 查询用的
    同一句任务指令（QUERY_TASK）。
    """

    def __init__(self, settings: RerankSettings, *, http_client: "httpx.Client | None" = None):
        # OpenAI 的 SDK 没有封装重排接口，这里用它的通用 post，鉴权、超时和重试都沿用 SDK。
        # 用到时才加载 openai，不开重排时不依赖模型 SDK。
        from openai import OpenAI
        self._model = settings.model
        self._client = OpenAI(base_url=settings.base_url, api_key=settings.api_key.get_secret_value(),
                              timeout=60, http_client=http_client)
        self.profile_id = f"siliconflow:{settings.model}:content-v1:task-instruct-v1"

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        """给每段文本打相关性分数，按输入顺序返回。

        请求失败（SDK 重试用尽后）、返回的条数与文本数不同、index 没有恰好覆盖每段文本一次、
        分数不是有限数，都抛 RerankError，原因放在异常链里。
        """
        from openai import OpenAIError
        try:
            payload = self._client.post("/rerank", cast_to=object, body={
                "model": self._model, "query": query, "documents": documents, "instruction": QUERY_TASK,
                "return_documents": False})
            results = payload["results"]
            scores = {item["index"]: float(item["relevance_score"]) for item in results}
        except (OpenAIError, KeyError, TypeError, ValueError) as exc:
            raise RerankError() from exc
        # 服务按分数从高到低返回，用 index 对回输入顺序；条数和 index 两项一起查才能发现重复的 index。
        if (len(results) != len(documents) or set(scores) != set(range(len(documents)))
                or not all(isfinite(score) for score in scores.values())):
            raise RerankError()
        return [scores[index] for index in range(len(documents))]
