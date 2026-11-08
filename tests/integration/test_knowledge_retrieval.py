"""R3 检索验收：经 retrieve_knowledge 验证 BM25 行为，经知识读取层验证只读会话。

本文件不调用旧 pg_database；所有写入仅限同库本次唯一 corpus。
当前可立即执行的设施组在 test_bm25_search_setup.py。
"""

import json

import pytest
from hashlib import sha256
from math import isfinite
from uuid import uuid4

from querypilot.knowledge.api import KnowledgeReadError, KnowledgeScope, ReadLimits, open_read_scope
from querypilot.retrieval.api import KnowledgeRetrievalError, RetrievalRequest, retrieve_knowledge
from _bm25_fixtures import live_bm25_settings, role_connection, unchanged_live_knowledge
from _embedding_fakes import FakeEmbedder


@pytest.fixture
def retrieval_source(live_bm25_settings, r3_corpus_factory, unchanged_live_knowledge):
    """导入本次唯一原创 corpus；结束按身份精确删除，不改已有知识或权限。"""
    from psycopg import sql
    from querypilot.knowledge.api import import_corpus, load_corpus

    database_id = "r3probe" + uuid4().hex
    path = r3_corpus_factory("r3-source", database_id=database_id)
    source = load_corpus(path)
    scope = KnowledgeScope(corpus_id=source.manifest.corpus_id, database_id=database_id,
                           schema_version=source.manifest.identity_descriptor.schema_versions[database_id])
    with role_connection(live_bm25_settings, "admin") as conn:
        assert not conn.execute("SELECT 1 FROM querypilot_kb.knowledge_corpora WHERE corpus_id=%s",
                                (scope.corpus_id,)).fetchone()
    try:
        import_corpus(path, live_bm25_settings)
        yield live_bm25_settings, source, scope
    finally:
        # 先证明是本次生成且此前不存在的身份，再逐表按完整 scope 和记录 ID 清理。
        with role_connection(live_bm25_settings, "admin") as conn, conn.transaction():
            # 向量表对 Chunk 的外键是 RESTRICT，先删本次 Chunk 的向量。
            conn.execute("DELETE FROM querypilot_kb.chunk_embeddings WHERE chunk_id=ANY(%s::uuid[])",
                         ([str(chunk.chunk_id) for chunk in source.chunks],))
            for table, key, records in (
                ("knowledge_chunks", "chunk_id", source.chunks),
                ("schema_relations", "relation_id", source.relations),
                ("knowledge_documents", "document_id", source.documents),
            ):
                ids = [str(getattr(record, key)) for record in records]
                conn.execute(sql.SQL("DELETE FROM {} WHERE corpus_id=%s AND database_id=%s "
                                     "AND schema_version=%s AND {}=ANY(%s::uuid[])").format(
                    sql.Identifier("querypilot_kb", table), sql.Identifier(key)),
                    (scope.corpus_id, scope.database_id, scope.schema_version, ids))
            conn.execute("DELETE FROM querypilot_kb.knowledge_corpora WHERE corpus_id=%s "
                         "AND database_id=%s AND schema_version=%s",
                         (scope.corpus_id, scope.database_id, scope.schema_version))


def test_scoped_read_uses_reader_and_one_readonly_view(retrieval_source):
    """输入：本次原创 corpus；返回：只读账号、隔离级别和完整数量断言。"""
    settings, source, scope = retrieval_source
    with open_read_scope(scope, settings) as session:
        settings_row = session.fetch_one("""SELECT current_user AS role,
            current_setting('transaction_read_only') AS readonly,
            current_setting('transaction_isolation') AS isolation""")
        assert settings_row == {"role": "querypilot_kb_reader", "readonly": "on", "isolation": "repeatable read"}
        assert session.counts == source.manifest.counts
        assert session.search_bytes > 0
        assert not any(hasattr(session, name) for name in ("connection", "cursor", "commit"))
    with pytest.raises(KnowledgeReadError) as closed:
        session.fetch_one("SELECT 1 AS value")
    assert closed.value.error_type == "knowledge_state_error"


@pytest.mark.parametrize("change,category", [
    ({"corpus_id": "0" * 64}, "scope_not_found"),
    ({"database_id": "another_database"}, "scope_mismatch"),
    ({"schema_version": "0" * 64}, "scope_mismatch"),
])
def test_scoped_read_rejects_unknown_or_mismatched_scope(retrieval_source, change, category):
    """输入：错误范围；返回：独立错误类别断言；异常：预期受控失败；无写入。"""
    settings, _, scope = retrieval_source
    with pytest.raises(KnowledgeRetrievalError) as caught:
        retrieve_knowledge(RetrievalRequest(query="label", scope=KnowledgeScope(**{**scope.model_dump(), **change})),
                           settings)
    assert caught.value.error_type == category


@pytest.mark.parametrize("pollution", ["registration", "missing_chunk"])
def test_scoped_read_refuses_known_partial_state(retrieval_source, pollution):
    """只修改本次唯一 corpus 的登记或列块；已有知识完全不动，结束精确清理。"""
    settings, source, scope = retrieval_source
    with role_connection(settings, "admin") as conn:
        if pollution == "registration":
            conn.execute("UPDATE querypilot_kb.knowledge_corpora SET counts='{}'::jsonb WHERE corpus_id=%s", (scope.corpus_id,))
        else:
            column = next(c for c in source.chunks if c.knowledge_type == "column")
            conn.execute("DELETE FROM querypilot_kb.knowledge_chunks WHERE chunk_id=%s", (column.chunk_id,))
    with pytest.raises(KnowledgeRetrievalError) as caught:
        retrieve_knowledge(RetrievalRequest(query="label", scope=scope), settings)
    assert caught.value.error_type == "knowledge_state_error"


def test_scoped_read_checks_full_scope_capacity(retrieval_source):
    """给读取层一个 1 字节的检索文本预算，打开范围时应报容量超限。"""
    settings, _, scope = retrieval_source
    with pytest.raises(KnowledgeReadError) as caught:
        with open_read_scope(scope, settings, ReadLimits(max_search_bytes=1)):
            pytest.fail("预算超限不能继续")
    assert caught.value.error_type == "capacity_exceeded"


# 下组是尚待实现的完整 BM25 入口验收，不能用设施验证或纯 Python 准备代替。
@pytest.mark.parametrize("query,kind,should_match,expected_column", [
    ("item_id", "column", True, "item_id"), ("item id", "column", True, "item_id"),
    ('"main"."items"."label"', "column", True, "label"),
    ("activation label", "column", True, "label"),
    ("main inactive", "column", True, "label"),
    ("租户内物品", "table", True, None), ("事件关联同租户物品", "relationship", True, None),
    ("main 租户内物品", "table", True, None), ("main 不存在词", "table", True, None),
    # 预设无匹配词不会因宽松 OR 而排除另一有效词，包括两种语言的两个方向。
    ("label qpnomatch9f8e7d6c", "column", True, "label"),
    ("不存在词 物品", "table", True, None),
    ("qpnomatch9f8e7d6c 物品", "table", True, None),
    ("请问帮我查询租户内物品的事件", "table", True, None),
    ("qpnomatch9f8e7d6c", "column", False, None),
])
def test_matching_semantics_in_real_pg(retrieval_source, query, kind, should_match, expected_column):
    """输入：原创 BM25 正反例；返回：OR/排序/原投影断言；核心完成后运行。"""
    settings, source, scope = retrieval_source
    result = retrieve_knowledge(RetrievalRequest(query=query, scope=scope, top_k=5,
                                                knowledge_types=[kind]), settings)
    assert result.outcome == ("matched" if should_match else "no_match")
    assert bool(result.hits) is should_match
    assert result.retrieval_config_version == "r3-bm25-v1"
    assert result.scope == scope and result.hit_count == len(result.hits)
    assert result.query_sha256 == sha256(query.encode("utf-8")).hexdigest()
    assert all(hit.knowledge_type == kind for hit in result.hits)
    originals = {chunk.chunk_id: chunk for chunk in source.chunks}
    for rank, hit in enumerate(result.hits, 1):
        original = originals[hit.chunk_id]
        assert hit.rank == rank
        assert isfinite(hit.score) and hit.score >= 0
        for field in ("chunk_key", "knowledge_type", "title", "content", "content_hash",
                      "schema_name", "table_name", "column_name"):
            assert getattr(hit, field) == getattr(original, field)
    assert [(hit.score, hit.chunk_key, str(hit.chunk_id)) for hit in result.hits] == sorted(
        [(hit.score, hit.chunk_key, str(hit.chunk_id)) for hit in result.hits],
        key=lambda value: (-value[0], value[1].encode("utf-8"), value[2]))
    if should_match:
        if kind == "column":
            assert any(hit.table_name == "items" and hit.column_name == expected_column for hit in result.hits)
        elif kind == "table":
            assert any(hit.table_name == "items" for hit in result.hits)
        else:
            assert all(hit.schema_name is None and hit.table_name is None and hit.column_name is None
                       for hit in result.hits)


@pytest.mark.parametrize("query", ["---", "请问 的 谢谢"])
def test_complete_query_without_effective_terms_is_input_error(retrieval_source, query):
    """完整操作过滤后无词必须报输入错误；不能当 no_match 或返回全库。"""
    settings, _, scope = retrieval_source
    with pytest.raises(KnowledgeRetrievalError) as caught:
        retrieve_knowledge(RetrievalRequest(query=query, scope=scope), settings)
    assert caught.value.error_type == "input_error"


def test_complete_scores_and_order_equal_raw_plugin_bm25(retrieval_source):
    """公开检索的身份、分数及次序须等于同条件插件原分数，拒绝隐藏人工加分。"""
    settings, _, scope = retrieval_source
    request = RetrievalRequest(query="activation label", scope=scope, top_k=5, knowledge_types=["column"])
    result = retrieve_knowledge(request, settings)
    # 固定英文输入的有效词已明确；此处直接调用插件作为公开结果的独立对照。
    statement = """SELECT chunk_id::text AS chunk_id,chunk_key,pdb.score(chunk_id) AS score
        FROM querypilot_kb.knowledge_chunks
        WHERE corpus_id=%(corpus)s AND database_id=%(database)s AND schema_version=%(schema)s
          AND knowledge_type=ANY(%(types)s::text[])
          AND (title ||| %(terms)s::text[] OR content ||| %(terms)s::text[]
            OR schema_name ||| %(terms)s::text[] OR table_name ||| %(terms)s::text[]
            OR column_name ||| %(terms)s::text[]
            OR (CASE WHEN jsonb_typeof(metadata->'display_name')='string'
                THEN metadata->>'display_name' END) ||| %(terms)s::text[])
        ORDER BY score DESC,chunk_key COLLATE "C",chunk_id LIMIT %(k)s"""
    parameters = {"corpus": scope.corpus_id, "database": scope.database_id,
                  "schema": scope.schema_version, "types": request.knowledge_types,
                  "terms": ["activation", "label"], "k": request.top_k}
    with open_read_scope(scope, settings) as session:
        expected = session.fetch_rows(statement, parameters)
    assert expected
    assert [(str(hit.chunk_id), hit.chunk_key, hit.score) for hit in result.hits] == [
        (row["chunk_id"], row["chunk_key"], row["score"]) for row in expected]


# ---- 向量检索（工单 03）：Seam 1，retrieve_knowledge + 真实 PG + 假 embedder ----

VECTOR_QUERY = "哪一列是物品的名称？"


def _key(*parts):
    """Chunk 的 chunk_key：紧凑 JSON 数组。"""
    return json.dumps(list(parts), ensure_ascii=False, separators=(",", ":"))


@pytest.fixture
def vector_source(retrieval_source):
    """给探针 Corpus 写入假 Profile 的向量。查询向量 [1,0,0]：

    items.label  → [1,0,0]  余弦 1.0
    items 表     → [1,1,0]  余弦 0.7071
    其余 6 个 Chunk → [0,0,1]  余弦 0，并列时按 chunk_key 排
    """
    from querypilot.knowledge.api import embed_corpus
    settings, source, scope = retrieval_source
    db = scope.database_id
    content = {chunk.chunk_key: chunk.content for chunk in source.chunks}
    embedder = FakeEmbedder({
        content[_key("column", db, "main", "items", "label")]: [1.0, 0.0, 0.0],
        content[_key("table", db, "main", "items")]: [1.0, 1.0, 0.0],
        VECTOR_QUERY: [1.0, 0.0, 0.0],
    }, default=(0.0, 0.0, 1.0))
    embed_corpus(scope, settings, embedder)
    return settings, source, scope, embedder


def test_vector_ranks_chunks_by_cosine_similarity(vector_source):
    """输入：method=vector，top_k=3；输出：按余弦相似度排序，并列按 chunk_key；查询只嵌入一次。"""
    settings, _, scope, embedder = vector_source
    db = scope.database_id
    result = retrieve_knowledge(RetrievalRequest(query=VECTOR_QUERY, scope=scope, method="vector", top_k=3),
                                settings, embedder=embedder)
    assert (result.method, result.embedding_profile_id) == ("vector", "fake:3:test-v1")
    assert [hit.chunk_key for hit in result.hits] == [
        _key("column", db, "main", "items", "label"),
        _key("table", db, "main", "items"),
        _key("column", db, "main", "events", "item_ref"),
    ]
    assert [round(hit.score, 4) for hit in result.hits] == [1.0, 0.7071, 0.0]
    assert embedder.query_calls == 1


def test_vector_without_vectors_for_profile_is_embedding_missing(retrieval_source):
    """输入：探针 Corpus 没有当前 Profile 的向量；输出：embedding_missing，查询不嵌入（不花钱）。"""
    settings, _, scope = retrieval_source
    embedder = FakeEmbedder()
    with pytest.raises(KnowledgeRetrievalError) as caught:
        retrieve_knowledge(RetrievalRequest(query=VECTOR_QUERY, scope=scope, method="vector"),
                           settings, embedder=embedder)
    assert caught.value.error_type == "embedding_missing"
    assert embedder.query_calls == 0


def test_embedding_failure_is_embedding_error_without_bm25_fallback(vector_source):
    """输入：已有向量，但嵌入查询时 Embedding 服务失败；输出：embedding_error，不退回 BM25 返回结果。"""
    from _embedding_fakes import ErrorEmbedder
    settings, _, scope, _ = vector_source
    embedder = ErrorEmbedder()
    with pytest.raises(KnowledgeRetrievalError) as caught:
        retrieve_knowledge(RetrievalRequest(query=VECTOR_QUERY, scope=scope, method="vector"),
                           settings, embedder=embedder)
    assert caught.value.error_type == "embedding_error"
    assert embedder.query_calls == 1
    assert VECTOR_QUERY not in str(caught.value)


def test_vector_without_embedder_builds_one_from_settings(retrieval_source, monkeypatch):
    """输入：vector 检索没传 embedder；输出：按 QUERYPILOT_EMBEDDING_* 配置创建，用真实 Profile 检查向量。

    探针 Corpus 没有真实 Profile 的向量，所以在调用服务前就报 embedding_missing。base_url 指向本机
    不监听的端口，万一调用了服务也只会失败，不会产生费用。
    """
    settings, _, scope = retrieval_source
    monkeypatch.setenv("QUERYPILOT_EMBEDDING_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("QUERYPILOT_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-8B")
    monkeypatch.setenv("QUERYPILOT_EMBEDDING_API_KEY", "sk-not-a-real-key")
    with pytest.raises(KnowledgeRetrievalError) as caught:
        retrieve_knowledge(RetrievalRequest(query=VECTOR_QUERY, scope=scope, method="vector"), settings)
    assert caught.value.error_type == "embedding_missing"


def test_vector_filters_by_knowledge_types(vector_source):
    """输入：method=vector，只要表块；输出：只返回表块，items 表（0.7071）排第一。"""
    settings, _, scope, embedder = vector_source
    result = retrieve_knowledge(RetrievalRequest(query=VECTOR_QUERY, scope=scope, method="vector",
                                                 knowledge_types=["table"]), settings, embedder=embedder)
    assert result.hits and {hit.knowledge_type for hit in result.hits} == {"table"}
    assert result.hits[0].chunk_key == _key("table", scope.database_id, "main", "items")
    assert round(result.hits[0].score, 4) == 0.7071


def test_vector_query_needs_no_lexical_terms(vector_source):
    """输入：method=vector，查询全是标点（BM25 会报 input_error）；输出：照常嵌入并返回结果。"""
    settings, _, scope, embedder = vector_source
    result = retrieve_knowledge(RetrievalRequest(query="？！……", scope=scope, method="vector"),
                                settings, embedder=embedder)
    assert result.outcome == "matched" and embedder.query_calls == 1


def test_lexical_neither_calls_nor_builds_an_embedder(vector_source, monkeypatch, tmp_path):
    """输入：method=lexical。传了 embedder 也不调用；没传且没有 Embedding 配置也照常检索。"""
    settings, _, scope, embedder = vector_source
    request = RetrievalRequest(query="items label", scope=scope)
    result = retrieve_knowledge(request, settings, embedder=embedder)
    assert (result.method, result.embedding_profile_id) == ("lexical", None)
    assert embedder.query_calls == 0
    for name in ("BASE_URL", "MODEL", "API_KEY"):
        monkeypatch.delenv(f"QUERYPILOT_EMBEDDING_{name}", raising=False)
    monkeypatch.chdir(tmp_path)                       # 不读仓库的 .env
    assert retrieve_knowledge(request, settings).method == "lexical"
