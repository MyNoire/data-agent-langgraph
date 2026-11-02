"""检索的公开入口：retrieve_knowledge。"""

from hashlib import sha256
import time
from uuid import uuid4

from querypilot.knowledge.api import KnowledgeReadError, KnowledgeStorageSettings, open_read_scope

from . import _search, _text
from .models import (
    KnowledgeRetrievalError,
    LexicalProfile,
    RetrievalHit,
    RetrievalRequest,
    RetrievalResult,
    ScopeFile,
)


def retrieve_knowledge(request: RetrievalRequest, settings: KnowledgeStorageSettings) -> RetrievalResult:
    """在 request.scope 指定的 Corpus 中做一次 BM25 检索。

    参数：
        request：查询原文、Knowledge Scope、top_k 和知识类型。
        settings：知识存储的读取配置（使用只读账号）。
    返回：
        RetrievalResult。有 Hit 时 outcome 为 matched，hits 按 rank 从 1 连续排列；
        否则为 no_match。
    异常：
        KnowledgeRetrievalError：查询没有有效词时为 input_error；范围不存在或不匹配、
        超出资源上限、连接或权限等读取失败时，沿用读取层的错误类别。
    副作用：
        只读访问知识库；不记录查询原文。
    """
    started = time.perf_counter()
    try:
        with open_read_scope(request.scope, settings) as session:
            # ===== 结对核心：从这里开始 =====
            # 依次完成：准备查询词 → 搜索 → 按 ID 读回 → 按搜索顺序组装 Hit。
            # 可用的零件：_text.prepare_query、_text._prepare_query_terms、
            #            _search.search_chunks、_search.load_chunks、RetrievalHit。
            raise NotImplementedError("retrieve_knowledge 的主流程待结对完成")
            # ===== 结对核心：到这里结束 =====
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
    )


__all__ = [
    "retrieve_knowledge",
    "RetrievalRequest",
    "RetrievalResult",
    "RetrievalHit",
    "KnowledgeRetrievalError",
    "LexicalProfile",
    "ScopeFile",
]
