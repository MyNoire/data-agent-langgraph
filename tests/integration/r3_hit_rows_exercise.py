"""T012读取首段：原字段与基础父来源；显式只读运行，尚未验收完整metadata来源。"""

import pytest

from querypilot.retrieval.lexical import _load_hit_rows
from querypilot.retrieval.models import SourceRef
from _r3_readonly_exercises import source_and_settings, search_session


@pytest.mark.parametrize("knowledge_type", ["table", "column", "relationship"])
def test_reads_only_requested_chunk_with_original_fields(search_session, knowledge_type):
    """指定一个已知ID，原值/范围/文本ID和基础来源正确；不据此证明可选来源齐全。"""
    session, source, scope = search_session
    original = next(chunk for chunk in source.chunks if chunk.knowledge_type == knowledge_type)
    parent = next(document for document in source.documents if document.document_id == original.document_id)
    rows = _load_hit_rows(session, [str(original.chunk_id)])
    assert len(rows) == 1
    row = rows[0]
    original_fields = (
        "chunk_id", "chunk_key", "knowledge_type", "title", "content", "content_hash",
        "document_id", "document_key", "schema_name", "table_name", "column_name",
        "relation_id", "relation_key",
    )
    assert set(row) == {*original_fields, "corpus_id", "database_id", "schema_version", "source_ref"}
    for name in original_fields:
        assert row[name] == getattr(original, name)
    assert isinstance(row["chunk_id"], str) and isinstance(row["document_id"], str)
    if original.relation_id is not None:
        assert isinstance(row["relation_id"], str)
    for name, value in scope.model_dump().items():
        assert row[name] == value
    ref = row["source_ref"]
    assert isinstance(ref, dict)
    assert {"document_id", "document_key", "source_kind"} <= set(ref)
    assert set(ref) <= set(SourceRef.model_fields)
    assert ref["document_id"] == original.document_id
    assert ref["document_key"] == original.document_key
    assert ref["source_kind"] == parent.source_kind


def test_empty_ids_need_no_session_access():
    """空ID列表无需读取会话属性或执行查询；不是有效查询无匹配的业务结果。"""
    assert _load_hit_rows(object(), []) == []
