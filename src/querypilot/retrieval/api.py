"""检索的公开入口：retrieve_knowledge。"""

from hashlib import sha256
import time
from uuid import uuid4

from querypilot.knowledge.api import (
    Embedder, EmbeddingError, EmbeddingSettings, KnowledgeReadError, KnowledgeStorageSettings,
    OpenAIEmbedder, open_read_scope,
)

from . import _search, _text, _fusion
from .models import (
    KnowledgeRetrievalError,
    RetrievalHit,
    RetrievalRequest,
    RetrievalResult,
    ScopeFile,
)


def retrieve_knowledge(request: RetrievalRequest, settings: KnowledgeStorageSettings,
                       embedder: Embedder | None = None) -> RetrievalResult:
    """在 request.scope 指定的 Corpus 中按 request.method 做一次检索。

    lexical 为 BM25，不使用 embedder；vector 先确认当前 Embedding Profile 的向量存在，
    再把查询嵌入一次，按余弦距离做精确全表扫描；hybrid 让 BM25 和向量各取 50 个候选，
    用 RRF 融合后取前 top_k 个，BM25 没有命中时结果就是向量那一路的排序。

    参数：
        request：查询原文、Knowledge Scope、检索方法、top_k 和知识类型。
        settings：知识存储的读取配置（使用只读账号）。
        embedder：vector 和 hybrid 使用的 Embedder，决定 Embedding Profile。没传时按 QUERYPILOT_EMBEDDING_*
            配置创建 OpenAIEmbedder；lexical 不创建也不调用。批量调用时应由调用方创建一个并传入，
            复用同一个客户端。
    返回：
        RetrievalResult，写明实际方法和 Embedding Profile（lexical 时为 None）。有 Hit 时
        outcome 为 matched，hits 按 rank 从 1 连续排列；否则为 no_match。
    异常：
        KnowledgeRetrievalError：lexical 或 hybrid 查询没有有效词时为 input_error（hybrid 不调用
        Embedding 服务）；当前 Profile 没有向量时
        为 embedding_missing；嵌入查询失败时为 embedding_error，不退回 BM25；范围不存在或不匹配、
        超出资源上限、连接或权限等读取失败时，沿用读取层的错误类别。
    副作用：
        只读访问知识库；vector 和 hybrid 调用一次 Embedding 服务；不记录查询原文。
    """
    started = time.perf_counter()
    try:
        terms = _text.query_terms(request.query) if request.method == "lexical" or request.method == "hybrid" else None
        if request.method != "lexical" and embedder is None:
            embedder = OpenAIEmbedder(EmbeddingSettings())
        with open_read_scope(request.scope, settings) as session:
            if request.method == "lexical":
                rows = _search.search_chunks(session, request, terms)
            elif request.method == "vector":
                if not _search.has_vectors(session, embedder.profile_id):
                    raise KnowledgeRetrievalError("embedding_missing", "当前 Embedding Profile 在该 Scope 下没有向量")
                try:
                    query_vector = embedder.embed_query(request.query)
                except EmbeddingError as exc:
                    raise KnowledgeRetrievalError(exc.error_type, str(exc)) from exc
                rows = _search.vector_chunks(session, request, embedder.profile_id, query_vector)
            elif request.method == "hybrid":
                candidates = request.model_copy(update={"top_k": _fusion.CANDIDATES_PER_BRANCH})
                lexical_rows = _search.search_chunks(session, candidates, terms)
                if not _search.has_vectors(session, embedder.profile_id):
                    raise KnowledgeRetrievalError("embedding_missing", "当前 Embedding Profile 在该 Scope 下没有向量")
                try:
                    query_vector = embedder.embed_query(candidates.query)
                except EmbeddingError as exc:
                    raise KnowledgeRetrievalError(exc.error_type, str(exc)) from exc
                vector_rows = _search.vector_chunks(session, candidates, embedder.profile_id, query_vector)
                rows = _fusion.reciprocal_rank_fusion([lexical_rows, vector_rows])[:request.top_k]
        hits = [RetrievalHit(rank=i, **row) for i, row in enumerate(rows, start=1)]
    except KnowledgeReadError as exc:
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
        embedding_profile_id=embedder.profile_id if request.method == "vector" or request.method == "hybrid" else None,
    )


__all__ = [
    "retrieve_knowledge",
    "RetrievalRequest",
    "RetrievalResult",
    "RetrievalHit",
    "KnowledgeRetrievalError",
    "ScopeFile",
]
