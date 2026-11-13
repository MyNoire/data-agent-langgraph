"""重排：把检索到的候选交给 Reranker 打分，按相关性分数重新排序。"""

from typing import Protocol

# 重排这一步的版本，拼在检索方法的配置版本后面（如 r4-hybrid-v1+r4b-rerank-v1）。它描述检索里怎么用
# Reranker，Reranker 自己的选择（模型、文本、指令）由 Rerank Profile 描述。
# r4b-rerank-v1：取检索方法的前 20 个候选，按 Rerank Profile 打分，按分数降序排列，同分保持重排前的顺序。
RERANK_STEP_VERSION = "r4b-rerank-v1"
# 开启重排时从检索方法取的候选数，固定不调，与 top_k 无关：任何 top_k 的前 N 个都与更大 top_k
# 结果的前 N 个相同，一次重排的费用和延迟也有上限。
RERANK_CANDIDATES = 20


class Reranker(Protocol):
    """给"查询 + 候选文本"打相关性分数。同一个 Reranker 的打分方式属于同一个 Rerank Profile。

    profile_id：Rerank Profile 的身份（模型、候选取哪段文本、带什么指令），写进检索结果。
    rerank：按输入顺序返回每段文本的相关性分数，条数与输入相同，分数越高越相关。
    """

    profile_id: str

    def rerank(self, query: str, documents: list[str]) -> list[float]: ...


class RerankError(RuntimeError):
    """Reranker 调用失败或返回结果不符合预期。消息是固定的安全文本，底层原因放在异常链里。"""

    error_type = "rerank_error"

    def __init__(self, message: str = "Reranker 服务调用失败或返回结果不符合预期"):
        super().__init__(message)


def rerank_rows(query: str, rows: list[dict], reranker: Reranker) -> list[dict]:
    """用 reranker 给 rows 打分并重新排序。

    参数：
        query：查询原文。
        rows：按检索名次排好的候选行；每行的 content 原样交给 reranker。
        reranker：打分用的 Reranker。
    返回：
        同一批行，score 换成相关性分数，按分数降序排列；同分的行保持传入时的先后顺序。
    """
    scores = reranker.rerank(query, [row["content"] for row in rows])
    # strict：分数条数和候选对不上说明 Reranker 的实现违反了接口约定，直接报错；不加的话 zip 会悄悄丢掉候选。
    scored = [{**row, "score": score} for row, score in zip(rows, scores, strict=True)]
    return sorted(scored, key=lambda row: -row["score"])
