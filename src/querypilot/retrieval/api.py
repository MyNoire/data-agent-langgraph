"""检索的公开入口：retrieve_knowledge。"""

from hashlib import sha256
import time
from uuid import uuid4

from querypilot.knowledge.api import (
    Embedder, EmbeddingError, EmbeddingSettings, KnowledgeReadError, KnowledgeStorageSettings,
    OpenAIEmbedder, ReadSession, open_read_scope,
)

from . import _fusion, _rerank, _search, _text
from ._rerank import RerankError, Reranker
from .models import (
    KnowledgeRetrievalError,
    RetrievalHit,
    RetrievalMethod,
    RetrievalRequest,
    RetrievalResult,
    ScopeFile,
)


def _vector_rows(session: ReadSession, request: RetrievalRequest, embedder: Embedder, limit: int) -> list[dict]:
    """向量这一路：确认当前 Profile 有向量，把查询嵌入一次，按余弦距离取前 limit 行。

    没有向量时报 embedding_missing，不调用 Embedding 服务；嵌入失败时报 embedding_error。
    """
    if not _search.has_vectors(session, embedder.profile_id):
        raise KnowledgeRetrievalError("embedding_missing", "当前 Embedding Profile 在该 Scope 下没有向量")
    try:
        query_vector = embedder.embed_query(request.query)
    except EmbeddingError as exc:
        raise KnowledgeRetrievalError(exc.error_type, str(exc)) from exc
    return _search.vector_chunks(session, request, embedder.profile_id, query_vector, limit)


def retrieve_knowledge(request: RetrievalRequest, settings: KnowledgeStorageSettings,
                       embedder: Embedder | None = None, reranker: Reranker | None = None) -> RetrievalResult:
    """在 request.scope 指定的 Corpus 中按 request.method 做一次检索，request.rerank 为真时再重排。

    lexical 为 BM25，不使用 embedder；vector 先确认当前 Embedding Profile 的向量存在，
    再把查询嵌入一次，按余弦距离做精确全表扫描；hybrid 让 BM25 和向量各取 50 个候选，
    用 RRF 融合后取前 top_k 个，BM25 没有命中时结果就是向量那一路的排序。

    开启重排时，先按上面的方法取前 20 个候选（与 top_k 无关），关闭只读会话后把查询原文和各候选的
    content 交给 reranker 打分，按相关性分数降序排列（同分保持原来的先后），再取前 top_k 个。
    没有候选时不调用 reranker。

    参数：
        request：查询原文、Knowledge Scope、检索方法、是否重排、top_k 和知识类型。
        settings：知识存储的读取配置（使用只读账号）。
        embedder：vector 和 hybrid 使用的 Embedder，决定 Embedding Profile。没传时按
            QUERYPILOT_EMBEDDING_* 配置创建 OpenAIEmbedder；lexical 不创建也不调用。批量调用时
            应由调用方创建一个并传入，复用同一个客户端。
        reranker：request.rerank 为真时使用的 Reranker，决定 Rerank Profile；不开重排时不调用。
    返回：
        RetrievalResult，写明实际方法、Embedding Profile（lexical 时为 None）和 Rerank Profile
        （请求没有开启重排时为 None；开启了但没有候选时仍写明，表示这次请求按哪套配置执行）。有 Hit 时 outcome 为 matched，hits 按 rank 从 1 连续排列；否则为 no_match。
    异常：
        KnowledgeRetrievalError：lexical 或 hybrid 查询没有有效词时为 input_error（hybrid 不调用
        Embedding 服务）；当前 Profile 没有向量时为 embedding_missing；嵌入查询失败时为
        embedding_error，不退回 BM25；重排失败时为 rerank_error，不退回未重排的结果；范围不存在或
        不匹配、超出资源上限、连接或权限等读取失败时，沿用读取层的错误类别。
    副作用：
        只读访问知识库；vector 和 hybrid 调用一次 Embedding 服务；开启重排且有候选时调用一次
        Reranker；不记录查询原文。
    """
    started = time.perf_counter()
    uses_terms = request.method != "vector"
    uses_vectors = request.method != "lexical"
    try:
        # 先提取查询词：没有有效词时在创建 embedder、打开会话之前就报 input_error。
        terms = _text.query_terms(request.query) if uses_terms else None
        if uses_vectors and embedder is None:
            embedder = OpenAIEmbedder(EmbeddingSettings())
        # 开启重排时先取固定数量的候选，重排后再截到 top_k。
        limit = _rerank.RERANK_CANDIDATES if request.rerank else request.top_k
        with open_read_scope(request.scope, settings) as session:
            if request.method == "lexical":
                rows = _search.search_chunks(session, request, terms, limit)
            elif request.method == "vector":
                rows = _vector_rows(session, request, embedder, limit)
            else:  # hybrid
                rows = _fusion.reciprocal_rank_fusion([
                    _search.search_chunks(session, request, terms, _fusion.CANDIDATES_PER_BRANCH),
                    _vector_rows(session, request, embedder, _fusion.CANDIDATES_PER_BRANCH),
                ])[:limit]
        # 只读会话已经关闭：候选行都取出来了，不让数据库事务等 Reranker 服务。
        if request.rerank and rows:
            rows = _rerank.rerank_rows(request.query, rows, reranker)[:request.top_k]
        hits = [RetrievalHit(rank=i, **row) for i, row in enumerate(rows, start=1)]
    except (KnowledgeReadError, RerankError) as exc:
        raise KnowledgeRetrievalError(exc.error_type, str(exc)) from exc

    return RetrievalResult(
        request_id=str(uuid4()),
        outcome="matched" if hits else "no_match",
        scope=request.scope,
        query_sha256=sha256(request.query.encode("utf-8")).hexdigest(),
        top_k=request.top_k,
        knowledge_types=request.knowledge_types,
        hit_count=len(hits),
        hits=hits,
        elapsed_ms=(time.perf_counter() - started) * 1000,
        method=request.method,
        embedding_profile_id=embedder.profile_id if uses_vectors else None,
        rerank_profile_id=reranker.profile_id if request.rerank else None,
    )


__all__ = [
    "retrieve_knowledge",
    "RetrievalRequest",
    "RetrievalMethod",
    "RetrievalResult",
    "RetrievalHit",
    "KnowledgeRetrievalError",
    "Reranker",
    "RerankError",
    "ScopeFile",
]
