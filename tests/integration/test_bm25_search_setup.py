"""可立即运行的真实 BM25 设施验收；不依赖尚未实现的 R3 检索核心。

显式执行本文件并加 --require-postgres 时，环境缺失必须失败。
原创样本只进入同库唯一 schema，原知识表不写入、不清空。
"""

from math import isfinite

import pytest

from _bm25_fixtures import (
    bm25_probe,
    live_bm25_settings,
    role_connection,
    unchanged_live_knowledge,
    unprepared_search_table,
)


def probe_query(probe, terms, *, column="content", types=None, scope=None, top_k=50):
    """用 reader 在同一只读快照执行参数化词项 OR；仅查询本次自有表。"""
    from psycopg import sql

    if column == "display_name":
        expression = sql.SQL("(CASE WHEN jsonb_typeof(metadata->'display_name')='string' "
                             "THEN metadata->>'display_name' END)")
    elif column in {"content", "title", "table_name", "column_name"}:
        expression = sql.Identifier(column)
    else:
        raise ValueError("只允许测试约定的搜索字段")
    scope = scope or ("scope-a", "db-a", "v1")
    statement = sql.SQL("""SELECT chunk_id::text,chunk_key,knowledge_type,pdb.score(chunk_id) AS score
        FROM {} WHERE corpus_id=%s AND database_id=%s AND schema_version=%s
        AND knowledge_type=ANY(%s::text[]) AND {} ||| %s::text[]
        ORDER BY score DESC,chunk_key COLLATE "C",chunk_id LIMIT %s""").format(probe["table"], expression)
    with role_connection(probe["settings"], "read") as reader, reader.transaction():
        reader.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        return reader.execute(statement, (*scope, types or ["table", "column", "relationship"], terms, top_k)).fetchall()


def test_search_management_reuses_real_index(live_bm25_settings, unchanged_live_knowledge):
    """重复管理员准备应复用已验证索引；只读检查返回实际配置和全索引语料摘要。"""
    from querypilot.knowledge.api import check_search, prepare_search

    first = prepare_search(live_bm25_settings)
    second = prepare_search(live_bm25_settings)
    checked = check_search(live_bm25_settings)
    assert first.outcome == second.outcome == "reused"
    assert checked.outcome == "checked"
    assert first.search_index_profile == second.search_index_profile == checked.search_index_profile
    assert first.index_corpus_fingerprint == second.index_corpus_fingerprint == checked.index_corpus_fingerprint
    assert checked.indexed_chunks > 0
    assert len(checked.index_corpus_fingerprint) == 64


def test_search_management_creates_missing_index_and_detects_changed_corpus(unprepared_search_table, monkeypatch):
    """真实公开操作首次建立自有索引；语料实际新增后旧指纹必须拒绝，原设施不动。"""
    from psycopg import sql
    from querypilot.knowledge.api import (
        KnowledgeReadError, KnowledgeScope, check_search, check_search_session,
        open_read_scope, prepare_search,
    )
    from querypilot.knowledge.knowledge_store import _search

    probe = unprepared_search_table
    settings = probe["settings"]
    # 只替换可信物理目标；准备、DDL、配置检查及指纹计算均运行原有真实实现。
    monkeypatch.setattr(_search, "SEARCH_SCHEMA", probe["schema"])
    with role_connection(settings, "admin") as conn:
        assert conn.execute("SELECT to_regclass(%s) AS target",
                            (probe["schema"] + ".knowledge_chunks_bm25_idx",)).fetchone()["target"] is None
    prepared = prepare_search(settings)
    checked = check_search(settings)
    reused = prepare_search(settings)
    assert (prepared.outcome, checked.outcome, reused.outcome) == ("applied", "checked", "reused")
    assert prepared.search_index_profile.index_schema == probe["schema"]
    assert prepared.search_index_profile == checked.search_index_profile == reused.search_index_profile
    assert prepared.index_corpus_fingerprint == checked.index_corpus_fingerprint == reused.index_corpus_fingerprint
    assert prepared.indexed_chunks == checked.indexed_chunks == reused.indexed_chunks == 0

    with role_connection(settings, "admin") as conn:
        conn.execute(sql.SQL("""INSERT INTO {} (chunk_id,chunk_key,corpus_id,database_id,
            schema_version,knowledge_type,title,content,schema_name,table_name,column_name,metadata)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""").format(probe["table"]),
            ("00000000-0000-5000-8000-000000000001", "own-new-corpus-row", "own-other-corpus",
             "own-other-db", "v1", "table", "新增语料", "common", "main", "items", None, "{}"))
    changed = check_search(settings)
    assert changed.indexed_chunks == 1
    assert changed.search_index_profile == checked.search_index_profile
    assert changed.index_corpus_fingerprint != checked.index_corpus_fingerprint

    with role_connection(settings, "read") as conn:
        original_scope = conn.execute("""SELECT corpus_id,database_id,schema_version
            FROM querypilot_kb.knowledge_corpora ORDER BY corpus_id LIMIT 1""").fetchone()
    assert original_scope is not None
    with open_read_scope(KnowledgeScope(**original_scope), settings) as session:
        with pytest.raises(KnowledgeReadError) as caught:
            check_search_session(session, expected_profile=checked.search_index_profile,
                                 expected_fingerprint=checked.index_corpus_fingerprint)
        assert caught.value.error_type == "migration_conflict"
        current = check_search_session(session, expected_profile=changed.search_index_profile,
                                       expected_fingerprint=changed.index_corpus_fingerprint)
        assert current.model_dump() == changed.model_dump(exclude={"command", "outcome"})


def test_search_check_uses_existing_read_session(live_bm25_settings, unchanged_live_knowledge):
    """完整范围入口提供的 reader/RR/只读事务内核对设施，不另开连接或增写权。"""
    from querypilot.knowledge.api import (
        KnowledgeScope, check_search, check_search_session, open_read_scope,
    )

    expected = check_search(live_bm25_settings)
    with role_connection(live_bm25_settings, "read") as reader:
        row = reader.execute("""SELECT corpus_id,database_id,schema_version
            FROM querypilot_kb.knowledge_corpora ORDER BY corpus_id LIMIT 1""").fetchone()
    assert row is not None
    with open_read_scope(KnowledgeScope(**row), live_bm25_settings) as session:
        state = check_search_session(session, expected_profile=expected.search_index_profile,
                                     expected_fingerprint=expected.index_corpus_fingerprint)
        assert state.search_index_profile == expected.search_index_profile
        assert state.index_corpus_fingerprint == expected.index_corpus_fingerprint
        assert state.indexed_chunks == expected.indexed_chunks
        settings = session.fetch_one("""SELECT current_user AS role,
            current_setting('transaction_read_only') AS readonly,
            current_setting('transaction_isolation') AS isolation,
            has_table_privilege(current_user,'querypilot_kb.knowledge_chunks','SELECT') AS can_read,
            has_table_privilege(current_user,'querypilot_kb.knowledge_chunks',
                'INSERT,UPDATE,DELETE,TRUNCATE') AS can_write""")
    assert settings == {"role": "querypilot_kb_reader", "readonly": "on",
                        "isolation": "repeatable read", "can_read": True, "can_write": False}


def test_real_bm25_frequency_rarity_and_stable_ties(bm25_probe):
    """真实插件词频/稀有度产生排序；并列用原逻辑 key，不能靠手工加分。"""
    common = probe_query(bm25_probe, ["common"])
    assert [row["chunk_key"] for row in common] == ["a", "b", "c", "d"]
    assert common[0]["score"] > common[1]["score"] == common[2]["score"] == common[3]["score"]
    mixed = probe_query(bm25_probe, ["common", "rare"])
    assert [row["chunk_key"] for row in mixed] == ["e", "a", "b", "c", "d"]
    assert all(isfinite(row["score"]) and row["score"] > 0 for row in mixed)
    assert mixed == probe_query(bm25_probe, ["common", "rare"])
    assert mixed == probe_query(bm25_probe, ["common", "rare"])


def test_real_or_scope_type_and_limit_apply_before_top_k(bm25_probe):
    """任一词命中即可；三元范围和类型先约束，再由真实分数取 K。"""
    matched = probe_query(bm25_probe, ["common", "qpnomatch9f8e7d6c"], types=["column"], top_k=1)
    assert len(matched) == 1 and matched[0]["chunk_key"] == "b"
    assert matched[0]["knowledge_type"] == "column"
    for scope in (("scope-b", "db-a", "v1"), ("scope-a", "db-b", "v1"), ("scope-a", "db-a", "v2")):
        result = probe_query(bm25_probe, ["common"], scope=scope)
        assert len(result) == 1 and result[0]["chunk_key"] in {"x", "y", "z"}
    assert not probe_query(bm25_probe, ["qpnomatch9f8e7d6c"])


def test_real_chinese_terms_do_not_fall_back_to_substrings(bm25_probe):
    """索引中的长江词项可命中；仅在原文出现的京市长子串不能命中。"""
    assert [row["chunk_key"] for row in probe_query(bm25_probe, ["长江"], column="title")] == ["a"]
    assert not probe_query(bm25_probe, ["京市长"], column="title")


def test_real_locators_and_string_display_name_are_searchable(bm25_probe):
    """标识符按插件词项检索，短编码及字符串 display_name 可查；其它 metadata 不参与。"""
    assert [row["chunk_key"] for row in probe_query(bm25_probe, ["cl"], column="column_name")] == ["b"]
    assert any(row["chunk_key"] == "a" for row in probe_query(bm25_probe, ["atom", "id"], column="table_name"))
    assert [row["chunk_key"] for row in probe_query(bm25_probe, ["库存"], column="display_name")] == ["f"]
    assert not probe_query(bm25_probe, ["123"], column="display_name")
    assert not probe_query(bm25_probe, ["qpmetadatasecret"], column="display_name")


def test_probe_uses_real_index_without_reader_write_privilege(bm25_probe):
    """实际执行计划必须使用 ParadeDB；reader 在本次自有对象上也只有查询能力。"""
    from psycopg import sql

    with role_connection(bm25_probe["settings"], "read") as reader, reader.transaction():
        reader.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        plan = reader.execute(sql.SQL("EXPLAIN (ANALYZE, FORMAT JSON) SELECT chunk_id FROM {} "
                              "WHERE content ||| %s::text[]").format(bm25_probe["table"]), (["common"],)).fetchone()
        assert "ParadeDB" in str(plan) or "paradedb" in str(plan)
        privileges = reader.execute("""SELECT current_user AS role,
            current_setting('transaction_read_only') AS readonly,
            has_table_privilege(current_user,%s,'SELECT') AS can_read,
            has_table_privilege(current_user,%s,'INSERT,UPDATE,DELETE,TRUNCATE') AS can_write""",
            (bm25_probe["schema"] + ".docs", bm25_probe["schema"] + ".docs")).fetchone()
    assert privileges == {"role": "querypilot_kb_reader", "readonly": "on", "can_read": True, "can_write": False}
