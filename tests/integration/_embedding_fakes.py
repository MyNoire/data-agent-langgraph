"""测试用的假 Embedder：不联网，向量来自手写字典。"""


class FakeEmbedder:
    """从"文本 → 向量"字典取向量，没登记的文本用默认向量；记录被调用的次数，不联网。"""

    profile_id = "fake:3:test-v1"

    def __init__(self, vectors: dict[str, list[float]] | None = None, default=(1.0, 0.0, 0.0)):
        self.vectors = vectors or {}
        self.default = list(default)
        self.document_calls = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_calls += 1
        return [self.vectors.get(text, self.default) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.vectors.get(text, self.default)


class ErrorEmbedder(FakeEmbedder):
    """查询嵌入时抛 EmbeddingError，模拟 Embedding 服务失败。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.query_calls = 0

    def embed_query(self, text):
        from querypilot.knowledge.api import EmbeddingError
        self.query_calls += 1
        raise EmbeddingError()
