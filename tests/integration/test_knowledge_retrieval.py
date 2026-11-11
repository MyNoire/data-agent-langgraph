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
from _embedding_fakes import ErrorEmbedder, FakeEmbedder
from _rerank_fakes import ErrorReranker, FakeReranker


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
    assert result.retrieval_config_version == "r4-vector-v1"
    assert [hit.chunk_key for hit in result.hits] == [
        _key("column", db, "main", "items", "label"),
        _key("table", db, "main", "items"),
        _key("column", db, "main", "events", "item_ref"),
    ]
    assert [round(hit.score, 4) for hit in result.hits] == [1.0, 0.7071, 0.0]
    assert embedder.query_calls == 1


@pytest.mark.parametrize("method", ["vector", "hybrid"])
def test_vector_and_hybrid_without_vectors_are_embedding_missing(retrieval_source, method):
    """输入：探针 Corpus 没有当前 Profile 的向量；输出：embedding_missing，查询不嵌入（不花钱）。
    hybrid 也不只返回 BM25 那一路。"""
    settings, _, scope = retrieval_source
    embedder = FakeEmbedder()
    with pytest.raises(KnowledgeRetrievalError) as caught:
        retrieve_knowledge(RetrievalRequest(query=VECTOR_QUERY, scope=scope, method=method),
                           settings, embedder=embedder)
    assert caught.value.error_type == "embedding_missing"
    assert embedder.query_calls == 0


@pytest.mark.parametrize("method", ["vector", "hybrid"])
def test_embedding_failure_is_embedding_error_without_bm25_fallback(vector_source, method):
    """输入：已有向量，但嵌入查询时 Embedding 服务失败；输出：embedding_error，不退回 BM25 返回结果。"""
    settings, _, scope, _ = vector_source
    embedder = ErrorEmbedder()
    with pytest.raises(KnowledgeRetrievalError) as caught:
        retrieve_knowledge(RetrievalRequest(query=VECTOR_QUERY, scope=scope, method=method),
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


@pytest.mark.parametrize(("method", "top_score"), [("vector", 0.7071), ("hybrid", round(2 / 61, 4))])
def test_vector_and_hybrid_filter_by_knowledge_types(vector_source, method, top_score):
    """输入：只要表块；输出：只返回表块，items 表排第一。vector 分数为余弦 0.7071；
    hybrid 中 items 表在两路的表块里都排第 1，RRF 分 2/61。"""
    settings, _, scope, embedder = vector_source
    result = retrieve_knowledge(RetrievalRequest(query=VECTOR_QUERY, scope=scope, method=method,
                                                 knowledge_types=["table"]), settings, embedder=embedder)
    assert result.hits and {hit.knowledge_type for hit in result.hits} == {"table"}
    assert result.hits[0].chunk_key == _key("table", scope.database_id, "main", "items")
    assert round(result.hits[0].score, 4) == top_score


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


def test_hybrid_fuses_both_rankings_with_rrf(vector_source):
    """输入：method=hybrid，top_k=5。两路各取 50 个候选（探针 Corpus 只有 8 个 Chunk）：

    BM25：items 表、events 表、关系                     （"物品"命中三个块）
    向量：label、items 表、item_ref、tenant_ref、item_id、tenant_id、关系、events 表
    RRF： items 表 1/61+1/62，events 表 1/62+1/68，关系 1/63+1/67，label 1/61，item_ref 1/63

    输出：label 在向量里第 1、融合后第 4；events 表在向量里最后、融合后第 2。
    """
    settings, _, scope, embedder = vector_source
    db = scope.database_id
    result = retrieve_knowledge(RetrievalRequest(query=VECTOR_QUERY, scope=scope, method="hybrid", top_k=5),
                                settings, embedder=embedder)
    assert (result.method, result.embedding_profile_id) == ("hybrid", "fake:3:test-v1")
    assert result.retrieval_config_version == "r4-hybrid-v1"
    assert [hit.chunk_key for hit in result.hits] == [
        _key("table", db, "main", "items"),
        _key("table", db, "main", "events"),
        _key("relationship", db, "main", "events", 0),
        _key("column", db, "main", "items", "label"),
        _key("column", db, "main", "events", "item_ref"),
    ]
    assert [hit.rank for hit in result.hits] == [1, 2, 3, 4, 5]
    assert [hit.score for hit in result.hits] == pytest.approx(
        [0.0325225, 0.0308349, 0.0307984, 0.0163934, 0.0158730], abs=1e-7)
    assert embedder.query_calls == 1


def test_hybrid_with_no_bm25_hits_returns_the_vector_ranking(vector_source):
    """输入：method=hybrid，查询有词但 BM25 一个都没命中，查询向量为 [1,0,0]；
    输出：BM25 那一路为空，名次与 vector 相同，分数为 RRF 分 1/61、1/62、1/63。"""
    settings, _, scope, _ = vector_source
    db = scope.database_id
    embedder = FakeEmbedder({"zzqxv": [1.0, 0.0, 0.0]})
    result = retrieve_knowledge(RetrievalRequest(query="zzqxv", scope=scope, method="hybrid", top_k=3),
                                settings, embedder=embedder)
    assert [hit.chunk_key for hit in result.hits] == [
        _key("column", db, "main", "items", "label"),
        _key("table", db, "main", "items"),
        _key("column", db, "main", "events", "item_ref"),
    ]
    assert [hit.score for hit in result.hits] == pytest.approx([0.0163934, 0.0161290, 0.0158730], abs=1e-7)


def test_hybrid_without_lexical_terms_is_input_error_without_embedding(vector_source):
    """输入：method=hybrid，查询全是标点；输出：与 lexical 一样报 input_error，不调用 Embedding 服务。

    向量检索对任何输入都会返回 top_k 个结果，退化成纯向量会把明确的输入错误变成看似正常的 Hit。
    """
    settings, _, scope, embedder = vector_source
    with pytest.raises(KnowledgeRetrievalError) as caught:
        retrieve_knowledge(RetrievalRequest(query="？！……", scope=scope, method="hybrid"), settings, embedder=embedder)
    assert caught.value.error_type == "input_error"
    assert embedder.query_calls == 0


# ---- 重排：retrieve_knowledge + 真实 PG + 假 Reranker ----

def _contents(source):
    """chunk_key → content。"""
    return {chunk.chunk_key: chunk.content for chunk in source.chunks}


def test_rerank_orders_hits_by_reranker_score(retrieval_source):
    """输入：lexical + rerank。BM25 的顺序是 items 表、events 表、关系；假 Reranker 给关系 0.9、
    events 表 0.5、items 表 0.2。

    输出：Hit 按相关性分数从高到低排，score 就是相关性分数，rank 从 1 连续；Reranker 收到查询原文
    和按 BM25 顺序排列的 content 原文，只调用一次；结果写明 Rerank Profile 和带重排的配置版本。
    """
    settings, source, scope = retrieval_source
    db = scope.database_id
    content = _contents(source)
    items, events, relation = (_key("table", db, "main", "items"), _key("table", db, "main", "events"),
                               _key("relationship", db, "main", "events", 0))
    reranker = FakeReranker({content[relation]: 0.9, content[events]: 0.5, content[items]: 0.2})
    result = retrieve_knowledge(RetrievalRequest(query=VECTOR_QUERY, scope=scope, rerank=True),
                                settings, reranker=reranker)
    assert [hit.chunk_key for hit in result.hits] == [relation, events, items]
    assert [hit.score for hit in result.hits] == [0.9, 0.5, 0.2]
    assert [hit.rank for hit in result.hits] == [1, 2, 3]
    assert reranker.calls == [(VECTOR_QUERY, [content[items], content[events], content[relation]])]
    assert result.rerank_profile_id == "fake-rerank:test-v1"
    assert result.retrieval_config_version == "r3-bm25-v1+r4b-rerank-v1"


def test_rerank_scores_all_candidates_before_cutting_to_top_k(vector_source):
    """输入：vector + rerank，top_k=2。向量的顺序里 events 表排在 8 个 Chunk 的最后；假 Reranker 给
    events 表 0.9、label 0.5，其余 0。

    输出：候选数与 top_k 无关，8 个 Chunk 都交给 Reranker；排在候选最后的 events 表被提到第 1。
    """
    settings, source, scope, embedder = vector_source
    db = scope.database_id
    content = _contents(source)
    events, label = _key("table", db, "main", "events"), _key("column", db, "main", "items", "label")
    reranker = FakeReranker({content[events]: 0.9, content[label]: 0.5})
    result = retrieve_knowledge(RetrievalRequest(query=VECTOR_QUERY, scope=scope, method="vector", rerank=True,
                                                 top_k=2), settings, embedder=embedder, reranker=reranker)
    assert [(hit.rank, hit.chunk_key, hit.score) for hit in result.hits] == [(1, events, 0.9), (2, label, 0.5)]
    assert len(reranker.calls[0][1]) == 8
    assert (result.top_k, result.hit_count) == (2, 2)


def test_rerank_without_candidates_is_no_match_without_calling_reranker(retrieval_source):
    """输入：lexical + rerank，查询有词但 BM25 一个都没命中；输出：no_match，不调用 Reranker（不花钱）。"""
    settings, _, scope = retrieval_source
    reranker = FakeReranker()
    result = retrieve_knowledge(RetrievalRequest(query="qpnomatch9f8e7d6c", scope=scope, rerank=True),
                                settings, reranker=reranker)
    assert (result.outcome, result.hits) == ("no_match", [])
    assert reranker.calls == []
    # 结果仍写明这次请求按哪套配置执行；有没有实际打分看 hits 是否为空。
    assert (result.rerank_profile_id, result.retrieval_config_version) == (
        "fake-rerank:test-v1", "r3-bm25-v1+r4b-rerank-v1")


def test_lexical_rerank_candidates_do_not_depend_on_top_k(retrieval_source):
    """输入：lexical + rerank，top_k=1。BM25 命中 3 个，顺序是 items 表、events 表、关系；假 Reranker 只给
    排在最后的关系 0.9。

    输出：3 个候选都交给 Reranker（不是只取 top_k 个），关系被提到第 1 并作为唯一的结果返回。
    """
    settings, source, scope = retrieval_source
    db, content = scope.database_id, _contents(source)
    relation = _key("relationship", db, "main", "events", 0)
    reranker = FakeReranker({content[relation]: 0.9})
    result = retrieve_knowledge(RetrievalRequest(query=VECTOR_QUERY, scope=scope, rerank=True, top_k=1),
                                settings, reranker=reranker)
    assert len(reranker.calls[0][1]) == 3
    assert [(hit.rank, hit.chunk_key, hit.score) for hit in result.hits] == [(1, relation, 0.9)]


def test_rerank_failure_is_rerank_error_without_unreranked_hits(retrieval_source):
    """输入：lexical + rerank，BM25 有命中，但 Reranker 服务失败；输出：rerank_error，不返回未重排的结果，
    错误消息里没有查询原文。"""
    settings, _, scope = retrieval_source
    reranker = ErrorReranker()
    with pytest.raises(KnowledgeRetrievalError) as caught:
        retrieve_knowledge(RetrievalRequest(query=VECTOR_QUERY, scope=scope, rerank=True),
                           settings, reranker=reranker)
    assert caught.value.error_type == "rerank_error"
    assert len(reranker.calls) == 1
    assert VECTOR_QUERY not in str(caught.value)


def _vector_order(db):
    """vector_source 下向量那一路的完整顺序（余弦 1.0、0.7071，其余并列按 chunk_key）。"""
    return [
        _key("column", db, "main", "items", "label"), _key("table", db, "main", "items"),
        _key("column", db, "main", "events", "item_ref"), _key("column", db, "main", "events", "tenant_ref"),
        _key("column", db, "main", "items", "item_id"), _key("column", db, "main", "items", "tenant_id"),
        _key("relationship", db, "main", "events", 0), _key("table", db, "main", "events"),
    ]


def test_rerank_keeps_candidate_order_for_equal_scores(vector_source):
    """输入：vector + rerank，假 Reranker 只给 tenant_id 0.7，其余 7 个都是 0。

    输出：tenant_id 排第 1；分数相同的 7 个保持重排前（向量）的先后顺序，不按 chunk_key 重新排。
    """
    settings, source, scope, embedder = vector_source
    order = _vector_order(scope.database_id)
    tenant_id = order[5]
    reranker = FakeReranker({_contents(source)[tenant_id]: 0.7})
    result = retrieve_knowledge(RetrievalRequest(query=VECTOR_QUERY, scope=scope, method="vector", rerank=True),
                                settings, embedder=embedder, reranker=reranker)
    assert [hit.chunk_key for hit in result.hits] == [tenant_id] + order[:5] + order[6:]
    assert [hit.score for hit in result.hits] == [0.7] + [0.0] * 7


def _fused_order(db):
    """vector_source 下 VECTOR_QUERY 的 RRF 融合顺序：items 表、events 表、关系在两路都出现；label、item_ref、
    tenant_ref、item_id、tenant_id 只在向量那一路，RRF 分依次为 1/61、1/63、1/64、1/65、1/66。"""
    return [
        _key("table", db, "main", "items"), _key("table", db, "main", "events"),
        _key("relationship", db, "main", "events", 0), _key("column", db, "main", "items", "label"),
        _key("column", db, "main", "events", "item_ref"), _key("column", db, "main", "events", "tenant_ref"),
        _key("column", db, "main", "items", "item_id"), _key("column", db, "main", "items", "tenant_id"),
    ]


@pytest.mark.parametrize(("method", "ordering"), [("vector", _vector_order), ("hybrid", _fused_order)])
def test_rerank_takes_a_fixed_number_of_candidates(vector_source, monkeypatch, method, ordering):
    """输入：vector 或 hybrid + rerank，top_k=1；候选数常量临时改成 3（探针 Corpus 只有 8 个 Chunk，够不到
    真实的 20）。假 Reranker 给该方法顺序里的第 3 个 0.6、最后一个 0.9。

    输出：只有该方法顺序的前 3 个交给 Reranker，其中分数最高的作为结果；排在第 3 之后的 Chunk 分数再高也不会出现。
    """
    from querypilot.retrieval import _rerank
    monkeypatch.setattr(_rerank, "RERANK_CANDIDATES", 3)
    settings, source, scope, embedder = vector_source
    order, content = ordering(scope.database_id), _contents(source)
    reranker = FakeReranker({content[order[2]]: 0.6, content[order[7]]: 0.9})
    result = retrieve_knowledge(RetrievalRequest(query=VECTOR_QUERY, scope=scope, method=method, rerank=True,
                                                 top_k=1), settings, embedder=embedder, reranker=reranker)
    assert reranker.calls == [(VECTOR_QUERY, [content[key] for key in order[:3]])]
    assert [(hit.chunk_key, hit.score) for hit in result.hits] == [(order[2], 0.6)]


def test_hybrid_rerank_reorders_the_fused_candidates(vector_source):
    """输入：hybrid + rerank，top_k=3；假 Reranker 只给融合顺序里最后一名的 tenant_id 0.8。

    输出：Reranker 收到的是融合后的顺序；tenant_id 从融合后的最后一名提到第 1；结果同时写明
    Embedding Profile 和 Rerank Profile，配置版本是 hybrid 的版本加重排的版本；查询只嵌入一次。
    """
    settings, source, scope, embedder = vector_source
    db, content = scope.database_id, _contents(source)
    fused = _fused_order(db)
    reranker = FakeReranker({content[fused[7]]: 0.8})
    result = retrieve_knowledge(RetrievalRequest(query=VECTOR_QUERY, scope=scope, method="hybrid", rerank=True,
                                                 top_k=3), settings, embedder=embedder, reranker=reranker)
    assert reranker.calls == [(VECTOR_QUERY, [content[key] for key in fused])]
    assert [(hit.chunk_key, hit.score) for hit in result.hits] == [(fused[7], 0.8), (fused[0], 0.0), (fused[1], 0.0)]
    assert (result.method, result.embedding_profile_id, result.rerank_profile_id) == (
        "hybrid", "fake:3:test-v1", "fake-rerank:test-v1")
    assert result.retrieval_config_version == "r4-hybrid-v1+r4b-rerank-v1"
    assert embedder.query_calls == 1


def test_rerank_is_not_reached_when_retrieval_itself_fails(vector_source):
    """输入：开启重排，但检索这一步就失败。查询没有有效词 → input_error；嵌入查询失败 → embedding_error。

    输出：错误类别和不开重排时一样，Reranker 都没被调用。
    """
    settings, _, scope, embedder = vector_source
    reranker = FakeReranker()
    with pytest.raises(KnowledgeRetrievalError) as no_terms:
        retrieve_knowledge(RetrievalRequest(query="？！……", scope=scope, method="hybrid", rerank=True),
                           settings, embedder=embedder, reranker=reranker)
    with pytest.raises(KnowledgeRetrievalError) as embedding_failed:
        retrieve_knowledge(RetrievalRequest(query=VECTOR_QUERY, scope=scope, method="vector", rerank=True),
                           settings, embedder=ErrorEmbedder(), reranker=reranker)
    assert (no_terms.value.error_type, embedding_failed.value.error_type) == ("input_error", "embedding_error")
    assert reranker.calls == []


def test_without_rerank_the_reranker_is_not_called_and_results_are_unchanged(retrieval_source):
    """输入：不开重排（默认），一次传了 Reranker、一次没传；输出：Reranker 没被调用，两次的 Hit 完全相同，
    分数仍是 BM25 分，结果里没有 Rerank Profile，配置版本不带重排。"""
    settings, _, scope = retrieval_source
    reranker = FakeReranker(default=0.5)
    request = RetrievalRequest(query=VECTOR_QUERY, scope=scope)
    plain = retrieve_knowledge(request, settings)
    result = retrieve_knowledge(request, settings, reranker=reranker)
    assert reranker.calls == []
    assert result.hits == plain.hits and len(result.hits) == 3
    assert (result.rerank_profile_id, result.retrieval_config_version) == (None, "r3-bm25-v1")
