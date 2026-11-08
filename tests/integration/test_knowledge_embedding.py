"""embed_corpus 在测试 PG 上的行为：全部写入、失败不写、已存在不调用 embedder。"""

import pytest

from _embedding_fakes import FakeEmbedder


def _scope(source):
    from querypilot.knowledge.api import KnowledgeScope
    manifest = source.manifest
    return KnowledgeScope(corpus_id=manifest.corpus_id, database_id=manifest.identity_descriptor.database_scope[0],
                          schema_version=next(iter(manifest.identity_descriptor.schema_versions.values())))


def _stored_vectors(settings, corpus_id):
    from querypilot.knowledge.knowledge_store import _postgres as pg
    with pg.connection(settings, "read") as conn:
        return conn.execute(
            "SELECT e.chunk_id::text AS chunk_id, e.embedding_profile_id AS profile, e.embedding_dimensions AS dims "
            "FROM querypilot_kb.chunk_embeddings e JOIN querypilot_kb.knowledge_chunks c USING (chunk_id) "
            "WHERE c.corpus_id=%s ORDER BY c.chunk_key", (corpus_id,)).fetchall()


@pytest.fixture
def imported(pg_database, r2_corpus):
    """已导入测试库的原创小 Corpus。"""
    from querypilot.knowledge.api import import_corpus, load_corpus
    import_corpus(r2_corpus, pg_database)
    return load_corpus(r2_corpus)


def test_embed_corpus_writes_one_vector_per_chunk(pg_database, imported):
    """输入：已导入的 Corpus 和假 embedder；输出：每个 Chunk 一条 3 维向量，回执不含正文和向量。"""
    from querypilot.knowledge.api import embed_corpus
    result = embed_corpus(_scope(imported), pg_database, FakeEmbedder())
    stored = _stored_vectors(pg_database, imported.manifest.corpus_id)
    assert sorted(row["chunk_id"] for row in stored) == sorted(str(chunk.chunk_id) for chunk in imported.chunks)
    assert {(row["profile"], row["dims"]) for row in stored} == {("fake:3:test-v1", 3)}
    assert (result.outcome, result.embedding_profile_id, result.dimensions, result.embedded_chunks) == (
        "embedded", "fake:3:test-v1", 3, len(imported.chunks))
    assert "content" not in result.model_dump_json() and "[1.0" not in result.model_dump_json()


class FailingEmbedder(FakeEmbedder):
    """一调用就失败，模拟 API 中途出错。"""

    def embed_documents(self, texts):
        self.document_calls += 1
        raise RuntimeError("upstream failed")


class ShortEmbedder(FakeEmbedder):
    """少返回一条向量，模拟服务端返回条数不对。"""

    def embed_documents(self, texts):
        return super().embed_documents(texts)[:-1]


def test_failed_embedding_writes_nothing(pg_database, imported):
    """输入：embedder 抛错；输出：异常原样抛出，向量表里没有这份 Corpus 的行。"""
    from querypilot.knowledge.api import embed_corpus
    with pytest.raises(RuntimeError):
        embed_corpus(_scope(imported), pg_database, FailingEmbedder())
    assert _stored_vectors(pg_database, imported.manifest.corpus_id) == []


def test_wrong_vector_count_is_embedding_error(pg_database, imported):
    """输入：返回的向量少一条；输出：embedding_error，一行不写。"""
    from querypilot.knowledge.api import EmbeddingError, embed_corpus
    with pytest.raises(EmbeddingError) as caught:
        embed_corpus(_scope(imported), pg_database, ShortEmbedder())
    assert caught.value.error_type == "embedding_error"
    assert _stored_vectors(pg_database, imported.manifest.corpus_id) == []


def test_second_run_reports_exists_without_calling_embedder(pg_database, imported):
    """输入：同一 Scope、同一 Profile 执行两次；输出：第二次为 exists，不调用 embedder，行数不变。"""
    from querypilot.knowledge.api import embed_corpus
    embed_corpus(_scope(imported), pg_database, FakeEmbedder())
    again = FakeEmbedder()
    result = embed_corpus(_scope(imported), pg_database, again)
    assert again.document_calls == 0
    assert (result.outcome, result.dimensions, result.embedded_chunks) == ("exists", 3, len(imported.chunks))
    assert len(_stored_vectors(pg_database, imported.manifest.corpus_id)) == len(imported.chunks)


class RacingEmbedder(FakeEmbedder):
    """在 API 调用期间，让"另一个进程"先把同一 Profile 的向量全部写完。"""

    def __init__(self, settings, scope):
        super().__init__()
        self.settings, self.scope = settings, scope

    def embed_documents(self, texts):
        from querypilot.knowledge.knowledge_store._embedding import embed_scope
        embed_scope(self.scope, self.settings, FakeEmbedder())
        return super().embed_documents(texts)


def test_vectors_written_meanwhile_report_exists(pg_database, imported):
    """输入：读完 Chunk 后、写入前，同一 Profile 的向量已被写入；输出：exists，不报错，行数不变。"""
    from querypilot.knowledge.api import embed_corpus
    scope = _scope(imported)
    result = embed_corpus(scope, pg_database, RacingEmbedder(pg_database, scope))
    assert result.outcome == "exists"
    assert len(_stored_vectors(pg_database, imported.manifest.corpus_id)) == len(imported.chunks)


def test_corpus_still_verifies_after_embedding(pg_database, r2_corpus, imported):
    """输入：已生成向量的 Corpus；输出：store verify 仍为 verified，Corpus 原有的行没被改动。"""
    from querypilot.knowledge.api import embed_corpus, verify_import
    before = verify_import(r2_corpus, pg_database)
    embed_corpus(_scope(imported), pg_database, FakeEmbedder())
    after = verify_import(r2_corpus, pg_database)
    assert after.outcome == "verified"
    assert (after.projection_digest, after.timestamps_digest) == (before.projection_digest, before.timestamps_digest)


def test_unknown_scope_is_rejected_before_embedding(pg_database, imported):
    """输入：不存在的 corpus_id；输出：scope_not_found，不调用 embedder，不写入。"""
    from querypilot.knowledge.api import KnowledgeReadError, embed_corpus
    scope = _scope(imported).model_copy(update={"corpus_id": "0" * 64})
    embedder = FakeEmbedder()
    with pytest.raises(KnowledgeReadError) as caught:
        embed_corpus(scope, pg_database, embedder)
    assert caught.value.error_type == "scope_not_found"
    assert embedder.document_calls == 0
