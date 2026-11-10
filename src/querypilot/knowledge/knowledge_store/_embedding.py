"""为一个 Knowledge Scope 生成向量：读 Chunk、调用 Embedder、一个事务写入全部向量。"""

import psycopg

from querypilot.knowledge.knowledge_embedding.api import Embedder, EmbeddingError
from . import _postgres as postgres
from ._read_session import open_read_scope
from .models import EmbeddingSummary, KnowledgeScope
from .settings import KnowledgeStorageSettings

_SCOPE_VECTORS = """
    SELECT count(*) AS n, max(e.embedding_dimensions) AS dims
    FROM querypilot_kb.chunk_embeddings e JOIN querypilot_kb.knowledge_chunks c USING (chunk_id)
    WHERE c.corpus_id = %s AND e.embedding_profile_id = %s
"""
_INSERT = ("INSERT INTO querypilot_kb.chunk_embeddings(chunk_id, embedding_profile_id, embedding_dimensions, embedding) "
           "VALUES (%s, %s, %s, %s::public.vector)")


def embed_scope(scope: KnowledgeScope, settings: KnowledgeStorageSettings, embedder: Embedder) -> EmbeddingSummary:
    """见 knowledge_store.api.embed_corpus。"""
    profile = embedder.profile_id
    with open_read_scope(scope, settings) as session:
        existing = session.fetch_one(_SCOPE_VECTORS, (scope.corpus_id, profile))
        if existing["n"]:
            return _summary(scope, profile, existing["dims"], existing["n"], "exists")
        chunks = session.fetch_rows("SELECT chunk_id, content FROM querypilot_kb.knowledge_chunks "
                                    "WHERE corpus_id = %s ORDER BY chunk_key", (scope.corpus_id,))
    vectors = embedder.embed_documents([chunk["content"] for chunk in chunks])
    dims = len(vectors[0]) if vectors else 0
    # 条数或维度不一致时一行都不写，避免一份 Corpus 只有部分向量。
    if len(vectors) != len(chunks) or not dims or any(len(vector) != dims for vector in vectors):
        raise EmbeddingError()
    rows = [(chunk["chunk_id"], profile, dims, _literal(vector)) for chunk, vector in zip(chunks, vectors)]
    with postgres.connection(settings, "import") as conn:
        try:
            with conn.transaction():
                postgres.transaction_limits(conn, settings)
                with conn.cursor() as cursor:
                    cursor.executemany(_INSERT, rows)
        except psycopg.errors.UniqueViolation:
            # 读完 Chunk 之后，另一个进程已为同一 Profile 写完向量；主键保证不会重复，本次整体回滚。
            return _summary(scope, profile, dims, len(chunks), "exists")
    return _summary(scope, profile, dims, len(chunks), "embedded")


def _literal(vector: list[float]) -> str:
    """pgvector 的文本格式，例如 [0.1,0.2,0.3]。"""
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


def _summary(scope: KnowledgeScope, profile: str, dims: int, count: int, outcome: str) -> EmbeddingSummary:
    return EmbeddingSummary(outcome=outcome, corpus_id=scope.corpus_id, database_id=scope.database_id,
                            schema_version=scope.schema_version, embedding_profile_id=profile,
                            dimensions=dims, embedded_chunks=count)
