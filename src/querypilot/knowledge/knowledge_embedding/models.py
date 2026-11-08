"""Embedder 接口与 Embedding 调用失败的错误类型。"""

from typing import Protocol


class Embedder(Protocol):
    """把文本变成向量。同一个 Embedder 算出的向量属于同一个 Embedding Profile。

    profile_id：这批向量的身份，和向量一起存进向量表。
    embed_documents：按输入顺序返回每段 Chunk 文本的向量，条数与输入相同。
    embed_query：返回一条查询的向量，查询的取法（如任务指令）由实现决定。
    """

    profile_id: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class EmbeddingError(RuntimeError):
    """Embedding 调用失败或返回的条数、维度不对。消息是固定的安全文本，底层原因放在异常链里。"""

    error_type = "embedding_error"
    exit_code = 3

    def __init__(self, message: str = "Embedding 服务调用失败或返回结果不符合预期"):
        super().__init__(message)
