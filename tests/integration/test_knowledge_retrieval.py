"""R3 完整检索与范围验收；未完成的核心组只收集，不冒充 BM25 已通过。

本文件不调用旧 pg_database；所有写入仅限同库本次唯一 corpus。
当前可立即执行的设施组在 test_bm25_search_setup.py。
"""

import pytest
from hashlib import sha256
from math import isfinite
from uuid import uuid4

from querypilot.retrieval.models import KnowledgeRetrievalError, KnowledgeScope, RetrievalRequest
from querypilot.retrieval.service import open_scoped_read
from _bm25_fixtures import live_bm25_settings, role_connection, unchanged_live_knowledge


@pytest.fixture
def retrieval_source(live_bm25_settings, r3_corpus_factory, unchanged_live_knowledge):
    """导入本次唯一原创 corpus；结束按身份精确删除，不改已有知识或权限。"""
    from psycopg import sql
    from querypilot.knowledge.api import import_snapshot, load_snapshot

    database_id = "r3probe" + uuid4().hex
    path = r3_corpus_factory("r3-source", database_id=database_id)
    source = load_snapshot(path)
    scope = KnowledgeScope(corpus_id=source.manifest.corpus_id, database_id=database_id,
                           schema_version=source.manifest.identity_descriptor.schema_versions[database_id])
    with role_connection(live_bm25_settings, "admin") as conn:
        assert not conn.execute("SELECT 1 FROM querypilot_kb.knowledge_corpora WHERE corpus_id=%s",
                                (scope.corpus_id,)).fetchone()
    try:
        import_snapshot(path, live_bm25_settings)
        yield live_bm25_settings, source, scope
    finally:
        # 先证明是本次生成且此前不存在的身份，再逐表按完整 scope 和记录 ID 清理。
        with role_connection(live_bm25_settings, "admin") as conn, conn.transaction():
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
    with open_scoped_read(scope, settings) as session:
        settings_row = session.fetch_one("""SELECT current_user AS role,
            current_setting('transaction_read_only') AS readonly,
            current_setting('transaction_isolation') AS isolation""")
        assert settings_row == {"role": "querypilot_kb_reader", "readonly": "on", "isolation": "repeatable read"}
        assert session.counts == source.manifest.counts
        assert session.search_bytes > 0
        assert not any(hasattr(session, name) for name in ("connection", "cursor", "commit"))
    from querypilot.knowledge.api import KnowledgeReadError
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
        with open_scoped_read(KnowledgeScope(**{**scope.model_dump(), **change}), settings):
            pytest.fail("错误范围不应进入读取体")
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
        with open_scoped_read(scope, settings):
            pytest.fail("坏状态不应进入读取体")
    assert caught.value.error_type == "knowledge_state_error"


def test_scoped_read_checks_full_scope_capacity(retrieval_source, monkeypatch):
    """输入：受控缩小预算；返回：完整范围容量失败断言；仅注入限额，不灌大数据。"""
    from querypilot.retrieval import service

    settings, _, scope = retrieval_source
    monkeypatch.setattr(service, "PROFILE", service.PROFILE.model_copy(update={"max_search_bytes": 1}))
    with pytest.raises(KnowledgeRetrievalError) as caught:
        with open_scoped_read(scope, settings):
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
    from querypilot.retrieval.api import retrieve_knowledge

    settings, source, scope = retrieval_source
    result = retrieve_knowledge(RetrievalRequest(query=query, scope=scope, top_k=5,
                                                knowledge_types=[kind]), settings)
    assert result.outcome == ("matched" if should_match else "no_match")
    assert bool(result.hits) is should_match
    assert result.retrieval_config_version == "r3-bm25-v1"
    assert result.query_sha256 == sha256(query.encode("utf-8")).hexdigest()
    assert all(hit.knowledge_type == kind for hit in result.hits)
    originals = {chunk.chunk_id: chunk for chunk in source.chunks}
    for rank, hit in enumerate(result.hits, 1):
        original = originals[hit.chunk_id]
        assert hit.rank == rank and hit.scope == scope
        assert isfinite(hit.score) and hit.score >= 0
        for field in ("chunk_key", "title", "content", "content_hash", "document_id", "document_key",
                      "schema_name", "table_name", "column_name", "relation_id", "relation_key"):
            assert getattr(hit, field) == getattr(original, field)
        assert hit.source_ref.document_id == original.document_id
        assert hit.source_ref.document_key == original.document_key
    assert [(hit.score, hit.chunk_key, str(hit.chunk_id)) for hit in result.hits] == sorted(
        [(hit.score, hit.chunk_key, str(hit.chunk_id)) for hit in result.hits],
        key=lambda value: (-value[0], value[1].encode("utf-8"), value[2]))
    if should_match:
        if kind == "column":
            assert any(hit.table_name == "items" and hit.column_name == expected_column for hit in result.hits)
        elif kind == "table":
            assert any(hit.table_name == "items" for hit in result.hits)
        else:
            assert all(hit.relation_id is not None and hit.table_name is None for hit in result.hits)


@pytest.mark.parametrize("query", ["---", "请问 的 谢谢"])
def test_complete_query_without_effective_terms_is_input_error(retrieval_source, query):
    """完整操作过滤后无词必须报输入错误；不能当 no_match 或返回全库。"""
    from querypilot.retrieval.api import retrieve_knowledge

    settings, _, scope = retrieval_source
    with pytest.raises(KnowledgeRetrievalError) as caught:
        retrieve_knowledge(RetrievalRequest(query=query, scope=scope), settings)
    assert caught.value.error_type == "input_error"


def test_complete_scores_and_order_equal_raw_plugin_bm25(retrieval_source):
    """公开检索的身份、分数及次序须等于同条件插件原分数，拒绝隐藏人工加分。"""
    from querypilot.knowledge.api import open_read_scope
    from querypilot.retrieval.api import retrieve_knowledge

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
