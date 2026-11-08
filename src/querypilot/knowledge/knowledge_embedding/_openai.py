"""走 OpenAI 兼容 /embeddings 接口的 Embedder（硅基流动 Qwen3-Embedding）。"""

from typing import TYPE_CHECKING

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import EmbeddingError

if TYPE_CHECKING:
    import httpx

DIMENSIONS = 4096
BATCH_SIZE = 32
# Qwen3-Embedding 的非对称用法：查询前加任务指令，文档原样嵌入。指令只描述任务，不参考任何题目。
QUERY_TASK = ("Given a question about data in a relational database, retrieve the descriptions of the "
              "tables, columns and relationships needed to write a SQL query that answers it")


class EmbeddingSettings(BaseSettings):
    """Embedding 服务配置，与对话模型分开：QUERYPILOT_EMBEDDING_BASE_URL / MODEL / API_KEY。"""

    model_config = SettingsConfigDict(env_prefix="QUERYPILOT_EMBEDDING_", env_file=".env", extra="ignore",
                                      hide_input_in_errors=True)
    base_url: str
    model: str
    api_key: SecretStr


class OpenAIEmbedder:
    """Profile 为 siliconflow:<模型>:4096:content-v1:query-instruct-v1。

    content-v1：Chunk 的 content 原样嵌入；query-instruct-v1：查询前拼上 QUERY_TASK 指令。
    返回的条数或维度不对、请求失败，都抛 EmbeddingError，原因放在异常链里。
    """

    def __init__(self, settings: EmbeddingSettings, *, http_client: "httpx.Client | None" = None):
        # 用到时才加载 openai，只做 BM25 检索时不依赖模型 SDK。
        from openai import OpenAI
        self._model = settings.model
        self._client = OpenAI(base_url=settings.base_url, api_key=settings.api_key.get_secret_value(),
                              timeout=60, http_client=http_client)
        self.profile_id = f"siliconflow:{settings.model}:{DIMENSIONS}:content-v1:query-instruct-v1"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [vector for start in range(0, len(texts), BATCH_SIZE)
                for vector in self._embed(texts[start:start + BATCH_SIZE])]

    def embed_query(self, text: str) -> list[float]:
        return self._embed([f"Instruct: {QUERY_TASK}\nQuery:{text}"])[0]

    def _embed(self, texts: list[str]) -> list[list[float]]:
        from openai import OpenAIError
        try:
            response = self._client.embeddings.create(model=self._model, input=texts, encoding_format="float")
        except OpenAIError as exc:
            raise EmbeddingError() from exc
        vectors = [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
        if len(vectors) != len(texts) or any(len(vector) != DIMENSIONS for vector in vectors):
            raise EmbeddingError()
        return vectors
