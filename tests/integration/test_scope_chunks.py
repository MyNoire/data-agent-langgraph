"""knowledge 来源的知识读取：经 load_scope_chunks 读出一个 Knowledge Scope 下的全部 Chunk。

跑在专用测试库 querypilot_knowledge_test 上，导入两份原创 Corpus，不读写正式知识库。
"""

from uuid import uuid4

import pytest

from querypilot.knowledge.api import KnowledgeScope, import_corpus, load_corpus
from querypilot.retrieval.api import KnowledgeRetrievalError, load_scope_chunks


def _import(settings, factory, name: str):
    """导入一份原创 Corpus（库名随机），返回 (Corpus, Knowledge Scope)。"""
    database_id = "scopechunks" + uuid4().hex
    path = factory(name, database_id=database_id)
    source = load_corpus(path)
    scope = KnowledgeScope(corpus_id=source.manifest.corpus_id, database_id=database_id,
                           schema_version=source.manifest.identity_descriptor.schema_versions[database_id])
    import_corpus(path, settings)
    return source, scope


def test_reads_every_chunk_of_the_scope_and_nothing_else_in_chunk_key_order(pg_search_database, r3_corpus_factory):
    """输入：专用测试库里有两份不同库的 Corpus，读其中一份的 Scope。

    输出：恰好是这份 Corpus 的全部 Chunk（另一份的一条都没有），按 chunk_key 升序排列，
    rank 从 1 连续编号，score 都是 0；正文、定位字段与 Corpus 文件里的一致。
    """
    source, scope = _import(pg_search_database, r3_corpus_factory, "first")
    other, _ = _import(pg_search_database, r3_corpus_factory, "second")

    hits = load_scope_chunks(scope, pg_search_database)

    expected = sorted(source.chunks, key=lambda chunk: chunk.chunk_key)
    assert [hit.chunk_id for hit in hits] == [str(chunk.chunk_id) for chunk in expected]
    assert [hit.rank for hit in hits] == list(range(1, len(expected) + 1))
    assert {hit.score for hit in hits} == {0.0}
    assert [(hit.knowledge_type, hit.table_name, hit.column_name, hit.content) for hit in hits] == [
        (chunk.knowledge_type, chunk.table_name, chunk.column_name, chunk.content) for chunk in expected]
    assert not {hit.chunk_id for hit in hits} & {str(chunk.chunk_id) for chunk in other.chunks}
    assert {hit.knowledge_type for hit in hits} == {"table", "column", "relationship"}


def test_unknown_scope_is_a_retrieval_error(pg_search_database, r3_corpus_factory):
    """输入：corpus_id 不存在的 Scope。输出：KnowledgeRetrievalError，类别沿用读取层的 scope_not_found。"""
    _, scope = _import(pg_search_database, r3_corpus_factory, "only")

    with pytest.raises(KnowledgeRetrievalError) as caught:
        load_scope_chunks(KnowledgeScope(**{**scope.model_dump(), "corpus_id": "0" * 64}), pg_search_database)

    assert caught.value.error_type == "scope_not_found"
