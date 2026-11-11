"""测试用的假 Reranker：不联网，分数来自手写字典。"""

from querypilot.retrieval.api import RerankError


class FakeReranker:
    """从"候选文本 → 分数"字典取分数，没登记的文本用默认分；记录每次收到的查询和候选文本，不联网。"""

    profile_id = "fake-rerank:test-v1"

    def __init__(self, scores: dict[str, float] | None = None, default: float = 0.0):
        self.scores = scores or {}
        self.default = default
        self.calls: list[tuple[str, list[str]]] = []

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.calls.append((query, list(documents)))
        return [self.scores.get(document, self.default) for document in documents]


class ErrorReranker(FakeReranker):
    """重排时抛 RerankError，模拟 Reranker 服务失败。"""

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.calls.append((query, list(documents)))
        raise RerankError()
