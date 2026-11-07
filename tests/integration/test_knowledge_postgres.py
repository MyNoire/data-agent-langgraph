"""实库证明事务、作用域、并发与权限；没有 PostgreSQL 不能声称通过。"""

import json
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4, uuid5, NAMESPACE_URL

import pytest

from querypilot.knowledge.knowledge_store.api import KnowledgeStorageError


def _api():
    """输入：无；输出：延迟加载的 PG/导入 API；缺驱动由门禁 fixture 控制。"""
    from querypilot.knowledge.knowledge_store import _postgres as postgres, _importer as importer
    from querypilot.knowledge.knowledge_corpus.api import load_corpus as load_verified_corpus
    return postgres, importer, load_verified_corpus


def test_public_path_import_reuse_and_verification(pg_database, r2_corpus):
    """输入：原创实际路径；返回：公开导入/复用/复验摘要一致；事务仅写专用测试库。"""
    from querypilot.knowledge.api import import_corpus, verify_import
    first = import_corpus(r2_corpus, pg_database)
    again = import_corpus(r2_corpus, pg_database)
    checked = verify_import(r2_corpus, pg_database)
    assert (first.outcome, again.outcome, checked.outcome) == ("imported", "reused", "verified")
    assert first.projection_digest == again.projection_digest == checked.projection_digest
    assert first.timestamps_digest == again.timestamps_digest == checked.timestamps_digest


def test_committed_import_survives_receipt_publish_failure(pg_database, r2_corpus, monkeypatch):
    """输入：原创源和单点结果写故障；返回：imported事实保留、实库复验仍成功；不删提交数据。"""
    from querypilot.knowledge.api import import_corpus, verify_import
    from querypilot.runtime.api import RunArtifactError
    from querypilot.runtime import _artifacts
    original = _artifacts._write_json

    def fail_result(path, value, **kwargs):
        """只让本次结果发布失败；初始化、事件、失败记录仍可写。"""
        if path.name == "result.json":
            raise OSError("private-receipt-failure")
        return original(path, value, **kwargs)

    with monkeypatch.context() as isolated:
        isolated.setattr(_artifacts, "_write_json", fail_result)
        with pytest.raises(RunArtifactError) as failed:
            import_corpus(r2_corpus, pg_database)
    assert failed.value.operation_outcome == "imported"
    assert failed.value.artifact_outcome == "failed"
    verified = verify_import(r2_corpus, pg_database)
    assert verified.outcome == "verified"
    assert verified.counts.documents == 2


def test_all_migrations_are_applied_and_reused(pg_database):
    """输入：专用库；输出：迁移补齐到 002，再次迁移复用；只读核对版本。"""
    pg, _, _ = _api()
    assert pg.migrate(pg_database).outcome == "reused"
    assert pg.check(pg_database).migration_version == "002"


def test_database_at_001_upgrades_to_002(pg_database, r2_corpus):
    """输入：已导入 Corpus、降回 001 结构的专用库；输出：migrate 补跑 002 后核对通过，Corpus 仍能复验。"""
    pg, api, load = _api(); source = load(r2_corpus)
    imported = api.import_verified_corpus(source, pg_database)
    with pg.connection(pg_database, "admin") as conn, conn.transaction():
        conn.execute("DROP TABLE querypilot_kb.chunk_embeddings")
        conn.execute("ALTER TABLE querypilot_kb.knowledge_chunks ADD COLUMN embedding public.vector, "
                     "ADD COLUMN embedding_profile_id TEXT, ADD COLUMN embedding_dimensions INTEGER")
        conn.execute("""ALTER TABLE querypilot_kb.knowledge_chunks ADD CONSTRAINT chunks_embedding CHECK (
            (embedding IS NULL AND embedding_profile_id IS NULL AND embedding_dimensions IS NULL)
            OR (embedding IS NOT NULL AND embedding_profile_id IS NOT NULL AND length(embedding_profile_id) > 0
             AND embedding_dimensions IS NOT NULL AND embedding_dimensions > 0
             AND public.vector_dims(embedding) = embedding_dimensions AND public.vector_norm(embedding) > 0))""")
        conn.execute("DELETE FROM querypilot_kb.schema_migrations WHERE version='002'")
    assert pg.bootstrap(pg_database).outcome == "reused"
    assert pg.migrate(pg_database).outcome == "applied"
    assert pg.check(pg_database).migration_version == "002"
    verified = api.verify_corpus_import(source, pg_database)
    assert verified.projection_digest == imported.projection_digest


@pytest.mark.parametrize("statement", [
    "UPDATE querypilot_kb.schema_migrations SET checksum=repeat('0',64) WHERE version='001'",
    "INSERT INTO querypilot_kb.schema_migrations(version,checksum) VALUES ('999',repeat('0',64))",
])
def test_migration_records_must_match_files(pg_database, statement):
    """输入：迁移记录被改或多出陌生版本；输出：migrate 与 check 都报 migration_conflict。"""
    pg, _, _ = _api()
    with pg.connection(pg_database, "admin") as conn, conn.transaction():
        conn.execute(statement)
    try:
        for command in (pg.migrate, pg.check):
            with pytest.raises(KnowledgeStorageError) as caught:
                command(pg_database)
            assert caught.value.error_type == "migration_conflict"
    finally:
        with pg.connection(pg_database, "admin") as conn, conn.transaction():
            conn.execute("DELETE FROM querypilot_kb.schema_migrations")
            for record in pg.migration_records():
                conn.execute("INSERT INTO querypilot_kb.schema_migrations(version,checksum) VALUES (%(version)s,%(checksum)s)", record)


def test_migration_reuse_keeps_records(pg_database):
    """输入：已迁移到最新的专用库；输出：再次 migrate 返回 reused，迁移记录和执行时间不变。"""
    pg, _, _ = _api()
    with pg.connection(pg_database, "admin") as conn:
        before = conn.execute("SELECT * FROM querypilot_kb.schema_migrations").fetchall()
    assert pg.migrate(pg_database).outcome == "reused"
    with pg.connection(pg_database, "admin") as conn:
        assert conn.execute("SELECT * FROM querypilot_kb.schema_migrations").fetchall() == before


def test_import_reuse_verification_and_composite_order(pg_database, r2_corpus):
    """输入：原创正式源；输出：首导/复用/只读复验指纹相同且复合配对有序。"""
    pg, api, load = _api()
    source = load(r2_corpus)
    first = api.import_verified_corpus(source, pg_database)
    repeat = api.import_verified_corpus(source, pg_database)
    checked = api.verify_corpus_import(source, pg_database)
    assert (first.outcome, repeat.outcome, checked.outcome) == ("imported", "reused", "verified")
    assert first.projection_digest == repeat.projection_digest == checked.projection_digest
    assert first.timestamps_digest == repeat.timestamps_digest == checked.timestamps_digest
    with pg.connection(pg_database, "read") as conn:
        _, groups = pg.read_corpus(conn, source.manifest.corpus_id)
    assert groups["relations"][0]["source_columns"] == ["tenant_ref", "item_ref"]
    assert groups["relations"][0]["target_columns"] == ["tenant_id", "item_id"]


@pytest.mark.parametrize("kind", ["content", "missing", "extra", "registration"])
def test_polluted_corpus_is_not_repaired(pg_database, r2_corpus, kind):
    """输入：测试 admin 污染；输出：重复导入报冲突，污染现场与时间不再变化。"""
    pg, api, load = _api(); source = load(r2_corpus)
    api.import_verified_corpus(source, pg_database)
    with pg.connection(pg_database, "admin") as conn:
        if kind == "content": conn.execute("UPDATE querypilot_kb.knowledge_chunks SET content=content||' changed' WHERE chunk_id=%s", (source.chunks[0].chunk_id,))
        elif kind == "missing": conn.execute("DELETE FROM querypilot_kb.knowledge_chunks WHERE chunk_id=%s", (source.chunks[0].chunk_id,))
        elif kind == "extra":
            row = source.chunks[0].model_dump(mode="json")
            row.update(chunk_id=str(uuid5(NAMESPACE_URL, str(uuid4()))), chunk_key="extra")
            pg._insert(conn, "knowledge_chunks", row)
        else: conn.execute("UPDATE querypilot_kb.knowledge_corpora SET counts='{}'::jsonb WHERE corpus_id=%s", (source.manifest.corpus_id,))
        before = pg.read_corpus(conn, source.manifest.corpus_id)
    with pytest.raises(KnowledgeStorageError) as caught: api.import_verified_corpus(source, pg_database)
    assert caught.value.error_type == "target_conflict"
    with pg.connection(pg_database, "admin") as conn: assert pg.read_corpus(conn, source.manifest.corpus_id) == before


def test_two_legitimate_corpus_versions_coexist(pg_database, r2_corpus):
    """输入：同库两个 R1.4 正式构建版本；输出：独立落库、旧版内容/首次时间不变。"""
    from querypilot.knowledge.knowledge_corpus._corpus import build_knowledge_corpus
    pg, api, load = _api()
    original = load(r2_corpus)
    first = api.import_verified_corpus(original, pg_database)
    other_dir = r2_corpus.parent / "second-version"
    build_knowledge_corpus(r2_corpus.parent / "curated", r2_corpus.parent / "config/semantics.json", other_dir, "demo", profile_version="second-version")
    other = load(other_dir)
    assert other.manifest.corpus_id != original.manifest.corpus_id
    assert api.import_verified_corpus(other, pg_database).outcome == "imported"
    assert api.verify_corpus_import(other, pg_database).outcome == "verified"
    checked = api.verify_corpus_import(original, pg_database)
    assert (checked.projection_digest, checked.timestamps_digest) == (first.projection_digest, first.timestamps_digest)


def test_relationship_chunk_requires_its_relation_source_document(pg_database, r2_corpus):
    """输入：关系 chunk 指向合法但错误的父文档；输出：关系源父文档复合 FK 拒绝。"""
    pg, api, load = _api(); source = load(r2_corpus)
    api.import_verified_corpus(source, pg_database)
    row = next(c for c in source.chunks if c.knowledge_type == "relationship").model_dump(mode="json")
    wrong = next(d for d in source.documents if d.document_id != row["document_id"])
    row.update(chunk_id=str(uuid5(NAMESPACE_URL, str(uuid4()))), chunk_key="wrong-source", document_id=wrong.document_id, document_key=wrong.document_key)
    import psycopg
    with pg.connection(pg_database, "admin") as conn:
        with pytest.raises(psycopg.IntegrityError) as caught:
            with conn.transaction(): pg._insert(conn, "knowledge_chunks", row)
        assert caught.value.sqlstate == "23503"


def test_column_privilege_bypass_is_detected(pg_database):
    """输入：测试 admin 临时授权单列 UPDATE；输出：权限检查拒绝，事务回滚授权。"""
    pg, _, _ = _api()
    with pg.connection(pg_database, "admin") as conn:
        with pytest.raises(RuntimeError, match="restore"):
            with conn.transaction():
                conn.execute("GRANT UPDATE(status) ON querypilot_kb.knowledge_corpora TO querypilot_kb_reader")
                with pytest.raises(KnowledgeStorageError) as caught: pg.check_permissions(conn)
                assert caught.value.error_type == "migration_conflict"
                raise RuntimeError("restore")


def test_insert_failure_rolls_back_new_corpus_and_preserves_old(pg_database, r2_corpus, monkeypatch):
    """输入：旧已存 corpus/受控中途 INSERT 失败；输出：旧版本保留，新版本零残留。"""
    pg, api, load = _api(); old = load(r2_corpus)
    first = api.import_verified_corpus(old, pg_database)
    from querypilot.knowledge.knowledge_corpus._corpus import build_knowledge_corpus
    other_dir = r2_corpus.parent / "other"
    build_knowledge_corpus(r2_corpus.parent / "curated", r2_corpus.parent / "config/semantics.json", other_dir, "demo", profile_version="different")
    other = load(other_dir); real = pg._insert

    def fail(conn, table, row):
        """输入：INSERT 参数；输出：到 chunk 阶段主动失败；仅影响当前测试事务。"""
        if table == "knowledge_chunks":
            conn.execute("SELECT 1/0")
        return real(conn, table, row)
    monkeypatch.setattr(pg, "_insert", fail)
    with pytest.raises(KnowledgeStorageError): api.import_verified_corpus(other, pg_database)
    with pg.connection(pg_database, "admin") as conn:
        record, rows = pg.read_corpus(conn, other.manifest.corpus_id)
        assert record is None and not any(rows.values())
    assert api.verify_corpus_import(old, pg_database).projection_digest == first.projection_digest


@pytest.mark.parametrize("field,value", [("database_id", "other"), ("schema_version", "a"*64), ("corpus_id", "b"*64), ("document_key", "wrong"), ("document_id", None)])
def test_cross_scope_or_wrong_parent_is_rejected(pg_database, r2_corpus, field, value):
    """输入：非法父引用；输出：PG 约束拒绝；只有隔离测试库的 admin 尝试写入。"""
    pg, api, load = _api(); source = load(r2_corpus)
    api.import_verified_corpus(source, pg_database)
    row = source.chunks[0].model_dump(mode="json")
    row.update(chunk_id=str(uuid5(NAMESPACE_URL, str(uuid4()))), chunk_key="new")
    row[field] = value or source.documents[-1].document_id
    import psycopg
    with pg.connection(pg_database, "admin") as conn:
        with pytest.raises(psycopg.IntegrityError) as caught:
            with conn.transaction(): pg._insert(conn, "knowledge_chunks", row)
        assert caught.value.sqlstate == "23503"


@pytest.mark.parametrize("chunk,dimensions,vector,sqlstate", [
    ("existing", 3, "[1,0,0]", None),
    ("unknown", 3, "[1,0,0]", "23503"),
    ("existing", 3, "[0,0,0]", "23514"),
    ("existing", 2, "[1,0,0]", "23514"),
])
def test_importer_appends_vectors_only_for_known_chunks(pg_database, r2_corpus, chunk, dimensions, vector, sqlstate):
    """输入：importer 写一条向量；输出：已存在的 Chunk、非零向量、维度一致才写入，否则约束拒绝。"""
    pg, api, load = _api(); source = load(r2_corpus)
    api.import_verified_corpus(source, pg_database)
    chunk_id = source.chunks[0].chunk_id if chunk == "existing" else str(uuid5(NAMESPACE_URL, str(uuid4())))
    statement = ("INSERT INTO querypilot_kb.chunk_embeddings(chunk_id,embedding_profile_id,embedding_dimensions,embedding) "
                 "VALUES (%s,'fake:3:test-v1',%s,%s::public.vector)")
    import psycopg
    with pg.connection(pg_database, "import") as conn:
        if sqlstate is None:
            with conn.transaction(): conn.execute(statement, (chunk_id, dimensions, vector))
            assert conn.execute("SELECT count(*) AS n FROM querypilot_kb.chunk_embeddings").fetchone()["n"] == 1
        else:
            with pytest.raises(psycopg.IntegrityError) as caught:
                with conn.transaction(): conn.execute(statement, (chunk_id, dimensions, vector))
            assert caught.value.sqlstate == sqlstate


@pytest.mark.parametrize("role", ["read", "import"])
def test_runtime_roles_cannot_create_temp_or_persistent_objects(pg_database, role):
    """输入：两运行角色；输出：READ WRITE 下临时/持久建表均报 42501；无残留表。"""
    pg, _, _ = _api(); import psycopg
    with pg.connection(pg_database, role) as conn:
        privileges = conn.execute("SELECT has_database_privilege(current_user,current_database(),'TEMP') AS t, has_database_privilege(current_user,current_database(),'CREATE') AS c").fetchone()
        assert not privileges["t"] and not privileges["c"]
        for statement in ("CREATE TEMP TABLE forbidden(x int)", "CREATE TABLE querypilot_kb.forbidden(x int)"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as caught:
                with conn.transaction():
                    conn.execute("SET TRANSACTION READ WRITE")
                    conn.execute(statement)
            assert caught.value.sqlstate == "42501"


@pytest.mark.parametrize("role,statement", [
    ("read", "INSERT INTO querypilot_kb.knowledge_corpora SELECT * FROM querypilot_kb.knowledge_corpora"),
    ("read", "UPDATE querypilot_kb.knowledge_corpora SET status=status"),
    ("read", "DELETE FROM querypilot_kb.knowledge_corpora"),
    ("read", "ALTER TABLE querypilot_kb.knowledge_corpora ADD COLUMN forbidden int"),
    ("import", "UPDATE querypilot_kb.knowledge_corpora SET status=status"),
    ("import", "DELETE FROM querypilot_kb.knowledge_corpora"),
    ("import", "TRUNCATE querypilot_kb.knowledge_chunks"),
    ("read", "INSERT INTO querypilot_kb.chunk_embeddings SELECT * FROM querypilot_kb.chunk_embeddings"),
    ("import", "UPDATE querypilot_kb.chunk_embeddings SET embedding_profile_id=embedding_profile_id"),
    ("import", "DELETE FROM querypilot_kb.chunk_embeddings"),
])
def test_roles_cannot_modify_existing_knowledge(pg_database, role, statement):
    """输入：越界写入/DDL；输出：实际权限拒绝，不借默认只读掩盖授权错误。"""
    pg, _, _ = _api(); import psycopg
    with pg.connection(pg_database, role) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute("SET TRANSACTION READ WRITE")
                conn.execute(statement)


def test_same_corpus_two_processes_create_one_batch(pg_database, r2_corpus):
    """输入：两个 CLI 子进程/同源；输出：观察两者等待同锁后释放，只有一批落库。"""
    pg, api, load = _api()
    import os
    from psycopg.conninfo import make_conninfo
    source = load(r2_corpus)
    application = "r2-concurrency-" + uuid4().hex
    env = {**os.environ, **{"QUERYPILOT_KNOWLEDGE_"+role.upper()+"_DSN": getattr(pg_database, role+"_dsn").get_secret_value() for role in ("admin", "import", "read")}}
    env["QUERYPILOT_KNOWLEDGE_IMPORT_DSN"] = make_conninfo(env["QUERYPILOT_KNOWLEDGE_IMPORT_DSN"], application_name=application)
    # 保证两者都进入锁等待；专用协调场景给予有限但足够的等待预算。
    env["QUERYPILOT_KNOWLEDGE_LOCK_TIMEOUT_MS"] = "30000"
    args = [sys.executable, "-m", "querypilot", "knowledge", "store", "import",
            "--corpus-dir", str(r2_corpus)]
    processes = []
    outcomes = []
    try:
        with pg.connection(pg_database, "admin") as holder, holder.transaction(), pg.connection(pg_database, "admin") as observer:
            key = int.from_bytes(bytes.fromhex(source.manifest.corpus_id)[:8], "big", signed=True)
            holder.execute("SELECT pg_advisory_xact_lock(%s)", (key,))
            for _ in range(2):
                processes.append(subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env))
            deadline = time.monotonic() + 20
            while True:
                # observer 为 autocommit，避免持锁长事务复用旧统计语料。
                waiting = observer.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE datname=current_database() AND application_name=%s AND wait_event_type='Lock' AND wait_event='advisory'", (application,)).fetchone()["n"]
                if waiting == 2:
                    break
                assert all(p.poll() is None for p in processes), "子进程提前退出，未形成并发锁等待"
                assert time.monotonic() < deadline, "未在有限时间内观察到两个导入等待同锁"
                time.sleep(0.05)  # 轮询实际锁状态，不用偶然延迟决定执行先后。
        for process in processes:
            output, _ = process.communicate(timeout=45)
            assert process.returncode == 0
            outcomes.append(json.loads(output)["outcome"])
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill(); process.communicate()
    assert sorted(outcomes) == ["imported", "reused"]
    assert api.verify_corpus_import(load(r2_corpus), pg_database).counts.documents == 2


def test_lock_wait_is_bounded(pg_database, r2_corpus):
    """输入：持有同 corpus 事务锁；输出：另一个导入在有限等待后报 lock_timeout，无写入。"""
    pg, api, load = _api(); source = load(r2_corpus)
    limited = pg_database.model_copy(update={"lock_timeout_ms": 100})
    with pg.connection(pg_database, "admin") as holder, holder.transaction():
        key = int.from_bytes(bytes.fromhex(source.manifest.corpus_id)[:8], "big", signed=True)
        holder.execute("SELECT pg_advisory_xact_lock(%s)", (key,))
        with pytest.raises(KnowledgeStorageError) as caught: api.import_verified_corpus(source, limited)
        assert caught.value.error_type == "lock_timeout"
    with pg.connection(pg_database, "admin") as conn:
        record, rows = pg.read_corpus(conn, source.manifest.corpus_id)
        assert record is None and not any(rows.values())
