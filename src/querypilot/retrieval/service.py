"""R3 读取会话与单条命中组装；完整服务尚未接线。"""

from contextlib import contextmanager
from typing import Iterator

from pydantic import ValidationError

from querypilot.knowledge.api import (KnowledgeReadError, KnowledgeScope, KnowledgeStorageSettings,
                                    ReadLimits, ReadSession, open_read_scope)
from querypilot.retrieval.models import (
    KnowledgeRetrievalError, LexicalProfile, RetrievalHit, SourceRef,
)

PROFILE = LexicalProfile()


@contextmanager
def open_scoped_read(scope: KnowledgeScope, settings: KnowledgeStorageSettings) -> Iterator[ReadSession]:
    """输入：严格范围/配置；返回：公开读会话；错误映射原检索类别并保留cause。

    副作用：只持有同一只读事务；不创建检索作业，不实现后续匹配策略。
    """
    limits = ReadLimits(max_scope_chunks=PROFILE.max_scope_chunks,
                        max_search_bytes=PROFILE.max_search_bytes,
                        max_result_bytes=PROFILE.max_result_bytes)
    try:
        with open_read_scope(scope, settings, limits) as session:
            yield session
    except KnowledgeReadError as exc:
        raise KnowledgeRetrievalError(exc.error_type) from exc


def _build_hit(row: dict, *, rank: int, score: float) -> RetrievalHit:
    """把一条已投影的知识记录组装成命中对象。

    参数：row 含原块字段、corpus_id/database_id/schema_version，以及 SQL
        白名单投影后的 source_ref 字典；UUID 为规范文本，不含原 metadata。
        rank 是搜索次序（从1开始），score 是该块的原插件分数。
    返回：保留原身份、正文、摘要、来源及合法空值的 RetrievalHit。
    异常：缺必需字段或模型校验失败时转换为
        KnowledgeRetrievalError(knowledge_state_error)，并保留原 cause。
    副作用：无；不连接数据库、不修改输入、不重算排名或覆盖原摘要。
    """
    # 保留原正文、摘要及空定位，沿用模型的摘要和父引用校验。
    try:
        scope = KnowledgeScope(corpus_id=row["corpus_id"], database_id=row["database_id"], schema_version=row["schema_version"])
        source_ref = SourceRef.model_validate(row["source_ref"])
        retrieval_hit = RetrievalHit(
            rank=rank,
            score=score,
            chunk_id=row["chunk_id"],
            chunk_key=row["chunk_key"],
            scope=scope,
            knowledge_type=row["knowledge_type"],
            title=row["title"],
            content=row["content"],
            content_hash=row["content_hash"],
            document_id=row["document_id"],
            document_key=row["document_key"],
            schema_name=row.get("schema_name"),
            table_name=row.get("table_name"),
            column_name=row.get("column_name"),
            relation_id=row.get("relation_id"),
            relation_key=row.get("relation_key"),
            source_ref=source_ref,
        )
    except (ValidationError, KeyError) as exc:
        raise KnowledgeRetrievalError("knowledge_state_error") from exc
    return retrieval_hit
