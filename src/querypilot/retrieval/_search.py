"""检索的 BM25 搜索 SQL。

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
