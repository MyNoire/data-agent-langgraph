"""检索的 SQL：BM25 搜索和向量搜索。

可检索字段必须与知识存储里 BM25 索引的定义一致；两边是否一致由真实 PG 集成测试保证。
"""

from querypilot.knowledge.api import ReadSession

from .models import RetrievalRequest

SEARCH_SQL = """
    SELECT
        c.chunk_id::text AS chunk_id,
        c.chunk_key,
        c.knowledge_type,
        c.title,
        c.content,
        c.content_hash,
        c.schema_name,
        c.table_name,
        c.column_name,
        pdb.score(c.chunk_id) AS score
    FROM querypilot_kb.knowledge_chunks AS c
    WHERE c.corpus_id = %(corpus_id)s
        AND c.database_id = %(database_id)s
        AND c.schema_version = %(schema_version)s
        AND c.knowledge_type = ANY(%(knowledge_types)s::text[])
        AND (
            c.title ||| %(terms)s::text[]
            OR c.content ||| %(terms)s::text[]
            OR c.schema_name ||| %(terms)s::text[]
            OR c.table_name ||| %(terms)s::text[]
            OR c.column_name ||| %(terms)s::text[]
            OR (
                CASE
                    WHEN jsonb_typeof(c.metadata->'display_name') = 'string'
                    THEN c.metadata->>'display_name'
                END
            ) ||| %(terms)s::text[]
        )
    ORDER BY score DESC,
            c.chunk_key COLLATE "C" ASC,
            c.chunk_id ASC
    LIMIT %(top_k)s
"""


def search_chunks(session: ReadSession, request: RetrievalRequest, terms: list[str]) -> list[dict]:
    """在 request 的 Knowledge Scope 和知识类型内做 BM25 搜索。

    参数：
        session：用 request.scope 打开的只读会话。
        request：检索请求，提供范围、知识类型和 top_k。
        terms：已准备好的查询词；任意一个词命中即为候选（OR）。
    返回：
        最多 top_k 行，已按 分数降序 → chunk_key → chunk_id 排好序。每行的键与
        RetrievalHit 除 rank 外的 10 个字段同名：chunk_id、chunk_key、knowledge_type、
        title、content、content_hash、schema_name、table_name、column_name、score。
        没有命中时返回 []。
    """
    scope = request.scope
    return session.fetch_rows(SEARCH_SQL, {
        "corpus_id": scope.corpus_id,
        "database_id": scope.database_id,
        "schema_version": scope.schema_version,
        "knowledge_types": request.knowledge_types,
        "terms": terms,
        "top_k": request.top_k,
    })


HAS_VECTORS_SQL = """
    SELECT EXISTS (
        SELECT 1
        FROM querypilot_kb.chunk_embeddings AS e
        JOIN querypilot_kb.knowledge_chunks AS c USING (chunk_id)
        WHERE c.corpus_id = %(corpus_id)s
            AND c.database_id = %(database_id)s
            AND c.schema_version = %(schema_version)s
            AND e.embedding_profile_id = %(profile_id)s
    ) AS present
"""

# 精确全表扫描，不用 ANN 索引。<=> 是 pgvector 的余弦距离，score = 1 - 距离 = 余弦相似度。
VECTOR_SQL = """
    SELECT
        c.chunk_id::text AS chunk_id,
        c.chunk_key,
        c.knowledge_type,
        c.title,
        c.content,
        c.content_hash,
        c.schema_name,
        c.table_name,
        c.column_name,
        1 - (e.embedding <=> %(query_vector)s::public.vector) AS score
    FROM querypilot_kb.knowledge_chunks AS c
    JOIN querypilot_kb.chunk_embeddings AS e USING (chunk_id)
    WHERE c.corpus_id = %(corpus_id)s
        AND c.database_id = %(database_id)s
        AND c.schema_version = %(schema_version)s
        AND c.knowledge_type = ANY(%(knowledge_types)s::text[])
        AND e.embedding_profile_id = %(profile_id)s
    ORDER BY e.embedding <=> %(query_vector)s::public.vector ASC,
            c.chunk_key COLLATE "C" ASC,
            c.chunk_id ASC
    LIMIT %(top_k)s
"""


def has_vectors(session: ReadSession, profile_id: str) -> bool:
    """当前 Knowledge Scope 在 profile_id 下是否已有向量（生成向量是原子的，有一条就是全有）。"""
    scope = session.scope
    row = session.fetch_one(HAS_VECTORS_SQL, {
        "corpus_id": scope.corpus_id,
        "database_id": scope.database_id,
        "schema_version": scope.schema_version,
        "profile_id": profile_id,
    })
    return row["present"]


def vector_chunks(session: ReadSession, request: RetrievalRequest, profile_id: str,
                  query_vector: list[float]) -> list[dict]:
    """在 request 的 Knowledge Scope、知识类型和 profile_id 的向量里，按余弦距离找最近的 top_k 个 Chunk。

    参数：
        session：用 request.scope 打开的只读会话。
        request：检索请求，提供范围、知识类型和 top_k。
        profile_id：查询向量所属的 Embedding Profile，只和同一 Profile 的向量比较。
        query_vector：查询的向量，维度必须与该 Profile 的向量相同。
    返回：
        最多 top_k 行，按 距离升序 → chunk_key → chunk_id 排好序；键与 search_chunks 相同，
        score 为余弦相似度。
    """
    scope = request.scope
    return session.fetch_rows(VECTOR_SQL, {
        "corpus_id": scope.corpus_id,
        "database_id": scope.database_id,
        "schema_version": scope.schema_version,
        "knowledge_types": request.knowledge_types,
        "profile_id": profile_id,
        "query_vector": "[" + ",".join(repr(float(value)) for value in query_vector) + "]",
        "top_k": request.top_k,
    })
