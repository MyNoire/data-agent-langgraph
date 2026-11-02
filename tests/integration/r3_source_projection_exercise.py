"""T012详细来源练习；当前仅收集，验证固定快照合法来源，不写知识库。"""

import pytest

from querypilot.retrieval.lexical import _load_hit_rows
from querypilot.retrieval.models import SourceRef
from _r3_readonly_exercises import source_and_settings, search_session


def read_source(session, original):
    """按已知原块ID读取来源；返回行/来源对象，只用原只读会话。"""
    rows = _load_hit_rows(session, [str(original.chunk_id)])
    assert len(rows) == 1
    row = rows[0]
    assert row["chunk_id"] == original.chunk_id
    assert row["document_id"] == original.document_id
    assert row["document_key"] == original.document_key
    ref = SourceRef.model_validate(row["source_ref"])
    assert ref.document_id == original.document_id
    assert ref.document_key == original.document_key
    assert ref.source_kind == "derived_catalog"
    return row, ref


def expected_fields(original, names):
    """从已核验快照对象挑选预先约定字段；返回独立预期，不读取SQL或检索输出。"""
    return {name: original[name] for name in names if name in original and original[name] is not None}


def test_table_source_preserves_structure_and_semantic_profile(search_session):
    """atom表保留原结构位置/推断性质/profile三项，不能返回整份semantic配置。"""
    session, source, _ = search_session
    original = next(chunk for chunk in source.chunks
                    if chunk.knowledge_type == "table" and chunk.table_name == "atom")
    _, ref = read_source(session, original)
    assert ref.structure_locator == original.metadata["structure_locator"]
    assert ref.semantic_origin == original.metadata["semantic_origin"]
    assert ref.semantic_profile is not None
    assert ref.semantic_profile.model_dump(exclude_none=True) == expected_fields(
        original.metadata["semantic_profile"], ("file", "version", "sha256"),
    )
    assert ref.review_status is None
    assert ref.column_source_path is None
    assert ref.correction_ids == []
    assert ref.provenance_ref is None


@pytest.mark.parametrize("column_name,has_corrections", [("element", True), ("atom_id", False)])
def test_column_source_points_to_real_parent_entry(search_session, column_name, has_corrections):
    """两列分别检验真实修正及空列表；父成员存在才返回路径，不返回原始描述记录。"""
    session, source, _ = search_session
    original = next(chunk for chunk in source.chunks
                    if chunk.knowledge_type == "column" and chunk.table_name == "atom"
                    and chunk.column_name == column_name)
    parent = next(document for document in source.documents if document.document_id == original.document_id)
    entry = parent.metadata["column_sources"][column_name]
    expected_corrections = entry.get("correction_ids", [])
    assert bool(expected_corrections) is has_corrections, "固定样本必须分别覆盖有/无修正"
    _, ref = read_source(session, original)
    assert ref.structure_locator == original.metadata["structure_locator"]
    assert ref.review_status == original.metadata["review_status"]
    assert ref.column_source_path == ["column_sources", column_name]
    assert ref.correction_ids == expected_corrections
    assert ref.semantic_origin is None
    assert ref.semantic_profile is None
    assert ref.provenance_ref is None


def test_relationship_source_preserves_provenance_and_null_locator(search_session):
    """声明外键保留原资料引用和空表列定位，不把源表定位填成关系块定位。"""
    session, source, _ = search_session
    original = next(chunk for chunk in source.chunks if chunk.knowledge_type == "relationship")
    row, ref = read_source(session, original)
    assert row["relation_id"] == original.relation_id and row["relation_key"] == original.relation_key
    assert (row["schema_name"], row["table_name"], row["column_name"]) == (None, None, None)
    assert ref.structure_locator == original.metadata.get("structure_locator")
    assert ref.semantic_origin == original.metadata["semantic_origin"]
    profile = original.metadata.get("semantic_profile")
    if profile is None:
        assert ref.semantic_profile is None
    else:
        assert ref.semantic_profile is not None
        assert ref.semantic_profile.model_dump(exclude_none=True) == expected_fields(profile, ("file", "version", "sha256"))
    assert ref.provenance_ref is not None
    assert ref.provenance_ref.model_dump(exclude_none=True) == expected_fields(
        original.metadata["provenance"], ("artifact", "artifact_sha256", "locator"),
    )
    assert ref.review_status is None
    assert ref.column_source_path is None
    assert ref.correction_ids == []
