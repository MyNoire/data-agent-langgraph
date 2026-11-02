"""手动执行的 T010/T011 只读练习，不进入默认测试发现。

仅使用现有库的 reader 和固定语料，不导入、更新或删除知识。
"""

from math import isfinite

import pytest

from querypilot.knowledge.api import KnowledgeScope
from querypilot.retrieval._text import _prepare_query_terms, prepare_query
from querypilot.retrieval.lexical import _search_chunks
from querypilot.retrieval.models import KnowledgeRetrievalError, RetrievalRequest
from _r3_readonly_exercises import source_and_settings, search_session


def search(search_session, query, *, top_k=50, knowledge_types=None):
    """准备词项并调用本次内部函数；返回命中行，仅在已有只读会话内执行。"""
    session, _, scope = search_session
    kwargs = {} if knowledge_types is None else {"knowledge_types": knowledge_types}
    request = RetrievalRequest(query=query, scope=scope, top_k=top_k, **kwargs)
    return _search_chunks(session, request, _prepare_query_terms(prepare_query(query)))


def test_identifier_returns_real_chunks_and_ordered_scores(search_session):
    """atom_id 返回已知列块，身份不越固定范围，分数有限且按既定规则排序。"""
    _, source, _ = search_session
    expected = {
        str(chunk.chunk_id): chunk for chunk in source.chunks
        if chunk.knowledge_type == "column"
    }
    targets = {key for key, chunk in expected.items() if chunk.column_name == "atom_id"}
    assert targets, "固定语料必须提供 atom_id 列块"
    rows = search(search_session, "atom_id", knowledge_types=["column"])
    ids = [str(row["chunk_id"]) for row in rows]
    assert targets <= set(ids)
    assert len(ids) == len(set(ids))
    for row in rows:
        assert set(row) == {"chunk_id", "chunk_key", "knowledge_type", "score"}
        chunk = expected[str(row["chunk_id"])]
        assert row["chunk_key"] == chunk.chunk_key
        assert row["knowledge_type"] == "column"
        assert isfinite(row["score"]) and row["score"] >= 0
    assert rows == sorted(rows, key=lambda row: (
        -row["score"], row["chunk_key"].encode("utf-8"), str(row["chunk_id"]),
    ))


def test_unmatched_word_does_not_cancel_other_word(search_session):
    """已知命中词加一个不存在的词，仍返回原候选；验证词项 OR。"""
    baseline = search(search_session, "cl")
    partial = search(search_session, "cl qpnomatch9f8e7d6c")
    assert baseline, "固定语料的 cl 应命中"
    assert {row["chunk_id"] for row in partial} == {row["chunk_id"] for row in baseline}


def test_no_match_is_empty_list(search_session):
    """有合法词但全部无匹配时返回 []；不把空词或环境错误当成此结果。"""
    assert search(search_session, "qpnomatch9f8e7d6c") == []


def test_top_k_is_prefix_after_ranking(search_session):
    """同一查询先完整排名再取 K，第一条与大 K 的第一条一致。"""
    complete = search(search_session, "atom_id")
    assert len(complete) > 1, "固定语料应有多个候选，避免空洞断言"
    assert search(search_session, "atom_id", top_k=1) == complete[:1]


@pytest.mark.parametrize("knowledge_type", ["table", "column"])
def test_type_filter_has_nonempty_results(search_session, knowledge_type):
    """相同 atom 查询分别限制表/列，两组都应实际命中且类型正确。"""
    rows = search(search_session, "atom", knowledge_types=[knowledge_type])
    assert rows
    assert {row["knowledge_type"] for row in rows} == {knowledge_type}


def test_request_must_match_session_scope(search_session):
    """请求与只读会话范围不一致时明确拒绝，不搜索会话之外的知识。"""
    session, _, scope = search_session
    changed = KnowledgeScope(**{**scope.model_dump(), "database_id": "another_database"})
    request = RetrievalRequest(query="atom_id", scope=changed)
    with pytest.raises(KnowledgeRetrievalError) as caught:
        _search_chunks(session, request, ["atom", "id"])
    assert caught.value.error_type == "scope_mismatch"
