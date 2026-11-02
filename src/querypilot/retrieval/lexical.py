"""单次BM25匹配与排序、命中原字段读取；完整来源由后续服务组装。"""

from querypilot.knowledge.api import ReadSession
from querypilot.retrieval.models import KnowledgeRetrievalError, RetrievalRequest


def _search_chunks(
    session: ReadSession,
    request: RetrievalRequest,
    terms: list[str],
) -> list[dict]:
    """在当前只读会话内搜索并返回前 K 条知识块身份与得分。

    参数：session 为已核验范围的同一只读会话；request 为严格请求；
        terms 为 _prepare_query_terms 已生成的非空、规范化、去重词列表。
    返回：字典列表，每条包含 chunk_id、chunk_key、knowledge_type、score；
        有效词全部无匹配时返回 []，尚不组装正文和来源。
    异常：范围不一致抛 KnowledgeRetrievalError(scope_mismatch)；读取预算、
        关闭状态和数据库错误沿已有只读会话及其外层边界处理。
    副作用：只在 session 内执行一条绑定参数的搜索 SQL，不创建索引。
    """
    if request.scope != session.scope:
        raise KnowledgeRetrievalError("scope_mismatch")

    bound_parameters = {
        "corpus_id": request.scope.corpus_id,
        "database_id": request.scope.database_id,
        "schema_version": request.scope.schema_version,
        "knowledge_types": request.knowledge_types,
        "terms": terms,
        "top_k": request.top_k,
    }

    # 整组 OR 受范围和类型条件约束；请求值仅通过参数绑定进入固定查询。
    source_sql = """
        SELECT c.chunk_id, c.chunk_key, c.knowledge_type, pdb.score(c.chunk_id) AS score
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
    return session.fetch_rows(source_sql, bound_parameters)


def _load_hit_rows(session: ReadSession, chunk_ids: list[str]) -> list[dict]:
    """按已命中的ID读取原字段与基础来源。

    参数：session 为原搜索的同一受限只读会话；chunk_ids 为该次搜索命中的
        规范UUID文本列表。空列表不执行读取。
    返回：原块字段、三元范围及source_ref字典列表；当前小段的source_ref先含
        document_id/document_key/source_kind，随后在同一SQL补齐实际来源项。
        ID投影为文本，不带原metadata、父正文或向量，不承诺数据库行次序。
    异常：读取错误沿原会话边界处理；本函数尚不核对请求ID是否全部找到。
    副作用：空列表无；其他输入只在session内读取，不重新搜索或计算分数。
    """
    if not chunk_ids:
        return []

    bound_parameters = {
        "corpus_id": session.scope.corpus_id,
        "database_id": session.scope.database_id,
        "schema_version": session.scope.schema_version,
        "chunk_ids": chunk_ids,
    }

    # 当前SQL仍只返回基础来源；下一结对段在原jsonb_build_object中扩充：
    # 公共structure_locator；表/关系的semantic_origin与精简semantic_profile；
    # 列的review_status/实际column_source_path/correction_ids；关系的精简provenance_ref。
    # 只选允许字段并保留JSON类型，嵌套容器非法时须显式失败，不能当作来源缺失。
    # 完整来源、预算和身份对应尚未完成；下面的基础读取暂不接正式结果。
    source_sql = """
        SELECT
            c.chunk_id::text AS chunk_id,
            c.chunk_key,
            c.corpus_id,
            c.database_id,
            c.schema_version,
            c.knowledge_type,
            c.title,
            c.content,
            c.content_hash,
            c.document_id::text AS document_id,
            c.document_key,
            c.schema_name,
            c.table_name,
            c.column_name,
            c.relation_id::text AS relation_id,
            c.relation_key,
            jsonb_build_object(
                'document_id', d.document_id::text,
                'document_key', d.document_key,
                'source_kind', d.source_kind
            ) AS source_ref
        FROM querypilot_kb.knowledge_chunks AS c
        INNER JOIN querypilot_kb.knowledge_documents AS d
          ON d.document_id = c.document_id
         AND d.document_key = c.document_key
         AND d.corpus_id = c.corpus_id
         AND d.database_id = c.database_id
         AND d.schema_version = c.schema_version
        WHERE c.corpus_id = %(corpus_id)s
          AND c.database_id = %(database_id)s
          AND c.schema_version = %(schema_version)s
          AND c.chunk_id = ANY(%(chunk_ids)s::uuid[])
    """
    return session.fetch_rows(source_sql, bound_parameters)
